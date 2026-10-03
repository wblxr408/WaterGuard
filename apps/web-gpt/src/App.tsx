import { useEffect, useRef, useState } from "react";
import type { Violation } from "@waterguard/contracts";
import {
  actionText,
  caseNames,
  download,
  loadWorkspace,
  num,
  sources,
  statusText,
  time,
} from "./data";
import type { Candidate, Source, Workspace } from "./data";
import Icon from "./Icon";
import { Network, NodeChart } from "./Network";
import type { Entity } from "./Network";
import {
  Admission,
  Compare,
  Evidence,
  FixtureEvents,
  Modeling,
  Violations,
} from "./Pages";

type Page = "overview" | "compare" | "model" | "evidence";
const navigation: { id: Page; label: string; icon: string; english: string }[] =
  [
    {
      id: "overview",
      label: "应急工作台",
      icon: "network",
      english: "Operations",
    },
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
          <span className="eyebrow">SOURCE INSPECTOR</span>
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
        <span>接口原始字段 · 不修改服务端数据</span>
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
  const node = w.scene.nodes.find((n) => n.id === entity.id),
    link = w.scene.links.find((l) => l.id === entity.id);
  const sample = frame?.nodes.find((n) => n.node_id === entity.id),
    ls = frame?.links.find((l) => l.link_id === entity.id);
  const ratio =
    sample && sample.expected_demand_m3s > 0
      ? sample.delivered_demand_m3s / sample.expected_demand_m3s
      : null;
  const valid =
    c.verification.validity === "valid" &&
    c.verification.admission.simulation_valid === "pass";
  return (
    <aside className="inspector">
      <div className="inspector-top">
        <span className="eyebrow">ENTITY INSPECTOR</span>
        <span className="badge neutral">
          {entity.kind === "node"
            ? node?.kind === "junction"
              ? "用水节点"
              : node?.kind === "tank"
                ? "水箱"
                : "水源"
            : link?.kind === "pump"
              ? "水泵"
              : "管段"}
        </span>
      </div>
      <div className="entity-title">
        <span className="entity-icon">
          <Icon name={entity.kind === "node" ? "target" : "link"} size={24} />
        </span>
        <div>
          <h2>
            {entity.kind === "node" ? "节点" : "管段"} <b>{entity.id}</b>
          </h2>
          <span>{frame ? `当前时刻 ${time(frame.time_s)}` : "无时序数据"}</span>
        </div>
      </div>
      <dl className="detail-list entity-details">
        {entity.kind === "node" ? (
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
            <div>
              <dt>期望需水</dt>
              <dd>
                {valid ? num(sample?.expected_demand_m3s, 5) : "—"}{" "}
                <small>m³/s</small>
              </dd>
            </div>
            <div>
              <dt>实际供水</dt>
              <dd>
                {valid ? num(sample?.delivered_demand_m3s, 5) : "—"}{" "}
                <small>m³/s</small>
              </dd>
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
              <dt>管段状态</dt>
              <dd>
                {ls
                  ? { open: "开启", closed: "关闭", broken: "断裂" }[ls.status]
                  : "无数据"}
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
          </>
        )}
      </dl>
      <button
        className="text-button inspector-source"
        onClick={() =>
          inspect(`${entity.id} · 当前时序`, {
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
      <div
        className={`inspector-verdict ${c.verification.admission.recommendation_eligible}`}
      >
        <Icon name="shield" size={19} />
        <div>
          <strong>
            {c.verification.admission.recommendation_eligible === "pass"
              ? "可推荐"
              : c.verification.admission.recommendation_eligible === "pending"
                ? "推荐待验证"
                : "当前不可推荐"}
          </strong>
          <p>以服务端五层准入为准</p>
        </div>
      </div>
    </aside>
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
    reference = w.candidates[0].verification.metrics;
  const sameTask = w.candidates.every(
    (x) => x.task.constraint_hash === w.candidates[0].task.constraint_hash,
  );
  const improve =
    sameTask && c !== w.candidates[0] && reference.shortage_m3 > 0
      ? ((reference.shortage_m3 - metrics.shortage_m3) /
          reference.shortage_m3) *
        100
      : null;
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
          <span className="eyebrow">WATER SUPPLY OPERATIONS</span>
          <h1>
            供水应急工作台
            <span className="title-line" />
          </h1>
          <p>定位故障，比较处置，用仿真结果验证每一步。</p>
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
          <span>{w.scene.network_id.toUpperCase()}</span>
          <span>
            评估 {time(c.task.time_window.evaluation_start_s)} –{" "}
            {time(c.task.time_window.end_s)}
          </span>
          <span>压力 ≥ {num(c.task.hard_constraints.min_pressure_m, 0)} m</span>
        </div>
        <button
          className="text-button"
          onClick={() => inspect("冻结任务", c.task)}
        >
          任务详情
          <Icon name="chevron" size={15} />
        </button>
      </div>
      <div className="metric-strip">
        {[
          {
            label: "缺供体积",
            value: num(metrics.shortage_m3),
            unit: "m³",
            icon: "water",
            detail:
              improve != null
                ? `较首个候选减少 ${num(improve, 1)}%`
                : "评估窗口内累计缺供",
            positive: improve != null && improve > 0,
          },
          {
            label: "总体供需比",
            value:
              metrics.supply_ratio == null
                ? "—"
                : num(metrics.supply_ratio * 100),
            unit: "%",
            icon: "pulse",
            detail: "按用户供需体积计算",
            positive: false,
          },
          {
            label: "最低节点压力",
            value: num(metrics.min_pressure_m),
            unit: "m",
            icon: "target",
            detail: `工程要求 ≥ ${num(c.task.hard_constraints.min_pressure_m, 0)} m`,
            positive: false,
          },
          {
            label: "处置动作",
            value: String(c.plan.actions.length),
            unit: "次",
            icon: "layers",
            detail: `操作成本 ${num(metrics.action_cost, 0)} · 合成单位`,
            positive: false,
          },
        ].map((m) => (
          <div className="metric" key={m.label}>
            <div>
              <span>{m.label}</span>
              <Icon name={m.icon} size={18} />
            </div>
            <strong>
              {m.value}
              <small>{m.unit}</small>
            </strong>
            <p className={m.positive ? "positive" : ""}>
              {m.positive && <span>↘</span>}
              {m.detail}
            </p>
          </div>
        ))}
      </div>
      <div className="workspace-bar">
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
        <span className="muted">
          {c.result ? "引擎计算完成" : "人工样例数据"}
          <Icon name="check" size={14} />
        </span>
      </div>
      <div className="workspace-grid">
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
        </span>
      </div>
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
        <Admission candidate={c} />
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
            <span className="eyebrow">MODEL STUDIO</span>
            <h2>让建模成果，进入工作台。</h2>
            <p>泵站设备与管道剖面 · Blender 展示预留</p>
            <span className="text-button">
              打开建模展示
              <Icon name="arrow" size={16} />
            </span>
          </div>
          <img src="/images/pump-concept-v2.png" alt="水泵设备 AI 概念图" />
          <span className="teaser-caption">AI 概念图</span>
        </button>
      </div>
      <Violations candidate={c} onSelect={onViolation} />
      <FixtureEvents w={w} inspect={inspect} />
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [source, setSource] = useState<Source>("pair");
  const [selection, setSelection] = useState("");
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
  const inspect = (title: string, data: unknown) => setDialog({ title, data });
  useEffect(() => {
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
  }, [source, selection, retry]);
  const c =
    w?.candidates.find((x) => x.id === candidateId) ?? w?.candidates.at(-1);
  const openPage = (p: Page) => {
    setPage(p);
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
  const currentNav = navigation.find((n) => n.id === page)!;
  return (
    <div className={`app ${focus ? "focus-mode" : ""}`}>
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
              openPage("overview");
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
          <nav aria-label="主要导航">
            {navigation.map((n) => (
              <button
                key={n.id}
                aria-current={page === n.id ? "page" : undefined}
                className={page === n.id ? "active" : ""}
                onClick={() => openPage(n.id)}
              >
                <Icon name={n.icon} size={18} />
                <span>{n.label}</span>
              </button>
            ))}
          </nav>
          <div className="global-header-end">
            <span className="brand-caption">HYDRAULICS × INTELLIGENCE</span>
            <button
              className="icon-button guide-button"
              aria-label="演示与数据说明"
              onClick={() =>
                inspect("演示与数据说明", {
                  建议演示路径: [
                    "工作台：定位故障、切换 A/B、检查时间轴",
                    "方案对照：改善与工程准入分别呈现",
                    "建模与仿真：两个简洁演示，实际 Blender 成果待接入",
                    "证据中心：原始验收、物理检查与文件哈希",
                  ],
                  Benchmark: "固定脚本驱动的真实水力计算，不是 AI 决策。",
                  Fixture: "人工开发样例，不证明真实水力行为。",
                  AGH: "尚未接入 Live / Replay 实时作业。",
                  坐标: "示意布局，不代表实际位置与距离。",
                  只读服务: "不创建、修改、执行或取消计算。",
                })
              }
            >
              <Icon name="help" size={19} />
            </button>
          </div>
        </div>
      </header>
      <div className="app-main">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作空间</span>
            <Icon name="chevron" size={13} />
            <strong>{currentNav.label}</strong>
          </div>
          <div className="topbar-actions">
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
          <div className="data-mode-note">
            <Icon name="shield" size={14} />
            {w?.mode === "fixture"
              ? "Fixture · 人工设定的开发样例，不代表真实水力计算"
              : w
                ? "Benchmark · 固定脚本驱动的真实计算，非 AI 实时决策"
                : "正在核验数据来源与运行模式"}
            <span>
              {w ? "只读结果浏览" : `接口端口 ${sources[source].port}`}
            </span>
          </div>
          {w && c && source === "bench" && (
            <div className="benchmark-picker">
              <label htmlFor="benchmark-case">选择真实工况</label>
              <select
                id="benchmark-case"
                value={selection || w.candidates.at(-1)?.simulationId}
                onChange={(e) => setSelection(e.target.value)}
              >
                {w.index?.cases.map((x) => (
                  <option key={x.simulation_id} value={x.simulation_id}>
                    {x.network_id.toUpperCase()} / {x.engine.toUpperCase()} /{" "}
                    {caseNames[x.case] ?? x.case}
                  </option>
                ))}
              </select>
              <span>正常工况与所选工况对照</span>
            </div>
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
              <p className="muted">
                启动对应后端后重试；界面不会用虚构数据替代。
              </p>
            </div>
          ) : !w || !c ? (
            <div className="loading-state" role="status">
              <span className="loader" />
              <h2>正在载入水力计算</h2>
              <p>读取任务、拓扑、候选方案与验收证据…</p>
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
                  {w.scene.network_id.toUpperCase()} · 契约{" "}
                  {c.task.schema_version} · {w.mode.toUpperCase()}
                </span>
                <span>所有推荐结论来自验收接口</span>
              </footer>
            </>
          )}
        </main>
      </div>
      {dialog && <JsonDialog item={dialog} close={() => setDialog(null)} />}
    </div>
  );
}
