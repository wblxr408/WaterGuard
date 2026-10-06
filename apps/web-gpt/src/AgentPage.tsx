import {
  useEffect,
  useMemo,
  useState,
} from "react";
import type { RunEvent } from "@waterguard/contracts";
import {
  M,
  MSTAGE_H,
  MSTAGE_W,
  PLACED,
  TREE,
  mindNodeOf,
  mindState,
} from "./agent-mindmap";
import type { MindKind, MindNode, TagKind } from "./agent-mindmap";
import {
  EDGES,
  LAYOUT,
  NODES,
  STAGE_H,
  STAGE_W,
  edgeKey,
  flowState,
  nodeOfEvent,
  nodeXY,
} from "./agent-flow";
import type { NodeState } from "./agent-flow";
import { useAgentRun } from "./agent-stream";
import type { AgentCandidate as Candidate, AgentWorkspace as Workspace, StreamSource } from "./agent-stream";
import AgentLogs from "./AgentLogs";
import RunControls from "./RunControls";
import ExecutionFlow from "./ExecutionFlow";
import { usePanZoom, ZoomBar } from "./workflow-panzoom";
import JevPanel from './JevPanel';
import {
  actionText,
  conclusionText,
  engineStatus,
  eventText,
  gates,
  num,
  recommendationBlockerText,
  runStatusText,
  statusText,
  time,
} from "./data";
import Icon from "./Icon";
import "./agent-page.css";

/* 本次执行使用原生调用及返回生成节点；标准流程保留两种静态参考布局。
   SSE 与历史投影共用同一调用编号，不补造角色调用或未来分支。 */

/** 面板里展示的节点：两种看法的节点都满足 */
type PanelNode = {
  id: string;
  kind: MindKind;
  title: string;
  sub: string;
  agent?: boolean;
  tag?: MindNode["tag"];
  loop?: string;
};

type Inspect = (title: string, data: unknown) => void;

const originText: Record<string, string> = {
  agh: "智能体（AGH）",
  gateway: "网关",
  worker: "计算进程",
  verifier: "验收程序",
  user: "用户",
};
const sourceText: Record<string, string> = {
  agh: "智能体（AGH）",
  fixture: "人工样例",
  rule: "规则（工程预设）",
  enumeration: "枚举",
  graph: "图算法",
};
const modeText: Record<string, string> = {
  fixture: "人工样例",
  benchmark: "水力计算",
  replay: "回放",
  live: "实时",
};
const kindText: Record<MindKind, string> = {
  start: "开始",
  llm: "智能体角色",
  gateway: "网关",
  tool: "工具",
  condition: "条件",
  end: "结束",
  user: "等待用户",
};
const kindIcon: Record<MindKind, string> = {
  start: "play",
  llm: "agent",
  gateway: "shield",
  tool: "cube",
  condition: "compare",
  end: "check",
  user: "help",
};
/** 分支导图的大纲顺序（先序），窄屏竖排用 */
const MIND_OUTLINE: { n: MindNode; depth: number }[] = [];
{
  const walk = (n: MindNode, depth: number) => {
    MIND_OUTLINE.push({ n, depth });
    n.children?.forEach((c) => walk(c, depth + 1));
  };
  walk(TREE, 0);
}
const stateText: Record<NodeState, string> = {
  idle: "未执行",
  running: "进行中",
  ok: "完成",
  fail: "未通过",
  wait: "待完成",
  off: "待执行",
};
const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("zh-CN", { hour12: false });

/* ---------------------------------------------------------------- 页面 */
export default function AgentPage({ inspect, onRun }: { inspect: Inspect; onRun: (id: string) => void }) {
  const { w, events, logs, execution, state, error, source, runs, runId, selectSource, selectRun, refreshRuns, reconnect } = useAgentRun();
  useEffect(() => { if (source === 'runtime' && runId) onRun(runId); }, [source, runId, onRun]);
  const run = w?.run ?? null;
  const byId = useMemo(
    () => new Map((w?.candidates ?? []).map((c) => [c.id, c])),
    [w],
  );
  const [scope, setScope] = useState<"execution" | "standard">("execution");
  const [focus, setFocus] = useState<string | null>(null);
  const [view, setView] = useState<"mind" | "flow">("mind");
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState<"node" | "trace">("node");
  const [panelOpen, setPanelOpen] = useState(false);
  const shown: RunEvent[] = [];
  const flow = useMemo(() => flowState([], null, new Map(), false), []);
  const mind = useMemo(() => mindState([], null, new Map(), false), []);
  /** 一条事件在当前看法里落在哪个节点 */
  const nodeOf = (e: RunEvent) =>
    view === "mind" ? mindNodeOf(e) : nodeOfEvent[e.type];
  const openNode = (id: string) => {
    setSelected(id);
    setPanel("node");
    setPanelOpen(true);
  };
  const switchView = (v: "mind" | "flow") => {
    setView(v);
    setSelected(null);
    setPanelOpen(false);
  };

  useEffect(() => { setFocus(null); setSelected(null); setPanelOpen(false); }, [runId, source]);

  return (
    <div className="page-enter agent-page">
      <div className="section-intro">
        <h1>智能体</h1>
        <p>
          查看运行进度、工具调用、候选方案和验收结果。
        </p>
      </div>

      <section className="agent-stream-controls" aria-label="运行连接">
        <div className={`stream-indicator ${state}`} role="status"><i />{({ loading: "连接中", streaming: "已连接", reconnecting: "正在重连", done: "记录已同步", error: "连接异常", empty: "暂无运行" })[state]}</div>
        <label>数据来源<select aria-label="工作流数据来源" value={source} onChange={(e) => selectSource(e.target.value as StreamSource)}>
          <option value="runtime">任务运行服务</option><option value="fixture">人工样例回放</option>
        </select></label>
        {source === "runtime" && <label className="stream-run-select">运行<select aria-label="选择运行" value={runId} onChange={(e) => selectRun(e.target.value)}>
          {!runs.length && <option value={runId}>{runId || "等待运行记录"}</option>}
          {!!runs.length && !!runId && !runs.some((row) => row.run_id === runId) && <option value={runId}>指定运行 · {runId}</option>}
          {runs.map((row) => <option key={row.run_id} value={row.run_id}>{modeText[row.mode]} · {runStatusText[row.status]} · {row.run_id}</option>)}
        </select></label>}
        <button className="button secondary" onClick={refreshRuns}>刷新运行</button>
        <button className="button secondary" onClick={reconnect}>重新连接</button>
        {source === "runtime" && <RunControls run={run} created={(value) => { selectRun(value.run_id); refreshRuns(); }} updated={reconnect} />}
        {error && <p className="stream-error" role="alert">{error}</p>}
      </section>
      {source === 'runtime' && <JevPanel runId={runId || undefined} />}
      <div className="wf-scope" role="tablist" aria-label="工作流视图">
        <button role="tab" aria-selected={scope === 'execution'} className={scope === 'execution' ? 'on' : ''} onClick={() => setScope('execution')}>本次执行</button>
        <button role="tab" aria-selected={scope === 'standard'} className={scope === 'standard' ? 'on' : ''} onClick={() => setScope('standard')}>标准流程</button>
      </div>

      {state === "error" && !w ? (
        <div className="error-state" role="alert">
          <h2>读不到运行记录</h2>
          <p>
            {error || "启动任务运行服务后重新连接，或选择人工样例回放。"}
          </p>
        </div>
      ) : state === "empty" ? (
        <div className="loading-state"><h2>暂无可订阅的运行</h2><p>点击“新建任务”开始运行。</p></div>
      ) : !w || !run ? (
        <div className="loading-state" role="status">
          <span className="loader" />
          <h2>正在读取运行记录</h2>
        </div>
      ) : scope === 'execution' ? (
        <ExecutionFlow key={`${source}:${runId}`} trace={execution} events={events} run={run} focus={focus} inspect={inspect} />
      ) : (
        <section className="wf wf-standard" aria-label="智能体工作流">
          <header className="wf-bar">
            <div className="wf-title">
              <span className="wf-app">
                <Icon name="agent" size={18} />
              </span>
              <div>
                <b>标准流程</b>
                <small>
                  运行 <span className="mono">{run.run_id}</span> ·{" "}
                  {modeText[run.mode] ?? run.mode} · 任务第{" "}
                  {run.task_ref.revision} 版
                </small>
              </div>
            </div>
            <div className="wf-views" role="tablist" aria-label="看法">
              {(
                [
                  ["mind", "分支导图"],
                  ["flow", "流程总览"],
                ] as const
              ).map(([v, label]) => (
                <button
                  key={v}
                  role="tab"
                  aria-selected={view === v}
                  className={view === v ? "on" : ""}
                  onClick={() => switchView(v)}
                >
                  {label}
                </button>
              ))}
            </div>

          </header>

          <div className={`wf-body ${panelOpen ? "open" : ""}`}>
            {view === "mind" ? (
              <MindCanvas st={mind} selected={selected} onSelect={openNode} />
            ) : (
              <FlowCanvas flow={flow} selected={selected} onSelect={openNode} />
            )}
            {/* 窄屏：画布太小。分支导图按缩进大纲列出，流程总览按顺序竖排 */}
            <ol className="wf-list" aria-label="工作流节点">
              {(view === "mind"
                ? MIND_OUTLINE.map(({ n, depth }) => ({ n, depth }))
                : NODES.map((n) => ({ n, depth: 0 }))
              ).map(({ n, depth }) => {
                const info =
                  view === "mind" ? mind.nodes[n.id] : flow.nodes[n.id];
                const tag = "tag" in n ? n.tag : undefined;
                return (
                  <li
                    key={n.id}
                    style={{ marginLeft: depth * 14 }}
                    className={view === "mind" && depth > 0 ? "branch" : ""}
                  >
                    {tag && (
                      <span
                        className={`mm-tag ${tag.kind} ${mind.edges.has(n.id) ? "on" : ""}`}
                      >
                        {tag.kind} · {tag.text}
                      </span>
                    )}
                    <button
                      className={`wf-li ${n.kind} ${info.state} ${selected === n.id ? "sel" : ""} ${n.agent ? "agent" : ""}`}
                      onClick={() => openNode(n.id)}
                    >
                      <span className={`wf-icon ${n.kind}`}>
                        <Icon name={kindIcon[n.kind]} size={15} />
                      </span>
                      <span className="wf-text">
                        <b>{n.title}</b>
                        <small>
                          {"loop" in n && n.loop ? `↺ ${n.loop}` : n.sub}
                        </small>
                      </span>
                      <span className={`wf-badge ${info.state}`}>
                        {stateText[info.state]}
                        {info.count > 1 ? ` ×${info.count}` : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
            {panelOpen && (
              <aside className="wf-panel" aria-label="详情">
                <div className="wf-panel-tabs" role="tablist">
                  <button
                    role="tab"
                    aria-selected={panel === "node"}
                    className={panel === "node" ? "on" : ""}
                    onClick={() => setPanel("node")}
                    disabled={!selected}
                  >
                    节点详情
                  </button>
                  <button
                    role="tab"
                    aria-selected={panel === "trace"}
                    className={panel === "trace" ? "on" : ""}
                    onClick={() => setPanel("trace")}
                  >
                    追踪 · {shown.length}
                  </button>
                  <button
                    className="wf-close"
                    aria-label="收起详情"
                    onClick={() => setPanelOpen(false)}
                  >
                    <Icon name="close" size={15} />
                  </button>
                </div>
                {panel === "node" && selected ? (
                  <NodePanel
                    node={
                      (view === "mind"
                        ? PLACED.find((n) => n.id === selected)
                        : NODES.find((n) => n.id === selected)) as PanelNode
                    }
                    info={
                      view === "mind"
                        ? mind.nodes[selected]
                        : flow.nodes[selected]
                    }
                    events={shown.filter((e) => nodeOf(e) === selected)}
                    w={w}
                    byId={byId}
                    inspect={inspect}
                  />
                ) : (
                  <ol className="wf-trace">
                    {shown.map((e) => (
                      <li key={e.sequence} className={e.origin}>
                        <button onClick={() => openNode(nodeOf(e))}>
                          <span className="seq">
                            {String(e.sequence).padStart(2, "0")}
                          </span>
                          <span className="what">
                            <b>{eventText[e.type] ?? e.type}</b>
                            <small>
                              {originText[e.origin] ?? e.origin}
                              {e.candidate_id ? ` · ${e.candidate_id}` : ""}
                            </small>
                          </span>
                          <time>{clock(e.occurred_at)}</time>
                        </button>
                      </li>
                    ))}
                    {!shown.length && <li className="empty">还没有事件</li>}
                  </ol>
                )}
              </aside>
            )}
          </div>
        </section>
      )}

      <AgentLogs logs={logs} runId={runId || "waterguard"} onInspect={inspect} onEvent={(sequence) => {
        const event = events.find((event) => event.sequence === sequence);
        if (event) {
          setScope('execution');
          setFocus(event.native_tool_call_ref ?? (event.type === 'run.started' ? 'execution-start' : event.type === 'run.completed' ? 'execution-end' : null));
        }
      }} />

    </div>
  );
}

const tagText: Record<TagKind, string> = {
  IF: "如果",
  ELIF: "否则如果",
  ELSE: "否则",
};

/* ---------------------------------------------------------------- 分支导图 */
function MindCanvas({
  st,
  selected,
  onSelect,
}: {
  st: ReturnType<typeof mindState>;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const pz = usePanZoom(MSTAGE_W, MSTAGE_H);
  const byId = useMemo(() => new Map(PLACED.map((n) => [n.id, n])), []);
  return (
    <div className="wf-canvas mind" ref={pz.wrapRef} {...pz.handlers}>
      <div
        className="wf-stage"
        style={{ width: MSTAGE_W, height: MSTAGE_H, transform: pz.transform }}
      >
        <svg
          className="wf-edges"
          width={MSTAGE_W}
          height={MSTAGE_H}
          aria-hidden="true"
        >
          <defs>
            <marker
              id="mm-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L10,5 L0,10 z" />
            </marker>
          </defs>
          {PLACED.filter((n) => n.parent).map((n) => {
            const p = byId.get(n.parent!)!;
            const x1 = p.x + M.w,
              y1 = p.y + M.h / 2,
              x2 = n.x,
              y2 = n.y + M.h / 2;
            const dx = (x2 - x1) * 0.6;
            const on = st.edges.has(n.id);
            return (
              <g
                key={n.id}
                className={`wf-edge ${on ? "on" : ""} ${on && st.current === n.id ? "live" : ""} ${n.tag ? `t-${n.tag.kind}` : ""}`}
              >
                <path
                  d={`M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`}
                  markerEnd="url(#mm-arrow)"
                />
              </g>
            );
          })}
        </svg>
        {PLACED.filter((n) => n.tag).map((n) => (
          <span
            key={`tag-${n.id}`}
            className={`mm-tag ${n.tag!.kind} ${st.edges.has(n.id) ? "on" : ""}`}
            style={{ left: n.x, top: n.y - 27 }}
            title={tagText[n.tag!.kind]}
          >
            {n.tag!.kind} · {n.tag!.text}
          </span>
        ))}
        {PLACED.map((n) => {
          const info = st.nodes[n.id];
          return (
            <button
              key={n.id}
              className={`wf-node mm-node ${n.kind} ${info.state} ${selected === n.id ? "sel" : ""} ${n.agent ? "agent" : ""} ${n.loop ? "leaf-loop" : ""}`}
              style={{ left: n.x, top: n.y, width: M.w, height: M.h }}
              onClick={() => onSelect(n.id)}
              aria-label={`${n.title}：${stateText[info.state]}`}
            >
              <span className="wf-icon">
                <Icon name={kindIcon[n.kind]} size={16} />
              </span>
              <span className="wf-text">
                <b>{n.title}</b>
                <small>{n.loop ? `↺ ${n.loop}` : n.sub}</small>
              </span>
              <span className="wf-state">
                <i />
                {stateText[info.state]}
                {info.count > 1 ? ` ×${info.count}` : ""}
              </span>
            </button>
          );
        })}
      </div>
      <ZoomBar pz={pz} />
      <div className="wf-legend" aria-hidden="true">
        <span className="k-llm">智能体角色</span>
        <span className="k-tool">工具</span>
        <span className="k-gateway">网关</span>
        <span className="k-loop">↺ 回到前面的步骤</span>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- 流程总览 */
function FlowCanvas({
  flow,
  selected,
  onSelect,
}: {
  flow: ReturnType<typeof flowState>;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const pz = usePanZoom(STAGE_W, STAGE_H);
  const pos = useMemo(() => new Map(NODES.map((n) => [n.id, nodeXY(n)])), []);
  const { nodeW: W, nodeH: H } = LAYOUT;
  const path = (from: string, to: string) => {
    const a = pos.get(from)!,
      b = pos.get(to)!;
    if (a.y === b.y) {
      // 同一行：上行往右、下行往左
      const right = b.x > a.x;
      const x1 = right ? a.x + W : a.x,
        x2 = right ? b.x : b.x + W,
        y = a.y + H / 2;
      const dx = (x2 - x1) / 2;
      return `M${x1},${y} C${x1 + dx},${y} ${x2 - dx},${y} ${x2},${y}`;
    }
    // 上下两行之间：从下沿到上沿，或从上沿到下沿
    const down = b.y > a.y;
    const x = a.x + W / 2,
      y1 = down ? a.y + H : a.y,
      y2 = down ? b.y : b.y + H;
    const dy = (y2 - y1) / 2;
    return `M${x},${y1} C${x},${y1 + dy} ${x},${y2 - dy} ${x},${y2}`;
  };

  return (
    <div className="wf-canvas" ref={pz.wrapRef} {...pz.handlers}>
      <div
        className="wf-stage"
        style={{ width: STAGE_W, height: STAGE_H, transform: pz.transform }}
      >
        <svg
          className="wf-edges"
          width={STAGE_W}
          height={STAGE_H}
          aria-hidden="true"
        >
          <defs>
            <marker
              id="wf-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L10,5 L0,10 z" />
            </marker>
          </defs>
          {EDGES.map((e) => {
            const on = flow.edges.has(edgeKey(e));
            const live = on && flow.current === e.to;
            const d = path(e.from, e.to);
            const a = pos.get(e.from)!,
              b = pos.get(e.to)!;
            const lx =
              a.y === b.y
                ? (a.x + b.x + W) / 2
                : a.x + W / 2 + (e.loop ? -12 : 12);
            const ly = a.y === b.y ? a.y + H / 2 - 12 : (a.y + b.y + H) / 2;
            return (
              <g
                key={edgeKey(e)}
                className={`wf-edge ${on ? "on" : ""} ${live ? "live" : ""} ${e.loop ? "loop" : ""}`}
              >
                <path d={d} markerEnd="url(#wf-arrow)" />
                {e.label && (
                  <text
                    x={lx}
                    y={ly}
                    textAnchor={
                      a.y === b.y ? "middle" : e.loop ? "end" : "start"
                    }
                  >
                    {e.label}
                    {e.loop && flow.loops > 0 ? ` ×${flow.loops}` : ""}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {NODES.map((n) => {
          const p = pos.get(n.id)!;
          const info = flow.nodes[n.id];
          return (
            <button
              key={n.id}
              className={`wf-node ${n.kind} ${info.state} ${selected === n.id ? "sel" : ""} ${n.agent ? "agent" : ""}`}
              style={{ left: p.x, top: p.y, width: W, height: H }}
              onClick={() => onSelect(n.id)}
              aria-label={`${n.title}：${stateText[info.state]}`}
            >
              <span className="wf-icon">
                <Icon name={kindIcon[n.kind]} size={16} />
              </span>
              <span className="wf-text">
                <b>{n.title}</b>
                <small>{n.sub}</small>
              </span>
              <span className="wf-state">
                <i />
                {stateText[info.state]}
                {info.count > 1 ? ` ×${info.count}` : ""}
              </span>
            </button>
          );
        })}
      </div>
      <ZoomBar pz={pz} />
      <div className="wf-legend" aria-hidden="true">
        <span className="k-llm">智能体角色</span>
        <span className="k-tool">工具</span>
        <span className="k-gateway">网关</span>
        <span className="k-condition">条件</span>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- 右侧：节点详情 */
function NodePanel({
  node,
  info,
  events,
  w,
  byId,
  inspect,
}: {
  node: PanelNode;
  info: { state: NodeState; count: number; note: string };
  events: RunEvent[];
  w: Workspace;
  byId: Map<string, Candidate>;
  inspect: Inspect;
}) {
  const run = w.run!;
  return (
    <div className="wf-node-panel">
      <header>
        <span className={`wf-icon ${node.kind}`}>
          <Icon name={kindIcon[node.kind]} size={16} />
        </span>
        <div>
          <b>{node.title}</b>
          <small>
            {kindText[node.kind]} · {node.sub}
          </small>
        </div>
        <span className={`wf-badge ${info.state}`}>
          {stateText[info.state]}
        </span>
      </header>
      {info.note && <p className="wf-note">{info.note}</p>}
      {node.id === "end" && (
        <dl className="ag-kv">
          <div>
            <dt>状态</dt>
            <dd>{runStatusText[run.status] ?? run.status}</dd>
          </div>
          <div>
            <dt>结论</dt>
            <dd>{conclusionText[run.conclusion] ?? run.conclusion}</dd>
          </div>
          <div className="wide">
            <dt>选定方案</dt>
            <dd>{run.selected_candidate_id ?? "未选定"}</dd>
          </div>
        </dl>
      )}
      {node.id === "freeze" && (
        <dl className="ag-kv">
          <div>
            <dt>任务</dt>
            <dd className="mono">{run.task_ref.task_id}</dd>
          </div>
          <div>
            <dt>版本</dt>
            <dd>第 {run.task_ref.revision} 版</dd>
          </div>
          <div className="wide">
            <dt>约束哈希</dt>
            <dd className="mono">{run.task_ref.constraint_hash}</dd>
          </div>
        </dl>
      )}
      {events.length > 0 && (
        <div className="wf-runs">
          <h4>本次运行 · {events.length} 条记录</h4>
          {events.map((e) => (
            <EventDetail
              key={e.sequence}
              e={e}
              c={e.candidate_id ? byId.get(e.candidate_id) : undefined}
              w={w}
              inspect={inspect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- 一条事件的数据 */
function EventDetail({
  e,
  c,
  w,
  inspect,
}: {
  e: RunEvent;
  c: Candidate | undefined;
  w: Workspace;
  inspect: Inspect;
}) {
  const run = w.run!;
  return (
    <article className={`wf-event ${e.origin}`}>
      <header>
        <span className="seq">{String(e.sequence).padStart(2, "0")}</span>
        <b>{eventText[e.type] ?? e.type}</b>
        <span className="who">{originText[e.origin] ?? e.origin}</span>
        <time>{clock(e.occurred_at)}</time>
      </header>
      {e.type === "candidate.submitted" && c && (
        <>
          <p className="wf-line">
            <b>{c.name}</b>
          </p>
          {c.plan.actions.length ? (
            <ul className="ag-actions">
              {c.plan.actions.map((a, k) => (
                <li key={k}>
                  {actionText[a.kind]} <b className="mono">{a.link_id}</b> ·
                  仿真时刻 {time(a.simulation_time_s)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="wf-line">不操作任何设施（故障基线）。</p>
          )}
          <dl className="ag-kv">
            <div>
              <dt>提出者</dt>
              <dd>{sourceText[c.plan.source] ?? c.plan.source}</dd>
            </div>
            <div>
              <dt>父方案</dt>
              <dd className="mono">{c.plan.parent_candidate_id ?? "—"}</dd>
            </div>
            <div className="wide">
              <dt>理由</dt>
              <dd>{c.plan.actions.length ? c.plan.actions.map((action) => `${actionText[action.kind]} ${action.link_id}`).join('；') : '保持设施状态'}</dd>
            </div>
          </dl>
        </>
      )}
      {e.type === "simulation.completed" && c && c.verification && (
        <>
          <p className="ag-flag">{engineStatus(c).text}</p>
          <dl className="ag-kv">
            <div>
              <dt>缺水</dt>
              <dd>{num(c.verification.metrics.shortage_m3)} m³</dd>
            </div>
            <div>
              <dt>最低压力</dt>
              <dd>{num(c.verification.metrics.min_pressure_m)} m</dd>
            </div>
            <div>
              <dt>供需比</dt>
              <dd>
                {c.verification.metrics.supply_ratio == null
                  ? "—"
                  : `${num(c.verification.metrics.supply_ratio * 100)}%`}
              </dd>
            </div>
            <div>
              <dt>时序帧数</dt>
              <dd>{c.series?.frames.length ?? "—"}</dd>
            </div>
          </dl>
        </>
      )}
      {e.type === "verification.completed" && c && c.verification && (
        <>
          <Gates c={c} />
          {c.verification.violations.length > 0 && (
            <p className="wf-line">
              违规 {c.verification.violations.length} 条，例：节点{" "}
              <span className="mono">
                {c.verification.violations[0].node_id}
              </span>{" "}
              {time(c.verification.violations[0].start_s)}–
              {time(c.verification.violations[0].end_s)}{" "}
              {c.verification.violations[0].unit === "ratio"
                ? `供需比 ${num(c.verification.violations[0].observed * 100)}%`
                : `压力 ${num(c.verification.violations[0].observed)} m`}
              （要求{" "}
              {c.verification.violations[0].unit === "ratio"
                ? `≥ ${num(c.verification.violations[0].threshold * 100, 0)}%`
                : `≥ ${num(c.verification.violations[0].threshold, 0)} m`}
              ）
            </p>
          )}
          {c.verification.recommendation_blockers.length > 0 && (
            <p className="wf-line muted">
              不能推荐的原因：
              {c.verification.recommendation_blockers.map((reason) => recommendationBlockerText[reason] ?? reason).join("、")}
            </p>
          )}
        </>
      )}
      {(e.type === "run.started" || e.type === "run.completed") && (
        <dl className="ag-kv">
          <div>
            <dt>任务版本</dt>
            <dd>第 {e.task_revision} 版</dd>
          </div>
          {e.type === "run.completed" && (
            <div>
              <dt>结论</dt>
              <dd>{conclusionText[run.conclusion] ?? run.conclusion}</dd>
            </div>
          )}
        </dl>
      )}
      {e.native_tool_call_ref && (
        <p className="wf-ids">原生调用号：{e.native_tool_call_ref}</p>
      )}
      <button
        className="ag-raw"
        onClick={() => inspect(`事件 ${e.sequence} 原文`, e)}
      >
        查看事件原文
        <Icon name="arrow" size={14} />
      </button>
    </article>
  );
}

function Gates({ c }: { c: Candidate }) {
  const verification = c.verification;
  if (!verification) return <p className="wf-line muted">尚未取得验证报告。</p>;
  return (
    <ol className="ag-gates" aria-label="五层准入">
      {gates.map(([key, label]) => {
        const st = verification.admission[key];
        return (
          <li key={key} className={st}>
            <span>{st === "pass" ? "✓" : st === "fail" ? "✕" : "…"}</span>
            {label}
            <small>{statusText[st]}</small>
          </li>
        );
      })}
    </ol>
  );
}
