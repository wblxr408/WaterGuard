import { useEffect, useMemo, useRef, useState } from 'react';
import type { RunEvent, RunRecord } from '@waterguard/contracts';
import { executionFrames, executionGraph, EXECUTION_LAYOUT as L } from './agent-execution';
import type { ExecutionFrame, ExecutionNode, ExecutionTrace } from './agent-execution';
import { conclusionText, runStatusText } from './data';
import Icon from './Icon';
import { usePanZoom, ZoomBar } from './workflow-panzoom';
import { branchGraph, actionText } from './agent-branches';
import './execution-flow.css';

const clock = (iso: string) => new Date(iso).toLocaleTimeString('zh-CN', { hour12: false });
const icons = { start: 'play', end: 'check', tool: 'cube', llm: 'agent', gateway: 'shield', jev: 'compare', candidate: 'layers', condition: 'shield' };
const states = { running: '调用中', ok: '完成', fail: '调用失败', interrupted: '已中断', idle: '未评估', wait: '待验证' };

export default function ExecutionFlow({ trace, events, run, focus, inspect }: {
  trace: ExecutionTrace | null; events: RunEvent[]; run: RunRecord; focus: string | null;
  inspect: (title: string, data: unknown) => void;
}) {
  const frames = useMemo(() => executionFrames(trace, events), [trace, events]);
  const [step, setStep] = useState<number | null>(null);
  const [recording, setRecording] = useState<ExecutionFrame[]>([]);
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [details, setDetails] = useState<'node' | 'trace' | null>(null);
  const [follow, setFollow] = useState(true);
  const [mode, setMode] = useState<'branches' | 'calls'>('branches');
  const [canvasWidth, setCanvasWidth] = useState(800);
  const [columns, setColumns] = useState(window.innerWidth < 1100 ? 3 : 4);
  const timer = useRef(0);
  const shown = step === null ? frames : recording.slice(0, step);
  const actual = useMemo(() => executionGraph(shown, run, columns), [shown, run, columns]);
  const graph = useMemo(() => mode === 'branches' ? branchGraph(shown, run) : actual, [mode, shown, run, actual]);
  const pz = usePanZoom(graph.width, graph.height, follow);
  useEffect(() => {
    const element = pz.wrapRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => { setColumns(element.clientWidth < 1000 ? 3 : 4); setCanvasWidth(element.clientWidth); });
    observer.observe(element);
    return () => observer.disconnect();
  }, [pz.wrapRef]);
  const byId = new Map(graph.nodes.map(node => [node.id, node]));
  const node = selected ? byId.get(selected) : undefined;
  const calls = actual.nodes.filter(n => n.events.length);
  const resolveRef = (ref: string) => graph.nodes.find(n => n.id === ref || (ref === 'execution-start' && n.id === 'execution-preparation') || n.events.some(e => e.native_tool_call_ref === ref))?.id ?? ref;
  const live = step === null && ['QUEUED', 'RUNNING'].includes(run.status);
  useEffect(() => { if (focus) { setStep(null); setPlaying(false); setSelected(resolveRef(focus)); setDetails('node'); } }, [focus, mode]);
  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => setStep(n => {
      if ((n ?? 0) >= recording.length) { setPlaying(false); return null; }
      return (n ?? 0) + 1;
    }), 700);
    return () => clearInterval(timer.current);
  }, [playing, recording]);
  useEffect(() => {
    if (step === null || !shown.length) return;
    const last = shown.at(-1)!;
    setSelected(resolveRef(last.event?.native_tool_call_ref ?? last.id));
  }, [step, mode]);
  const open = (id: string) => { setSelected(resolveRef(id)); setDetails('node'); };
  const label = (n: ExecutionNode) => n.statusLabel ?? (n.kind === 'end' && n.state === 'fail' ? '出错终止' : n.kind === 'jev' && n.events.some(e => e.jev_configured === false) ? '未连接' : states[n.state]);
  const card = (n: ExecutionNode, canvas: boolean) => <button key={n.id}
    className={`${canvas ? 'wf-node ex-node' : 'ex-list-node'} ${n.kind} ${n.state} ${n.id === selected ? 'sel' : ''} ${n.events.some(e => e.jev_configured === false) ? 'unconfigured' : ''}`}
    style={canvas ? { left: n.x, top: n.y, width: L.w, height: n.height ?? L.h } : undefined}
    onClick={() => open(n.id)} aria-label={`${n.title}：${label(n)}`} data-call-ref={n.events.length ? n.id : undefined}>
    <span className="wf-icon"><Icon name={icons[n.kind]} size={17} /></span>
    <span className="wf-text"><b>{n.title}</b><small>{n.summary}</small></span>
    <span className="ex-node-foot"><time>{clock(n.startedAt)}</time><span className={`ex-state ${n.state}`}>{label(n)}</span></span>
    {n.branch && <span className="ex-branch">↳ {n.branch}</span>}
  </button>;
  return <section className="wf ex-workflow" aria-label="本次执行工作流">
    <header className="wf-bar">
      <div className="wf-title"><span className="wf-app"><Icon name="pulse" size={18} /></span><div>
        <b>本次执行</b><small>任务第 {run.task_ref.revision} 版 · <span className="mono">{run.run_id}</span></small>
      </div></div>
      <div className="wf-status">
        <span className={`wf-pill ${live || playing ? 'running' : run.status === 'ERROR' ? 'ex-error' : 'ex-finished'}`}><i />{playing ? '回放中' : step !== null ? '回放暂停' : runStatusText[run.status]}</span>
        <span className="wf-meta">调用 {calls.length} 次 · 换方案 {graph.switches} 次 · 重试 {graph.retries} 次</span>
        <button className="button secondary" onClick={() => setDetails('trace')}><Icon name="layers" size={14} />追踪</button>
        {step !== null ? <>
          <button className="button secondary" onClick={() => setPlaying(!playing)}><Icon name={playing ? 'pause' : 'play'} size={14} />{playing ? '暂停' : '继续'}</button>
          <button className="button primary" onClick={() => { setStep(null); setPlaying(false); }}>返回最新</button>
        </> : <button className="button primary" disabled={!frames.length} onClick={() => {
          setRecording(frames); setStep(0); setPlaying(true); setSelected(null);
        }}><Icon name="play" size={14} />回放调用</button>}
      </div>
    </header>
    <div className="ex-summary"><span>仿真 {run.budget_usage.simulations} 次 · 模型调用 {run.budget_usage.model_calls ?? '—'} 次</span>
      <div className="ex-view" role="group" aria-label="执行图视图">{(['branches', 'calls'] as const).map(view => <button key={view} className={mode === view ? 'on' : ''} aria-pressed={mode === view}
        onClick={() => { setMode(view); setDetails(null); setSelected(null); }}>{view === 'branches' ? '决策分支' : '调用顺序'}</button>)}</div>
      <span>验收结论：{conclusionText[run.conclusion] ?? run.conclusion}</span>
      <label><input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} />跟随展开</label>
    </div>
    {step !== null && <div className="ex-replay"><span>{step} / {recording.length} 条记录</span><input aria-label="调用回放进度" type="range" min={0} max={recording.length} value={step}
      onChange={e => { setPlaying(false); setStep(Number(e.target.value)); }} /></div>}
    <div className={`wf-body ${details ? 'open' : ''}`}>
      <div className={`wf-canvas ex-canvas ${mode === 'branches' ? 'ex-branched' : ''}`} ref={pz.wrapRef} {...pz.handlers}
        style={mode === 'branches' ? { height: Math.max(570, graph.height * Math.min(1, (canvasWidth - 24) / graph.width) + 24) } : undefined}>
        {!graph.nodes.length ? <p className="ex-empty">{step === 0 ? '回放即将开始' : '等待运行调用…'}</p> : <div className="wf-stage" style={{ width: graph.width, height: graph.height, transform: pz.transform }}>
          <svg className="wf-edges" width={graph.width} height={graph.height} aria-hidden="true"><defs><marker id="ex-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" /></marker></defs>
            {graph.edges.map((edge, i) => {
              const a = byId.get(edge.from)!, b = byId.get(edge.to)!;
              const flat = a.y === b.y;
              const x1 = flat ? a.x + L.w : a.x + L.w / 2, y1 = flat ? a.y + L.h / 2 : a.y + L.h;
              const x2 = flat ? b.x : b.x + L.w / 2, y2 = flat ? b.y + L.h / 2 : b.y;
              const y = y1 + (y2 - y1) / 2;
              const branch = edge.kind === 'retry' || edge.kind === 'candidate';
              const ay = a.y + (a.height ?? L.h) / 2, by = b.y + (b.height ?? L.h) / 2;
              const fan = edge.kind === 'offer' || (edge.kind === 'chosen' && b.kind === 'candidate');
              const path = branch ? `M${a.x + L.w / 2},${a.y + (a.height ?? L.h)} V${a.y + (a.height ?? L.h) + 28} H18 V${by} H${b.x}`
                : mode === 'branches' && fan ? `M${a.x + L.w / 2},${a.y + (a.height ?? L.h)} V${a.y + (a.height ?? L.h) + 22} H30 V${by} H${b.x}`
                : mode === 'branches' && b.x > a.x ? `M${a.x + L.w},${ay} C${a.x + L.w + 32},${ay} ${b.x - 32},${by} ${b.x},${by}`
                : mode === 'branches' && b.y > a.y && b.x < a.x ? `M${a.x + L.w / 2},${a.y + (a.height ?? L.h)} V${b.y - 24} H${b.x + L.w / 2} V${b.y}`
                : mode === 'branches' && a.x === b.x ? `M${a.x + L.w / 2},${a.y + (a.height ?? L.h)} V${b.y}`
                : flat ? `M${x1},${y1} C${x1 + 30},${y1} ${x2 - 30},${y2} ${x2},${y2}` : `M${x1},${y1} C${x1},${y} ${x2},${y} ${x2},${y2}`;
              return <g key={`${edge.from}:${edge.to}:${i}`} className={`wf-edge on ex-edge ${edge.kind} ${b.state === 'running' ? 'live' : ''}`}><path
                d={path} markerEnd="url(#ex-arrow)" />{edge.label && <text x={branch || fan ? 30 : b.x - 8} y={branch || fan ? by - 10 : by - 12} textAnchor={branch || fan ? 'start' : 'end'}>{edge.label}</text>}</g>;
            })}
          </svg>{graph.nodes.map(n => card(n, true))}
        </div>}
        <ZoomBar pz={pz} /><div className="wf-legend ex-legend"><span>{mode === 'branches' ? '候选 → 选择 → 验收结果' : '连线：调用顺序'}</span><span className="ex-branch-key">↳ 换方案 / 重试</span><span className="ex-jev-key">Jev 决策</span></div>
      </div>
      <ol className={`ex-outline ${mode === 'branches' ? 'branched' : ''}`} aria-label="本次实际调用">
        {graph.nodes.map(n => <li key={n.id}>{card(n, false)}</li>)}
        {!graph.nodes.length && <li className="ex-empty">等待运行调用…</li>}
      </ol>
      {details && <aside className="wf-panel ex-panel" aria-label="实际调用详情">
        <div className="wf-panel-tabs"><button className={details === 'node' ? 'on' : ''} onClick={() => setDetails('node')} disabled={!node}>调用详情</button>
          <button className={details === 'trace' ? 'on' : ''} onClick={() => setDetails('trace')}>追踪 · {shown.length}</button>
          <button className="wf-close" aria-label="收起调用详情" onClick={() => setDetails(null)}><Icon name="close" size={15} /></button></div>
        {details === 'node' && node ? <div className="ex-detail"><h3>{node.title}</h3><p>{node.summary}</p>
          <dl><dt>执行者 / 工具</dt><dd>{node.tool}</dd><dt>开始时间</dt><dd>{clock(node.startedAt)}</dd>
            <dt>执行状态</dt><dd>{label(node)}</dd>{node.endedAt && <><dt>返回时间 / 耗时</dt><dd>{clock(node.endedAt)} · {Math.max(0, (Date.parse(node.endedAt) - Date.parse(node.startedAt)) / 1000).toFixed(2)} 秒</dd></>}
            {node.candidateId && <><dt>候选方案</dt><dd>{node.candidateId}</dd></>}
          </dl>{node.candidatePlan && <div className="ex-plan-detail"><b>方案操作</b><p>{actionText(node.candidatePlan)}</p></div>}
          {node.assessment && <dl><dt>供需比 / 缺水量</dt><dd>{node.assessment.metrics.supply_ratio?.toFixed(3) ?? '—'} / {node.assessment.metrics.shortage_m3?.toFixed(1) ?? '—'} m³</dd></dl>}
          {!!node.events.length && <div className="ex-native-calls"><b>实际工具调用</b>{[...new Set(node.events.map(e => e.native_tool_call_ref))].map(ref => {
            const rows = node.events.filter(e => e.native_tool_call_ref === ref), first = rows[0], returned = rows.find(e => e.type === 'tool.completed');
            return <div key={ref}><strong>{toolsTitle(first.tool_name)}</strong><small>{clock(first.occurred_at)} · {returned ? returned.failed ? '失败' : '已返回' : '未返回'}</small><code>{ref}</code></div>;
          })}</div>}
          <button className="ag-raw" onClick={() => inspect(node.title, node)}><Icon name="file" size={13} />查看调用记录</button>
        </div> : <ol className="wf-trace">{shown.map(frame => <li key={frame.id}><button onClick={() => open(frame.event?.native_tool_call_ref ?? frame.id)}>
          <span className="what"><b>{frame.event ? `${toolsTitle(frame.event.tool_name)} · ${frame.event.type === 'tool.started' ? '开始' : frame.event.failed ? '失败' : '返回'}` : frame.type === 'start' ? '运行建立' : frame.type === 'end' ? '执行结束' : '证据归档'}</b>
            <small>{frame.event?.candidate_id ?? frame.event?.tool_name ?? frame.summary}</small></span><time>{clock(frame.occurred_at)}</time>
        </button></li>)}{!shown.length && <li className="empty">等待调用记录…</li>}</ol>}
      </aside>}
    </div>
  </section>;
}

function toolsTitle(name: string) {
  return ({ wg_get_task: '读取任务', wg_inspect_network: '分析管网', wg_generate_candidates: '规划候选', wg_jev_decide: 'Jev 决策',
    wg_submit_plan: '提交方案', wg_simulate_candidate: '水力仿真', wg_verify_candidate: '独立验收', wg_build_evidence: '读取证据' } as Record<string, string>)[name] ?? name;
}
