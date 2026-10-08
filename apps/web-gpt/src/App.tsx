import { useCallback, useEffect, useRef, useState } from "react";
import type { Violation, RunRecord } from "@waterguard/contracts";
import {
  actionText,
  caseNames,
  download,
  engineStatus,
  loadWorkspace,
  networkName,
  num,
  sources,
  statusText,
  time,
} from "./data";
import type { Candidate, Source, Workspace } from "./data";
import AgentPage from "./AgentPage";
import RunControls, { ThresholdControls } from './RunControls';
import JevPanel from './JevPanel';
import { AgentRunStrip } from "./AgentRun";
import Cover from "./Cover";
import Icon from "./Icon";
import { studioImage, teaserImage } from "./model-assets";
import { useTween } from "./motion";
import { Network, NodeChart, linkState, linkStateText } from "./Network";
import type { Entity } from "./Network";
import {
  Admission,
  Compare,
  Evidence,
  FixtureEvents,
  Modeling,
  Violations,
} from "./Pages";

type Page = "cover" | "overview" | "agent" | "compare" | "model" | "evidence";
const navigation: { id: Page; label: string; icon: string; english: string }[] =
  [
    { id: "cover", label: "首页", icon: "home", english: "Home" },
    {
      id: "overview",
      label: "应急工作台",
      icon: "network",
      english: "Operations",
    },
    { id: "agent", label: "智能体", icon: "agent", english: "Agent" },
    {
      id: "compare",
      label: "方案对照",
      icon: "compare",
      english: "Comparison",
    },
    { id: "model", label: "建模与仿真", icon: "cube", english: "Model studio" },
    { id: "evidence", label: "证据中心", icon: "file", english: "Evidence" },
  ];

function JsonDialog({
  item,
  close,
}: {
  item: { title: string; data: unknown };
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const d = ref.current!;
    d.showModal();
    button.current?.focus();
    return () => d.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="json-dialog"
      aria-labelledby="dialog-title"
      onCancel={close}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <header>
        <div>
          <span className="eyebrow">接口原始数据</span>
          <h2 id="dialog-title">{item.title}</h2>
        </div>
        <button
          ref={button}
          className="icon-button"
          onClick={close}
          aria-label="关闭详情"
        >
          <Icon name="close" />
        </button>
      </header>
      <pre>{JSON.stringify(item.data, null, 2)}</pre>
      <footer>
        <button
          className="button secondary"
          onClick={() => download("waterguard-api-copy.json", item.data)}
        >
          <Icon name="download" size={16} />
          导出 JSON 副本
        </button>
      </footer>
    </dialog>
  );
}

function Inspector({
  w,
  c,
  frameIndex,
  entity,
  inspect,
}: {
  w: Workspace;
  c: Candidate;
  frameIndex: number;
  entity: Entity;
  inspect: (title: string, data: unknown) => void;
}) {
  const frame = c.series?.frames[frameIndex];
  // Net3 里节点和管段可能同号（如 123），按实体类型分别查找，不能只按编号。
  const isNode = entity.kind === "node";
  const node = isNode
      ? w.scene.nodes.find((n) => n.id === entity.id)
      : undefined,
    link = isNode ? undefined : w.scene.links.find((l) => l.id === entity.id);
  const sample = isNode
      ? frame?.nodes.find((n) => n.node_id === entity.id)
      : undefined,
    ls = isNode ? undefined : frame?.links.find((l) => l.link_id === entity.id),
    tank = isNode
      ? frame?.tanks?.find((t) => t.node_id === entity.id)
      : undefined,
    leaks = isNode
      ? []
      : (frame?.leaks?.filter((l) => l.source_link_id === entity.id) ?? []);
  const ratio =
    sample && sample.expected_demand_m3s > 0
      ? sample.delivered_demand_m3s / sample.expected_demand_m3s
      : null;
  const valid =
    c.verification.validity === "valid" &&
    c.verification.admission.simulation_valid === "pass";
  const state = link && valid ? linkState(ls, link.initial_status) : null;
  const kindText =
    entity.kind === "node"
      ? node?.kind === "junction"
        ? "用水节点"
        : node?.kind === "tank"
          ? "水箱"
          : "水源"
      : link?.kind === "pump"
        ? "水泵"
        : link?.kind === "valve"
          ? "阀门"
          : "管段";
  const eligible = c.verification.admission.recommendation_eligible;
  return (
    <aside className="inspector">
      <div className="inspector-top">
        <span className="eyebrow">实体详情</span>
        <span className="badge neutral">{kindText}</span>
      </div>
      <div className="entity-title" key={entity.id}>
        <span className="entity-icon">
          <Icon name={entity.kind === "node" ? "target" : "link"} size={24} />
        </span>
        <div>
          <h2>
            {isNode && node?.kind === "junction" ? "节点" : kindText}{" "}
            <b>{entity.id}</b>
          </h2>
          <span>{frame ? `当前时刻 ${time(frame.time_s)}` : "无时序数据"}</span>
        </div>
      </div>
      <dl className="detail-list entity-details">
        {entity.kind === "node" && node?.kind === "tank" ? (
          <>
            <div>
              <dt>水位</dt>
              <dd>
                {valid && tank ? num(tank.level_m) : "—"} <small>m</small>
              </dd>
            </div>
            <div>
              <dt>蓄水量</dt>
              <dd>
                {valid && tank ? num(tank.volume_m3, 0) : "—"} <small>m³</small>
              </dd>
            </div>
            <div>
              <dt>净流入</dt>
              <dd>
                {valid && tank ? num(tank.net_inflow_m3s, 4) : "—"}{" "}
                <small>m³/s</small>
              </dd>
            </div>
          </>
        ) : entity.kind === "node" && node?.kind === "reservoir" ? (
          <div>
            <dt>水源水头（场景数据）</dt>
            <dd>
              {num(node.elevation_m)} <small>m</small>
            </dd>
          </div>
        ) : entity.kind === "node" ? (
          <>
            <div>
              <dt>节点压力</dt>
              <dd
                className={
                  valid &&
                  sample &&
                  sample.pressure_m < c.task.hard_constraints.min_pressure_m
                    ? "negative"
                    : ""
                }
              >
                {valid ? num(sample?.pressure_m) : "—"} <small>m</small>
              </dd>
            </div>
            <div>
              <dt>供需比</dt>
              <dd>{valid && ratio != null ? `${num(ratio * 100)}%` : "—"}</dd>
            </div>
          </>
        ) : (
          <>
            <div>
              <dt>连接节点</dt>
              <dd>
                {link?.from_node} → {link?.to_node}
              </dd>
            </div>
            <div>
              <dt>物理状态</dt>
              <dd className={state && state !== "open" ? "negative" : ""}>
                {state ? linkStateText[state] : "无数据"}
              </dd>
            </div>
            <div>
              <dt>起点流量</dt>
              <dd>
                {valid ? num(ls?.flow_m3s, 5) : "—"} <small>m³/s</small>
              </dd>
            </div>
            <div>
              <dt>终点流量</dt>
              <dd>
                {valid ? num(ls?.downstream_flow_m3s, 5) : "—"}{" "}
                <small>m³/s</small>
              </dd>
            </div>
            {valid && leaks.length > 0 && (
              <div>
                <dt>漏水流量</dt>
                <dd className="negative">
                  {num(
                    leaks.reduce((n, l) => n + l.leak_flow_m3s, 0),
                    5,
                  )}{" "}
                  <small>m³/s</small>
                </dd>
              </div>
            )}
          </>
        )}
      </dl>
      <button
        className="text-button inspector-source"
        onClick={() =>
          inspect(`${kindText} ${entity.id} · 当前时序`, {
            entity: entity.kind === "node" ? node : link,
            time_s: frame?.time_s,
            sample: entity.kind === "node" ? sample : ls,
            validity: c.verification.validity,
            simulation_id: c.simulationId,
          })
        }
      >
        查看原始数据
        <Icon name="arrow" size={15} />
      </button>
      <div className="inspector-actions">
        <h3>
          当前方案动作 <span>{c.plan.actions.length}</span>
        </h3>
        {c.plan.actions.length ? (
          c.plan.actions.map((a, i) => (
            <div className="action-item" key={i}>
              <span className="action-mark">
                <Icon name="pulse" size={16} />
              </span>
              <div>
                <strong>
                  {actionText[a.kind]} {a.link_id}
                </strong>
                <small>仿真时刻 {time(a.simulation_time_s)}</small>
              </div>
            </div>
          ))
        ) : (
          <p className="muted">无处置动作</p>
        )}
        <p className="subtle-note">
          {c.plan.source === "agh"
            ? "方案来源：AGH"
            : c.plan.source === "fixture"
              ? "方案来源：人工开发样例"
              : "方案来源：固定工程脚本"}
        </p>
      </div>
      <div className={`inspector-verdict ${eligible}`}>
        <Icon name="shield" size={19} />
        <div>
          <strong>
            {eligible === "pass"
              ? "可推荐"
              : eligible === "pending"
                ? "推荐待验证"
                : "当前不可推荐"}
          </strong>
        </div>
      </div>
    </aside>
  );
}

function Metric({
  label,
  raw,
  digits = 2,
  scale = 1,
  unit,
  detail,
  tone = "",
  detailTone = "",
}: {
  label: string;
  raw: number | null;
  digits?: number;
  scale?: number;
  unit: string;
  detail: string;
  tone?: string;
  detailTone?: string;
}) {
  // 切换方案时数字滚动到新值；读屏软件读到的是终值。
  const shown = useTween(raw);
  const final = raw == null ? "—" : num(raw * scale, digits);
  const text = shown == null ? "—" : num(shown * scale, digits);
  // 字号按位数收缩（1,645.30 这类长数字在窄栏里不被截断）；滚动过程中按较长的一方取字号。
  const length = Math.min(9, Math.max(final.length, text.length));
  return (
    <div className={`metric ${tone}`}>
      <div>
        <span>{label}</span>
      </div>
      <strong
        className={`len-${length}`}
        aria-label={`${label} ${final} ${unit}`}
      >
        {text}
        <small>{unit}</small>
      </strong>
      <p className={detailTone}>
        {detailTone === "positive" && <span aria-hidden="true">↓</span>}
        {detail}
      </p>
    </div>
  );
}

/** 时间轴底纹：预运行、故障时段与处置动作的位置，全部取自冻结任务和方案。 */
function TimelineBands({ c }: { c: Candidate }) {
  const frames = c.series?.frames;
  if (!frames || frames.length < 2) return null;
  const t0 = frames[0].time_s,
    t1 = frames[frames.length - 1].time_s;
  const pct = (t: number) =>
    `${Math.max(0, Math.min(1, (t - t0) / (t1 - t0 || 1))) * 100}%`;
  const span = (a: number, b: number) =>
    `${Math.max(0, (Math.min(b, t1) - Math.max(a, t0)) / (t1 - t0 || 1)) * 100}%`;
  const warmup = c.task.time_window.warmup_end_s,
    fault = c.task.fault;
  return (
    <div className="timeline-bands" aria-hidden="true">
      {warmup > t0 && (
        <span
          className="band warmup"
          style={{ left: pct(t0), width: span(t0, warmup) }}
          title="预运行，不计入评估"
        />
      )}
      {fault && (
        <span
          className="band fault"
          style={{
            left: pct(fault.start_s),
            width: span(fault.start_s, fault.end_s),
          }}
        />
      )}
      {c.plan.actions.map((a, i) => (
        <span
          key={i}
          className="band-tick action"
          style={{ left: pct(a.simulation_time_s) }}
        />
      ))}
    </div>
  );
}

function Overview({
  w,
  c,
  entity,
  frameIndex,
  onEntity,
  onFrame,
  onViolation,
  onCandidate,
  inspect,
  openPage,
}: {
  w: Workspace;
  c: Candidate;
  entity: Entity;
  frameIndex: number;
  onEntity: (v: Entity) => void;
  onFrame: (i: number) => void;
  onViolation: (v: Violation) => void;
  onCandidate: (id: string) => void;
  inspect: (title: string, data: unknown) => void;
  openPage: (p: Page) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const frames = c.series?.frames ?? [];
  const current = frames[frameIndex];
  useEffect(() => {
    setPlaying(false);
  }, [c.id]);
  useEffect(() => {
    if (!playing || !frames.length) return;
    const timer = setInterval(() => {
      if (frameIndex >= frames.length - 1) setPlaying(false);
      else onFrame(frameIndex + 1);
    }, 1100);
    return () => clearInterval(timer);
  }, [playing, frameIndex, frames.length, onFrame]);
  const metrics = c.verification.metrics,
    reference = w.candidates[0].verification.metrics,
    referenceName = w.candidates[0].name.split(" · ")[0];
  const sameTask = w.candidates.every(
    (x) => x.task.constraint_hash === w.candidates[0].task.constraint_hash,
  );
  // 改善百分比由前端按两个方案的接口数值计算，并在文字里写明对比对象。
  const improve =
    sameTask && c !== w.candidates[0] && reference.shortage_m3 > 0
      ? ((reference.shortage_m3 - metrics.shortage_m3) /
          reference.shortage_m3) *
        100
      : null;
  const status = engineStatus(c);
  const minPressure = metrics.min_pressure_m;
  const threshold = c.task.hard_constraints.min_pressure_m;
  const chartNode =
    entity.kind === "node"
      ? entity.id
      : (() => {
          const link = w.scene.links.find((l) => l.id === entity.id);
          return (
            [link?.from_node, link?.to_node].find((id) =>
              current?.nodes.some((n) => n.node_id === id),
            ) ?? w.scene.nodes.find((n) => n.kind === "junction")!.id
          );
        })();
  return (
    <div className="page-enter" data-simulation-id={c.simulationId}>
      <div className="overview-title">
        <div>
          <h1>
            供水应急工作台
            <span className="title-line" />
          </h1>
          <p>故障定位、方案对照与独立验收。</p>
        </div>
        <button className="button primary" onClick={() => openPage("evidence")}>
          <Icon name="file" size={17} />
          查看计算证据
          <Icon name="arrow" size={17} />
        </button>
      </div>
      <div className="task-strip">
        <span className="fault-icon">
          <Icon name={c.task.fault ? "warning" : "check"} size={19} />
        </span>
        <div>
          <strong>
            {c.task.fault
              ? `${caseNames[c.task.fault.kind] ?? c.task.fault.kind} · 管段 ${c.task.fault.link_id}`
              : "正常供水工况"}
          </strong>
          <span>
            {c.task.fault
              ? `${time(c.task.fault.start_s)} – ${time(c.task.fault.end_s)}`
              : "无故障"}
          </span>
        </div>
        <div className="task-strip-context">
          <span>{networkName(w.scene.network_id)}</span>
          <span>
            评估 {time(c.task.time_window.evaluation_start_s)} –{" "}
            {time(c.task.time_window.end_s)}
          </span>
        </div>
        <button
          className="text-button"
          onClick={() => inspect("冻结任务", c.task)}
        >
          任务详情
          <Icon name="chevron" size={15} />
        </button>
      </div>
      <AgentRunStrip
        w={w}
        onOpen={() => inspect("运行记录（人工样例）", w.run)}
      />
      <section className="operations-surface" aria-label="供水计算工作面">
        <div className="workspace-bar">
          <span className="workspace-label">
            <Icon name="network" size={16} /> 计算工作面
          </span>
          <div className="candidate-tabs">
            {w.candidates.map((x) => (
              <button
                key={x.id}
                className={x.id === c.id ? "active" : ""}
                aria-pressed={x.id === c.id}
                onClick={() => onCandidate(x.id)}
              >
                <span className="tiny-dot" />
                {x.name}
              </button>
            ))}
          </div>
          <span className={`engine-status ${status.tone}`}>
            {status.text}
            {status.tone === "ok" && <Icon name="check" size={14} />}
          </span>
        </div>
        <div className="workspace-grid">
          <aside className="result-rail">
            <div className="metric-strip">
              <Metric
                label="缺供体积"
                raw={metrics.shortage_m3}
                unit="m³"
                detail={
                  improve != null
                    ? `较${referenceName} 减少 ${num(improve, 1)}%`
                    : "评估窗口内累计缺供"
                }
                detailTone={improve != null && improve > 0 ? "positive" : ""}
              />
              <Metric
                label="总体供需比"
                raw={metrics.supply_ratio}
                scale={100}
                unit="%"
                detail="按用户供需体积计算"
              />
              <Metric
                label="最低节点压力"
                raw={minPressure}
                unit="m"
                tone={
                  minPressure == null
                    ? ""
                    : minPressure < 0
                      ? "bad"
                      : minPressure < threshold
                        ? "warn"
                        : "ok"
                }
                detail={`任务阈值 ≥ ${num(threshold, 2)} m`}
              />
            </div>
          </aside>
          <Network
            workspace={w}
            candidate={c}
            frameIndex={frameIndex}
            entity={entity}
            onSelect={onEntity}
          />
          <Inspector
            w={w}
            c={c}
            frameIndex={frameIndex}
            entity={entity}
            inspect={inspect}
          />
        </div>
        <div className="timeline">
          <button
            className={`play-button ${playing ? "playing" : ""}`}
            disabled={!frames.length}
            aria-label={playing ? "暂停时间回放" : "播放时间回放"}
            onClick={() => {
              if (!playing && frameIndex === frames.length - 1) onFrame(0);
              setPlaying(!playing);
            }}
          >
            <Icon name={playing ? "pause" : "play"} size={17} />
          </button>
          <div className="time-now">
            <strong>{current ? time(current.time_s) : "—"}</strong>
            <span>仿真时刻</span>
          </div>
          <div className="timeline-track">
            <TimelineBands c={c} />
            <input
              aria-label="仿真时间轴"
              type="range"
              min="0"
              max={Math.max(0, frames.length - 1)}
              value={frameIndex}
              disabled={!frames.length}
              onChange={(e) => {
                setPlaying(false);
                onFrame(Number(e.target.value));
              }}
            />
            <div>
              {frames
                .filter(
                  (_, i) => i === 0 || i === frames.length - 1 || i % 4 === 0,
                )
                .map((f) => (
                  <span key={f.time_s}>
                    {time(f.time_s)}
                    {f.time_s === c.task.time_window.evaluation_start_s
                      ? " · 评估开始"
                      : ""}
                  </span>
                ))}
            </div>
          </div>
          <span className="timeline-note">
            {c.task.time_window.step_s / 60} 分钟 / 帧
            <span className="timeline-key">
              {c.task.fault && (
                <>
                  <i className="k-fault" />
                  故障
                </>
              )}
              {c.plan.actions.length > 0 && (
                <>
                  <i className="k-action" />
                  动作
                </>
              )}
            </span>
          </span>
        </div>
      </section>
      <div className="admission-bar">
        <div>
          <Icon name="shield" size={18} />
          <strong>五层准入</strong>
          <button
            aria-label="查看五层准入依据"
            className="icon-button"
            onClick={() => inspect("服务端验收报告", c.verification)}
          >
            <Icon name="help" size={14} />
          </button>
        </div>
        <Admission key={c.id} candidate={c} />
        <span
          className={`badge ${c.verification.admission.recommendation_eligible}`}
        >
          {c.verification.admission.recommendation_eligible === "fail"
            ? "不可推荐"
            : statusText[c.verification.admission.recommendation_eligible]}
        </span>
      </div>
      <div className="lower-grid">
        <NodeChart
          candidates={w.candidates}
          nodeId={chartNode}
          selected={c}
          frameIndex={frameIndex}
          onFrame={onFrame}
        />
        <button className="model-teaser" onClick={() => openPage("model")}>
          <div>
            <span className="eyebrow">建模展台</span>
            <h2>
              设备与管道
              <br />
              模型展台
            </h2>
            <p>{studioImage.teaser}</p>
            <span className="text-button">
              打开建模展示
              <Icon name="arrow" size={16} />
            </span>
          </div>
          <img src={teaserImage.src} alt={teaserImage.alt} />
          <span className="teaser-caption">{teaserImage.caption}</span>
        </button>
      </div>
      <Violations candidate={c} onSelect={onViolation} />
      <FixtureEvents w={w} inspect={inspect} />
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>(() => {
    const params = new URLSearchParams(window.location.search);
    const requested = params.get("page");
    return navigation.some((item) => item.id === requested) ? requested as Page : params.has("run") ? "agent" : "cover";
  });
  const [source, setSource] = useState<Source>("china");
  const [selection, setSelection] = useState("");
  const [activeRun, setActiveRun] = useState<RunRecord | null>(null);
  const followRun = useCallback((id: string) => {
    setSource('runtime'); setSelection(id);
    const url = new URL(window.location.href); url.searchParams.set('run', id); window.history.replaceState(null, '', url);
  }, []);
  const [w, setW] = useState<Workspace | null>(null);
  const [candidateId, setCandidateId] = useState("");
  const [entity, setEntity] = useState<Entity>({ kind: "node", id: "" });
  const [frameIndex, setFrameIndex] = useState(0);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [dialog, setDialog] = useState<{ title: string; data: unknown } | null>(
    null,
  );
  const [focus, setFocus] = useState(false);
  const loadHydraulics = page !== "agent";
  const inspect = (title: string, data: unknown) => setDialog({ title, data });
  useEffect(() => {
    if (!loadHydraulics || source === 'runtime') return;
    const controller = new AbortController();
    setW(null);
    setError("");
    loadWorkspace(source, selection, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        const active = data.candidates.at(-1)!;
        setW(data);
        setCandidateId(active.id);
        const nodeId =
          active.verification.violations.find((v) => v.code === "MIN_PRESSURE")
            ?.node_id ??
          data.scene.nodes.find((n) => n.kind === "junction")!.id;
        setEntity({ kind: "node", id: nodeId });
        setFrameIndex(
          Math.max(
            0,
            active.series?.frames.findIndex(
              (f) => f.time_s >= active.task.time_window.evaluation_start_s,
            ) ?? 0,
          ),
        );
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "数据读取失败");
      });
    return () => controller.abort();
  }, [source, selection, retry, loadHydraulics]);
  useEffect(() => {
    if (source !== 'runtime' || selection) return;
    const controller = new AbortController();
    fetch('/wg/api/runs', { signal: controller.signal }).then((response) => response.json()).then((rows: RunRecord[]) => {
      const run = rows.find((row) => row.mode === 'live') ?? rows.find((row) => row.status === 'COMPLETED');
      if (run && !controller.signal.aborted) followRun(run.run_id);
    }).catch(() => { if (!controller.signal.aborted) setError('任务服务连接失败'); });
    return () => controller.abort();
  }, [source, selection, followRun]);
  useEffect(() => {
    if (!loadHydraulics || source !== 'runtime' || !selection) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    let fetching = false, pending = false, first = true, latestStatus = '';
    setW(null); setError(''); setActiveRun(null);
    const hydrate = async () => {
      if (fetching) { pending = true; return; }
      fetching = true;
      try {
        const data = await loadWorkspace('runtime', selection, controller.signal);
        if (controller.signal.aborted) return;
        const candidate = data.candidates.at(-1)!;
        setW(data); setActiveRun(data.run); setError('');
        setCandidateId(candidate.id);
        if (first) {
          first = false; setEntity({ kind: 'node', id: data.scene.nodes.find((node) => node.kind === 'junction')!.id });
          setFrameIndex(Math.max(0, candidate.series?.frames.findIndex((frame) => frame.time_s >= candidate.task.time_window.evaluation_start_s) ?? 0));
        }
      } catch (error) {
        if (!controller.signal.aborted && latestStatus === 'COMPLETED') setError(error instanceof Error ? error.message : '运行结果读取失败');
      }
      finally { fetching = false; if (pending && !controller.signal.aborted) { pending = false; void hydrate(); } }
    };
    const stream = new EventSource(`/wg/api/runs/${encodeURIComponent(selection)}/stream`);
    const updateRun = (event: MessageEvent) => {
      try {
        const run = JSON.parse(event.data) as RunRecord;
        if (run.run_id !== selection) return;
        latestStatus = run.status;
        setActiveRun(run); setW((previous) => previous?.run?.run_id === selection ? { ...previous, run } : previous);
        if (['ERROR', 'BUDGET_EXHAUSTED', 'CANCELLED'].includes(run.status)) setError(`运行${run.status === 'CANCELLED' ? '已取消' : '已结束'}，请查看运行日志。`);
      } catch { setError('运行记录读取失败'); }
    };
    stream.addEventListener('run', updateRun as EventListener);
    stream.addEventListener('workflow', () => { clearTimeout(timer); timer = setTimeout(() => void hydrate(), 120); });
    stream.addEventListener('stream.complete', (event) => { updateRun(event as MessageEvent); void hydrate(); stream.close(); });
    void hydrate();
    return () => { controller.abort(); clearTimeout(timer); stream.close(); };
  }, [loadHydraulics, source, selection, retry]);
  const c =
    w?.candidates.find((x) => x.id === candidateId) ?? w?.candidates.at(-1);
  const openPage = (p: Page) => {
    setPage(p);
    const url = new URL(window.location.href);
    if (p === "cover" && !url.searchParams.has("run")) url.searchParams.delete("page");
    else url.searchParams.set("page", p);
    window.history.replaceState(null, "", url);
    window.scrollTo({ top: 0, behavior: "instant" });
  };
  const chooseCandidate = (id: string) => {
    if (!w || !c) return;
    const next = w.candidates.find((x) => x.id === id)!,
      t = c.series?.frames[frameIndex]?.time_s;
    setCandidateId(id);
    setFrameIndex(
      Math.max(0, next.series?.frames.findIndex((f) => f.time_s === t) ?? 0),
    );
  };
  const locateViolation = (v: Violation) => {
    setEntity({ kind: "node", id: v.node_id });
    setFrameIndex(
      Math.max(
        0,
        c?.series?.frames.findIndex((f) => f.time_s === v.start_s) ?? 0,
      ),
    );
    openPage("overview");
  };
  return (
    <div
      className={`app ${focus ? "focus-mode" : ""} ${page === "cover" ? "on-cover" : ""}`}
      data-page={page}
    >
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <header className="global-header">
        <div className="global-header-inner">
          <a
            className="brand"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              openPage("cover");
            }}
          >
            <span className="brand-mark">
              <Icon name="water" size={27} />
            </span>
            <div>
              <strong>WaterGuard</strong>
              <small>水脉卫士</small>
            </div>
          </a>
          <div className="header-context">
            <span>供水管网决策平台</span>
            <Icon name="chevron" size={14} />
            <strong>{navigation.find((item) => item.id === page)?.label}</strong>
          </div>
          <span className="header-edition">WaterGuard <span> / </span> Operations</span>
        </div>
      </header>
      <aside className="workspace-nav" aria-label="工作区导航">
          <div className="nav-section-label">工作空间 <span>WORKSPACE</span></div>
          <nav aria-label="主要导航">
            {navigation.map((n) => (
              <button
                key={n.id}
                aria-label={n.label}
                title={n.label}
                aria-current={page === n.id ? "page" : undefined}
                className={page === n.id ? "active" : ""}
                onClick={() => openPage(n.id)}
              >
                <Icon name={n.icon} size={18} />
                <span>{n.label}<small>{n.english}</small></span>
              </button>
            ))}
          </nav>
          <div className="nav-footer">
            <Icon name="water" size={18} />
            <div><strong>水脉卫士</strong><span>供水应急 · 仿真与决策</span></div>
          </div>
      </aside>
      {page === "cover" ? (
        <main id="main-content" className="cover-main">
          <Cover w={w} openPage={openPage} />
        </main>
      ) : page === "agent" ? (
        <div className="app-main">
          <header className="topbar">
            <div className="data-mode-note"><Icon name="agent" size={14} />智能体工作流与运行日志</div>
            <button className="icon-button" title="切换专注展示" aria-label="切换专注展示"
              aria-pressed={focus} onClick={() => setFocus(!focus)}><Icon name="expand" size={18} /></button>
          </header>
          <main id="main-content"><AgentPage inspect={inspect} onRun={followRun} /></main>
        </div>
      ) : (
        <div className="app-main">
          <header className="topbar">
            <div className="data-mode-note">
              <Icon name="shield" size={14} />
              {source === 'runtime' && activeRun?.mode === 'live' ? `Agnes · ${activeRun.status === 'RUNNING' ? '实时执行' : activeRun.status === 'COMPLETED' ? '运行完成' : '任务运行'} · ${activeRun.phase}` : w?.mode === 'fixture' ? '开发样例' : '供水管网 · 水力状态'}
            </div>
            <div className="topbar-actions">
              <RunControls run={source === 'runtime' ? activeRun : null} created={(run) => { followRun(run.run_id); }} updated={() => setRetry((n) => n + 1)} />
              <label className="source-select">
                <span className="tiny-dot" />
                <select
                  aria-label="数据源"
                  value={source}
                  onChange={(e) => {
                    setSelection("");
                    setSource(e.target.value as Source);
                  }}
                >
                  {Object.entries(sources).map(([key, s]) => (
                    <option key={key} value={key}>
                      {s.label}
                    </option>
                  ))}
                </select>
                <Icon name="down" size={13} />
              </label>
              <span className={`mode-badge ${w?.mode ?? "unknown"}`}>
                {w ? w.mode.toUpperCase() : error ? "连接失败" : "读取中"}
              </span>
              <span className="topbar-divider" />
              <button
                className="icon-button"
                title="切换专注展示"
                aria-label="切换专注展示"
                aria-pressed={focus}
                onClick={() => setFocus(!focus)}
              >
                <Icon name="expand" size={18} />
              </button>
              <button
                className="icon-button"
                aria-label="刷新数据"
                onClick={() => setRetry((n) => n + 1)}
              >
                <Icon name="refresh" size={18} />
              </button>
            </div>
          </header>
          <main id="main-content">
            {source === 'runtime' && <JevPanel runId={selection || undefined} />}
            {w && c && source !== 'fixture' && (
              <details className="workspace-configuration">
                <summary>
                  <Icon name="sliders" size={15} />
                  <strong>工况与验收</strong>
                  <span>{c.name}</span>
                  <span className="configuration-threshold">压力 ≥ {num(c.task.hard_constraints.min_pressure_m, 2)} m · 供需比 ≥ {num(c.task.hard_constraints.min_supply_ratio * 100, 2)}%</span>
                  <Icon name="down" size={13} />
                </summary>
                <div className="configuration-body">
                  <ThresholdControls task={c.task} created={(run) => { followRun(run.run_id); setRetry((n) => n + 1); }} />
            {w && c && (source === "bench" || source === "china") && (
              <div className="benchmark-picker">
                <label htmlFor="benchmark-case">选择真实工况</label>
                <select
                  id="benchmark-case"
                  value={selection || w.candidates.at(-1)?.simulationId}
                  onChange={(e) => setSelection(e.target.value)}
                >
                  {w.index?.cases.map((x) => (
                    <option key={x.simulation_id} value={x.simulation_id}>
                      {networkName(x.network_id)} / {x.engine.toUpperCase()} /{" "}
                      {caseNames[x.case] ?? x.case}
                    </option>
                  ))}
                </select>
                <span>正常工况与所选工况对照</span>
              </div>
            )}
                </div>
              </details>
            )}
            {error ? (
              <div className="error-state" role="alert">
                <span>
                  <Icon name="link" size={34} />
                </span>
                <h1>暂时无法读取计算结果</h1>
                <p>{error}</p>
                <code>
                  {sources[source].port} · {sources[source].label}
                </code>
                <button
                  className="button primary"
                  onClick={() => setRetry((n) => n + 1)}
                >
                  <Icon name="refresh" size={17} />
                  重新连接
                </button>
              </div>
            ) : !w || !c ? (
              <div className="loading-state" role="status">
                <span className="loader" />
                <h2>{source === 'runtime' && activeRun?.mode === 'live' ? 'Agnes 正在执行任务' : '正在载入水力计算'}</h2>
                <p>{source === 'runtime' && activeRun ? `${activeRun.phase} · 已仿真 ${activeRun.budget_usage.simulations} 次` : '读取任务、拓扑与候选方案…'}</p>
              </div>
            ) : (
              <>
                {page !== "overview" && (
                  <div className="page-candidate-select">
                    <label>
                      当前结果
                      <select
                        aria-label="当前结果"
                        value={c.id}
                        onChange={(e) => chooseCandidate(e.target.value)}
                      >
                        {w.candidates.map((x) => (
                          <option key={x.id} value={x.id}>
                            {x.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <span className="mono">{c.simulationId}</span>
                  </div>
                )}
                {page === "overview" && (
                  <Overview
                    w={w}
                    c={c}
                    entity={entity}
                    frameIndex={frameIndex}
                    onEntity={setEntity}
                    onFrame={setFrameIndex}
                    onViolation={locateViolation}
                    onCandidate={chooseCandidate}
                    inspect={inspect}
                    openPage={openPage}
                  />
                )}
                {page === "compare" && (
                  <Compare
                    w={w}
                    selected={c}
                    onCandidate={chooseCandidate}
                    onViolation={locateViolation}
                    inspect={inspect}
                  />
                )}
                {page === "model" && <Modeling w={w} c={c} inspect={inspect} />}
                {page === "evidence" && (
                  <Evidence w={w} c={c} inspect={inspect} />
                )}
                <footer className="app-footer">
                  <span>WaterGuard / 水脉卫士</span>
                  <span>
                    {networkName(w.scene.network_id)} · {w.mode.toUpperCase()}
                  </span>
                </footer>
              </>
            )}
          </main>
        </div>
      )}
      {dialog && <JsonDialog item={dialog} close={() => setDialog(null)} />}
    </div>
  );
}
