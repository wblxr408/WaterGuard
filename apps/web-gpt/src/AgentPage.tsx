import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { RunEvent } from "@waterguard/contracts";
import { missingText } from "./AgentRun";
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
import type { FlowNode, NodeKind, NodeState } from "./agent-flow";
import type { Candidate, Workspace } from "./data";
import {
  actionText,
  conclusionText,
  engineStatus,
  eventText,
  gates,
  loadWorkspace,
  num,
  runStatusText,
  statusText,
  time,
} from "./data";
import Icon from "./Icon";
import "./agent-page.css";

/* 智能体页面：Dify 式工作流画布。
   - 画布：开始 → 任务建模 → 冻结任务 → 方案规划 → 水力仿真 → 独立验收 → 达标？（否：回到方案规划）
     → 反例审查 → 打包证据 → 结束。节点按类型着色；由 Agnes 承担的三个角色在 AGH 未接入时显示为虚线框。
   - 回放：按运行事件逐条点亮节点和连线；点节点，右侧面板显示它做什么、能调用的工具、权限边界和本次运行的数据。
   - 下方保留设计细节（角色与权限、工具契约、执行流程、边界与失败处理、接入进度，出自 docs/Agent设计.md）。
   AGH 未接入前，唯一的运行记录是开发用的人工样例（Fixture），页面如实标注；不补造任何调用、失败或数值。 */

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
  benchmark: "固定脚本",
  replay: "回放",
  live: "实时",
};
const kindText: Record<NodeKind, string> = {
  start: "开始",
  llm: "智能体角色",
  gateway: "网关",
  tool: "工具",
  condition: "条件",
  end: "结束",
};
const kindIcon: Record<NodeKind, string> = {
  start: "play",
  llm: "agent",
  gateway: "shield",
  tool: "cube",
  condition: "compare",
  end: "check",
};
const stateText: Record<NodeState, string> = {
  idle: "未执行",
  running: "进行中",
  ok: "完成",
  fail: "未通过",
  wait: "待完成",
  off: "未接入",
};
/** 建议预算（docs/Agent设计.md 第 8 节；实测后确定，不是性能承诺） */
const BUDGET = { simulations: 40, rounds: 4 };

/* ---------------------------------------------------------------- 设计细节（出自 docs/Agent设计.md） */
const ROLES = [
  {
    name: "任务建模",
    input: "用户需求、网络目录、来源",
    decide: "解释字段、标出歧义、提出草案",
    output: "TaskProposal",
    bound: "不修改已冻结条件，不隐去未知项",
    when: "开始或修订时调用一次",
  },
  {
    name: "方案规划",
    input: "TaskSpec、动作目录、工具结果",
    decide: "选择生成策略、动作组合及修正方向",
    output: "ActionPlan、DecisionRecord",
    bound: "不降阈值，不写入验证通过",
    when: "贯穿搜索与修正",
  },
  {
    name: "反例审查",
    input: "候选、验证及数据引用、已测场景",
    decide: "指出缺失证据，选择域内扰动复验",
    output: "StressTestRequest、ReviewFinding",
    bound: "不改原候选、任务或授权范围",
    when: "有希望的名义候选出现后介入",
  },
];
const TOOLS: [string, string, string, string][] = [
  ["wg_inspect_network", "network_ref", "摘要、动作目录、来源", "全部"],
  ["wg_propose_task", "task_id、proposal", "冻结快照或澄清字段", "任务建模"],
  [
    "wg_generate_candidates",
    "task_ref、strategy、candidate_limit",
    "候选引用、生成诊断",
    "规划",
  ],
  [
    "wg_submit_plan",
    "task_ref、actions、parent_candidate_id?",
    "候选或非法动作错误",
    "规划",
  ],
  [
    "wg_simulate_candidate",
    "task_ref、candidate_id、perturbation_id?",
    "产物引用或作业句柄",
    "规划；审查仅复验已有候选",
  ],
  ["wg_get_simulation", "simulation_id", "状态与结果引用", "规划、审查"],
  ["wg_verify_candidate", "simulation_id", "VerificationReport", "规划、审查"],
  [
    "wg_create_perturbation",
    "task_ref、candidate_id、parameters",
    "扰动场景或越界错误",
    "审查",
  ],
  [
    "wg_compare_candidates",
    "task_ref、candidate_ids",
    "同口径指标与 Pareto 候选",
    "规划、审查",
  ],
  [
    "wg_build_evidence",
    "run_id、selected_candidate_id?",
    "证据包及准入结果",
    "规划",
  ],
];
const toolSpec = new Map(TOOLS.map((t) => [t[0], t]));
const FLOW: [string, string][] = [
  ["宿主", "API 建立运行记录并启动 AGH，后台负责生命周期与治理。"],
  ["任务建模", "调用 wg_inspect_network，然后 wg_propose_task。"],
  [
    "网关",
    "检查草案：输入完整且在授权内时冻结 TaskSpec；有实质歧义进入 NEEDS_INPUT，用户答复后恢复。",
  ],
  [
    "方案规划",
    "用 wg_submit_plan 提交故障基线的空动作方案，再调用 wg_simulate_candidate、wg_verify_candidate。",
  ],
  ["Agnes", "选择 wg_generate_candidates 的策略，或直接提出 ActionPlan。"],
  ["AGH", "调用仿真和验证；工具返回指标、失败类型、最差节点/时段及产物引用。"],
  ["Agnes", "根据反馈修改动作或策略，再发起下一轮；记录父候选和变更原因。"],
  [
    "反例审查",
    "调用 wg_create_perturbation，再仿真和验证；规划者据此决定是否修正。",
  ],
  [
    "Agnes",
    "选择候选，调用 wg_build_evidence；后端校验准入条件，Agnes 引用证据形成结论。",
  ],
  ["宿主", "记录终止原因和结论范围：可行、未找到、预算结束、错误或取消。"],
];
const BOUNDS: [string, string, string][] = [
  ["硬约束", "TaskSpec 只读、constraint_hash 校验", "TASK_REVISION_MISMATCH"],
  ["动作", "设施目录、参数、时序和物理映射", "INVALID_ACTION"],
  ["角色", "服务端能力矩阵", "PERMISSION_DENIED"],
  ["结果", "原始产物复算、版本及完整性检查", "ARTIFACT_INVALID"],
  ["资源", "派工前原子预留模型/仿真预算", "BUDGET_EXHAUSTED"],
  ["数据", "结构化解析，内容不改变指令权限", "INPUT_INVALID"],
  ["模型", "固定 Agnes 模型与提供商允许列表", "MODEL_NOT_ALLOWED"],
  ["结论", "推荐准入与覆盖范围分离", "ELIGIBILITY_INCOMPLETE"],
];
const RECOVERY = [
  `起始预算建议：最多 ${BUDGET.rounds} 轮修正、${BUDGET.simulations} 次仿真（含基线、失败尝试和扰动）、2 个并发计算进程。`,
  "模型格式错误最多修复一次，仍失败则停止该阶段。",
  "工具临时错误按声明重试；请求幂等，实际尝试计入资源消耗。",
  "仿真不收敛属于计算无效，不计通过，也不证明无解。",
  "预算结束返回最佳已验证候选和未完成项；没有候选则说明未找到。",
  "审查失败保留名义验证，不标记复验完成。",
  "取消同时传给 AGH 与计算进程；迟到结果归档，不覆盖取消状态。",
];
const PROBES = [
  "核验赛事指定 AGH 的安装方式、版本、Agnes 模型标识、认证与一次模型调用",
  "通过 AGH 调用最小网络检查工具，再接入小型真实水力仿真",
  "在核心会话中连续完成检查、调用、反馈处理与验证，反馈重新进入 Agnes 上下文",
  "验证原生子代理、上下文隔离，以及可信角色身份如何绑定工具权限",
  "验证日志导出、工具标识、恢复与取消；缺原生调用标识时建立可核对的映射",
  "核查实际模型、提供商、工具与扩展配置，覆盖后台总结、审查等模型调用",
];
const DESIGN_TABS = [
  "角色与权限",
  "工具契约",
  "执行流程",
  "边界与失败处理",
  "接入进度",
] as const;

/* ---------------------------------------------------------------- 数据：人工样例的运行记录与事件流 */
function useRun() {
  const [w, setW] = useState<Workspace | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [state, setState] = useState<
    "loading" | "streaming" | "done" | "error"
  >("loading");
  useEffect(() => {
    const c = new AbortController();
    loadWorkspace("fixture", "", c.signal)
      .then((x) => !c.signal.aborted && setW(x))
      .catch(() => !c.signal.aborted && setState("error"));
    return () => c.abort();
  }, []);
  useEffect(() => {
    if (!w?.run) return;
    setState("streaming");
    const es = new EventSource(`${w.base}/api/runs/${w.run.run_id}/events`);
    const onEvent = (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data) as RunEvent;
        if (ev.mode !== w.mode) throw new Error("mode");
        setEvents((rows) =>
          rows.some((r) => r.sequence === ev.sequence)
            ? rows
            : [...rows, ev].sort((a, b) => a.sequence - b.sequence),
        );
        if (ev.type === "run.completed") {
          setState("done");
          es.close();
        }
      } catch {
        setState("error");
        es.close();
      }
    };
    for (const name of Object.keys(eventText))
      es.addEventListener(name, onEvent as EventListener);
    es.onerror = () => {
      setState((s) => (s === "done" ? s : "error"));
      es.close();
    };
    return () => es.close();
  }, [w]);
  return { w, events, state };
}

const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("zh-CN", { hour12: false });

/* ---------------------------------------------------------------- 页面 */
export default function AgentPage({ inspect }: { inspect: Inspect }) {
  const { w, events, state } = useRun();
  const run = w?.run ?? null;
  const byId = useMemo(
    () => new Map((w?.candidates ?? []).map((c) => [c.id, c])),
    [w],
  );
  const [step, setStep] = useState<number | null>(null); // 回放时已发生的事件数；null 为全部
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<string>("plan");
  const [panel, setPanel] = useState<"node" | "trace">("node");
  const [tab, setTab] = useState<(typeof DESIGN_TABS)[number]>("角色与权限");
  const timer = useRef(0);

  const shown = step == null ? events : events.slice(0, step);
  const flow = useMemo(
    () => flowState(shown, run, byId, false),
    [shown, run, byId],
  );

  // 回放：每 1.1 秒发生一条事件，点亮对应节点，并把右侧面板切到这个节点
  const replay = () => {
    clearInterval(timer.current);
    let n = 0;
    setStep(0);
    setPlaying(true);
    setSelected("start");
    timer.current = window.setInterval(() => {
      n += 1;
      const e = events[n - 1];
      if (e) setSelected(nodeOfEvent[e.type]);
      if (n >= events.length) {
        clearInterval(timer.current);
        setStep(null);
        setPlaying(false);
      } else setStep(n);
    }, 1100);
  };
  const stop = () => {
    clearInterval(timer.current);
    setStep(null);
    setPlaying(false);
  };
  useEffect(() => () => clearInterval(timer.current), []);

  return (
    <div className="page-enter agent-page">
      <div className="section-intro">
        <h1>智能体</h1>
        <p>
          一次爆管处置的工作流：三个角色由 Agnes
          承担，仿真、验收和证据由工具完成；点节点看它做什么、能调用什么、这次运行交出了什么。
        </p>
      </div>

      <div className="notice agent-honesty" role="note">
        <Icon name="warning" size={18} />
        <p>
          <b>AGH 尚未接入（同学 2 负责）。</b>
          工作流里的运行记录是开发用的人工样例（Fixture），不是智能体的真实运行，网络和数值也是人工设定的；虚线框的三个角色节点因此显示「未接入」。接入后，同一张工作流显示真实运行（Replay
          / Live）。
        </p>
      </div>

      {state === "error" && !w ? (
        <div className="error-state" role="alert">
          <h2>读不到运行记录</h2>
          <p>
            样例服务（端口 8000）没有响应。启动后刷新；界面不会用虚构数据代替。
          </p>
        </div>
      ) : !w || !run ? (
        <div className="loading-state" role="status">
          <span className="loader" />
          <h2>正在读取运行记录</h2>
        </div>
      ) : (
        <section className="wf" aria-label="智能体工作流">
          <header className="wf-bar">
            <div className="wf-title">
              <span className="wf-app">
                <Icon name="agent" size={18} />
              </span>
              <div>
                <b>爆管处置工作流</b>
                <small>
                  运行 <span className="mono">{run.run_id}</span> ·{" "}
                  {modeText[run.mode] ?? run.mode} · 任务第{" "}
                  {run.task_ref.revision} 版
                </small>
              </div>
            </div>
            <div className="wf-status">
              <span className={`wf-pill ${playing ? "running" : "done"}`}>
                <i />
                {playing
                  ? `回放中 ${step ?? 0}/${events.length}`
                  : `${runStatusText[run.status] ?? run.status} · ${conclusionText[run.conclusion] ?? run.conclusion}`}
              </span>
              <span className="wf-meta">
                仿真 {run.budget_usage.simulations} 次 · 模型调用{" "}
                {run.budget_usage.model_calls ?? "—"} · 修改 {flow.loops} 轮
              </span>
              {playing ? (
                <button className="button secondary" onClick={stop}>
                  <Icon name="pause" size={15} />
                  停止
                </button>
              ) : (
                <button
                  className="button primary"
                  onClick={replay}
                  disabled={!events.length}
                >
                  <Icon name="play" size={15} />
                  回放运行
                </button>
              )}
            </div>
          </header>

          <div className="wf-body">
            <FlowCanvas
              flow={flow}
              selected={selected}
              onSelect={(id) => {
                setSelected(id);
                setPanel("node");
              }}
            />
            {/* 窄屏：画布太小，按流程顺序竖排节点 */}
            <ol className="wf-list" aria-label="工作流节点">
              {NODES.map((n) => {
                const info = flow.nodes[n.id];
                return (
                  <li key={n.id}>
                    <button
                      className={`wf-li ${n.kind} ${info.state} ${selected === n.id ? "sel" : ""} ${n.agent ? "agent" : ""}`}
                      onClick={() => {
                        setSelected(n.id);
                        setPanel("node");
                      }}
                    >
                      <span className={`wf-icon ${n.kind}`}>
                        <Icon name={kindIcon[n.kind]} size={15} />
                      </span>
                      <span className="wf-text">
                        <b>{n.title}</b>
                        <small>{n.sub}</small>
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
            <aside className="wf-panel" aria-label="详情">
              <div className="wf-panel-tabs" role="tablist">
                <button
                  role="tab"
                  aria-selected={panel === "node"}
                  className={panel === "node" ? "on" : ""}
                  onClick={() => setPanel("node")}
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
              </div>
              {panel === "node" ? (
                <NodePanel
                  node={NODES.find((n) => n.id === selected)!}
                  info={flow.nodes[selected]}
                  events={shown.filter((e) => nodeOfEvent[e.type] === selected)}
                  w={w}
                  byId={byId}
                  inspect={inspect}
                />
              ) : (
                <ol className="wf-trace">
                  {shown.map((e) => (
                    <li key={e.sequence} className={e.origin}>
                      <button
                        onClick={() => {
                          setSelected(nodeOfEvent[e.type]);
                          setPanel("node");
                        }}
                      >
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
          </div>
          {run.missing_items.length > 0 && (
            <p className="wf-missing">
              <Icon name="warning" size={15} />
              这次运行还缺：
              {run.missing_items.map((m) => missingText[m] ?? m).join("、")}
            </p>
          )}
        </section>
      )}

      {/* 设计细节 */}
      <section className="ag-design" aria-label="设计细节">
        <header>
          <h2>设计细节</h2>
          <span>
            出自 docs/Agent设计.md；工具名和接口是项目设计，接入后按实测锁定
          </span>
        </header>
        <div className="ag-tabs" role="tablist">
          {DESIGN_TABS.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={tab === t ? "on" : ""}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="ag-tab-body" role="tabpanel">
          {tab === "角色与权限" && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>角色</th>
                    <th>输入</th>
                    <th>可作出的决定</th>
                    <th>输出</th>
                    <th>权限边界</th>
                    <th>何时介入</th>
                  </tr>
                </thead>
                <tbody>
                  {ROLES.map((r) => (
                    <tr key={r.name}>
                      <td>
                        <b>{r.name}</b>
                      </td>
                      <td>{r.input}</td>
                      <td>{r.decide}</td>
                      <td className="mono">{r.output}</td>
                      <td className="ag-bound">{r.bound}</td>
                      <td>{r.when}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {tab === "工具契约" && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>工具</th>
                      <th>主要参数</th>
                      <th>结果</th>
                      <th>可调用的角色</th>
                    </tr>
                  </thead>
                  <tbody>
                    {TOOLS.map(([name, args, result, roles]) => (
                      <tr key={name}>
                        <td className="mono">{name}</td>
                        <td className="mono">{args}</td>
                        <td>{result}</td>
                        <td>{roles}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="ag-note">
                每个工具统一返回
                status、code、data_ref、summary、violations、retryable、remaining_budget、correlation_id；大数组放在产物里，摘要能定位到节点与时段。真实角色身份由宿主和服务端凭证绑定，不信任模型自报。
              </p>
            </>
          )}
          {tab === "执行流程" && (
            <>
              <ol className="ag-flow">
                {FLOW.map(([who, what], i) => (
                  <li key={i}>
                    <span className="ag-who">{who}</span>
                    <p>{what}</p>
                  </li>
                ))}
              </ol>
              <p className="ag-note">
                禁止一个工具包办任务解释、尝试、审查和报告；已经满足目标时可以提前结束，不为展示多轮而制造失败。
              </p>
            </>
          )}
          {tab === "边界与失败处理" && (
            <div className="ag-two">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>边界</th>
                      <th>强制机制</th>
                      <th>越界时返回</th>
                    </tr>
                  </thead>
                  <tbody>
                    {BOUNDS.map(([b, how, code]) => (
                      <tr key={b}>
                        <td>
                          <b>{b}</b>
                        </td>
                        <td>{how}</td>
                        <td className="mono">{code}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="ag-list">
                {RECOVERY.map((r) => (
                  <li key={r}>{r}</li>
                ))}
                <li>
                  例：模型提议把最低压力从 20 m 降到 14
                  m，网关拒绝；继续找合法方案，或生成独立的任务修订建议交给用户决定。
                </li>
              </ul>
            </div>
          )}
          {tab === "接入进度" && (
            <>
              <ol className="ag-probes">
                {PROBES.map((p) => (
                  <li key={p}>
                    <span className="badge pending">待实测</span>
                    <p>{p}</p>
                  </li>
                ))}
              </ol>
              <p className="ag-note">
                六项探针由同学 2 在 AGH
                环境里实测并保存版本、配置和输出；原生子代理不可用时，降级为单会话的三个角色阶段并如实标记。状态目前一律「待实测」，前端没有它们的结果。
              </p>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

/* ---------------------------------------------------------------- 画布：节点、连线、平移缩放 */
function FlowCanvas({
  flow,
  selected,
  onSelect,
}: {
  flow: ReturnType<typeof flowState>;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(
    null,
  );

  // 适应画布宽度：整张流程放得下，最大不放大到 1 倍以上
  const fit = () => {
    const el = wrapRef.current;
    if (!el) return;
    const k = Math.min(
      1,
      (el.clientWidth - 24) / STAGE_W,
      (el.clientHeight - 24) / STAGE_H,
    );
    setView({
      k,
      x: (el.clientWidth - STAGE_W * k) / 2,
      y: (el.clientHeight - STAGE_H * k) / 2,
    });
  };
  useLayoutEffect(() => {
    fit();
    const ro = new ResizeObserver(fit);
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);
  const zoom = (f: number) => {
    const el = wrapRef.current;
    if (!el) return;
    setView((v) => {
      const k = Math.max(0.4, Math.min(1.6, v.k * f));
      const cx = el.clientWidth / 2,
        cy = el.clientHeight / 2;
      return {
        k,
        x: cx - ((cx - v.x) * k) / v.k,
        y: cy - ((cy - v.y) * k) / v.k,
      };
    });
  };
  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest(".wf-node, .wf-zoom")) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    setView((v) => ({
      ...v,
      x: d.vx + e.clientX - d.x,
      y: d.vy + e.clientY - d.y,
    }));
  };
  const onUp = () => (drag.current = null);

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
    <div
      className="wf-canvas"
      ref={wrapRef}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      <div
        className="wf-stage"
        style={{
          width: STAGE_W,
          height: STAGE_H,
          transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`,
        }}
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
      <div className="wf-zoom">
        <button onClick={() => zoom(1.2)} aria-label="放大">
          <Icon name="plus" size={15} />
        </button>
        <button onClick={() => zoom(1 / 1.2)} aria-label="缩小">
          <Icon name="minus" size={15} />
        </button>
        <button onClick={fit} aria-label="适应画布">
          <Icon name="fit" size={15} />
        </button>
      </div>
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
  node: FlowNode;
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
      <p className="wf-does">{node.does}</p>
      {node.bound && (
        <p className="wf-bound">
          <Icon name="shield" size={14} />
          不能做：{node.bound}
        </p>
      )}
      {node.tools && (
        <div className="wf-tools">
          <h4>可调用的工具</h4>
          <ul>
            {node.tools.map((t) => {
              const spec = toolSpec.get(t);
              return (
                <li key={t}>
                  <code>{t}</code>
                  {spec && (
                    <span>
                      参数 {spec[1]} → {spec[2]}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {node.agent && (
        <p className="wf-agent-off">
          这个角色由 Agnes 承担。AGH
          接入前没有它的真实调用记录，这里不显示、也不补造。
        </p>
      )}
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
              <dd>{c.plan.reason || "—"}</dd>
            </div>
          </dl>
        </>
      )}
      {e.type === "simulation.completed" && c && (
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
      {e.type === "verification.completed" && c && (
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
              {c.verification.recommendation_blockers.join("、")}
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
      <p className="wf-ids">
        原生调用号：
        {e.native_tool_call_ref ??
          `无（${e.native_reference_missing_reason ?? "未说明原因"}）`}
      </p>
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
  return (
    <ol className="ag-gates" aria-label="五层准入">
      {gates.map(([key, label]) => {
        const st = c.verification.admission[key];
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
