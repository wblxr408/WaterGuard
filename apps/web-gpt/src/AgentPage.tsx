import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { RunEvent } from "@waterguard/contracts";
import { PHASES, missingText } from "./AgentRun";
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

/* 智能体页面（详细版）：
   1. 运行概况：运行记录（RunRecord）的模式、状态、阶段、预算、结论、缺失项；
   2. 运行轨迹：运行事件（RunEvent）逐条展开——谁做的、提交了什么方案、仿真和验收结果、原生调用号；
   3. 方案谱系：候选方案的父子关系、提出者与修改理由；
   4. 设计细节：角色与权限、工具契约、执行流程、边界与失败处理、接入进度（出自 docs/Agent设计.md 第 3–8、11 节）。
   AGH 未接入前，唯一的运行记录是开发用的人工样例（Fixture），页面顶部和每条事件都如实标注；
   接入后改读 Replay / Live 运行，结构不变。不补造任何调用、失败或数值。 */

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
  const [open, setOpen] = useState<number | null>(null);
  const [shown, setShown] = useState<number | null>(null); // 逐步回放时已显示的条数；null 为全部
  const [tab, setTab] = useState<(typeof DESIGN_TABS)[number]>("角色与权限");
  const timer = useRef(0);

  // 逐步回放：每 0.9 秒多显示一条并展开它，放完恢复全部显示
  const replay = () => {
    clearInterval(timer.current);
    let n = 0;
    setOpen(null);
    setShown(0);
    timer.current = window.setInterval(() => {
      n += 1;
      setShown(n >= events.length ? null : n);
      setOpen(events[n - 1]?.sequence ?? null);
      if (n >= events.length) clearInterval(timer.current);
    }, 900);
  };
  useEffect(() => () => clearInterval(timer.current), []);
  const visible = shown == null ? events : events.slice(0, shown);
  const phaseAt = run ? PHASES.findIndex(([p]) => p === run.phase) : -1;

  return (
    <div className="page-enter agent-page">
      <div className="section-intro">
        <h1>智能体</h1>
        <p>
          三个角色在 AGH
          里调用工具处理一次爆管；每一步谁做的、交了什么、结果如何，都记在运行记录里。
        </p>
      </div>

      <div className="notice agent-honesty" role="note">
        <Icon name="warning" size={18} />
        <p>
          <b>AGH 尚未接入（同学 2 负责）。</b>
          下面的运行记录是开发用的人工样例（Fixture），不是智能体的真实运行，网络和数值也是人工设定的；
          接入后，同一位置显示真实运行（Replay / Live），页面结构不变。
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
        <>
          {/* 1. 运行概况 */}
          <section className="ag-run" aria-label="运行概况">
            <div className="ag-run-head">
              <div>
                <span className="ag-label">运行</span>
                <b className="mono">{run.run_id}</b>
              </div>
              <span className="badge pending">
                {modeText[run.mode] ?? run.mode}
              </span>
              <span className="ag-meta">
                任务 <span className="mono">{run.task_ref.task_id}</span> · 第{" "}
                {run.task_ref.revision} 版
              </span>
            </div>
            <ol className="ag-phases" aria-label="运行阶段">
              {PHASES.map(([p, label], i) => (
                <li
                  key={p}
                  className={i < phaseAt ? "done" : i === phaseAt ? "now" : ""}
                >
                  <i />
                  <span>{label}</span>
                </li>
              ))}
            </ol>
            <dl className="ag-facts">
              <div>
                <dt>状态</dt>
                <dd>{runStatusText[run.status] ?? run.status}</dd>
              </div>
              <div>
                <dt>结论</dt>
                <dd>{conclusionText[run.conclusion] ?? run.conclusion}</dd>
              </div>
              <div>
                <dt>选定方案</dt>
                <dd>{run.selected_candidate_id ?? "未选定"}</dd>
              </div>
              <div>
                <dt>仿真次数</dt>
                <dd>
                  {run.budget_usage.simulations}
                  <small> / 建议上限 {BUDGET.simulations}</small>
                </dd>
              </div>
              <div>
                <dt>模型调用</dt>
                <dd>{run.budget_usage.model_calls ?? "—（未接入）"}</dd>
              </div>
              <div>
                <dt>耗时</dt>
                <dd>{num(run.budget_usage.wall_time_s, 0)} s</dd>
              </div>
              <div>
                <dt>AGH 会话</dt>
                <dd>{run.agh_session_ref ?? "未接入"}</dd>
              </div>
              <div>
                <dt>模型配置</dt>
                <dd>{run.model_config_ref ?? "未配置"}</dd>
              </div>
            </dl>
            {run.missing_items.length > 0 && (
              <p className="ag-missing">
                <Icon name="warning" size={15} />
                还缺：
                {run.missing_items.map((m) => missingText[m] ?? m).join("、")}
              </p>
            )}
          </section>

          <div className="ag-grid">
            {/* 2. 运行轨迹 */}
            <section className="ag-trace" aria-label="运行轨迹">
              <header>
                <div>
                  <h2>运行轨迹</h2>
                  <span>
                    {events.length} 个事件 ·{" "}
                    {state === "done"
                      ? "已读完"
                      : state === "error"
                        ? "事件流中断"
                        : "读取中"}
                  </span>
                </div>
                <div className="ag-trace-actions">
                  <button
                    className="button secondary"
                    onClick={replay}
                    disabled={!events.length}
                  >
                    <Icon name="play" size={15} />
                    逐步回放
                  </button>
                  <button
                    className="button secondary"
                    onClick={() => {
                      clearInterval(timer.current);
                      setShown(null);
                    }}
                    disabled={shown == null}
                  >
                    全部显示
                  </button>
                </div>
              </header>
              <ol className="ag-events">
                {visible.map((e, i) => (
                  <EventRow
                    key={e.sequence}
                    e={e}
                    i={i}
                    c={e.candidate_id ? byId.get(e.candidate_id) : undefined}
                    w={w}
                    open={open === e.sequence}
                    onToggle={() =>
                      setOpen(open === e.sequence ? null : e.sequence)
                    }
                    inspect={inspect}
                  />
                ))}
              </ol>
            </section>

            {/* 3. 方案谱系 */}
            <aside className="ag-lineage" aria-label="方案谱系">
              <h2>方案谱系</h2>
              <p className="ag-sub">每个方案的提出者、父方案和修改理由</p>
              <ol>
                {w.candidates.map((c, i) => (
                  <li key={c.id}>
                    {i > 0 && (
                      <p className="ag-edge">
                        <Icon name="down" size={15} />
                        {c.plan.reason || "（未写理由）"}
                      </p>
                    )}
                    <article>
                      <header>
                        <b>{c.name}</b>
                        <span className="mono">{c.id}</span>
                      </header>
                      <dl>
                        <div>
                          <dt>提出者</dt>
                          <dd>{sourceText[c.plan.source] ?? c.plan.source}</dd>
                        </div>
                        <div>
                          <dt>父方案</dt>
                          <dd className="mono">
                            {c.plan.parent_candidate_id ?? "—"}
                          </dd>
                        </div>
                        <div>
                          <dt>缺水</dt>
                          <dd>{num(c.verification.metrics.shortage_m3)} m³</dd>
                        </div>
                        <div>
                          <dt>最低压力</dt>
                          <dd>
                            {num(c.verification.metrics.min_pressure_m)} m
                          </dd>
                        </div>
                      </dl>
                      <Gates c={c} />
                    </article>
                  </li>
                ))}
              </ol>
              <p className="ag-note">
                真实管网（Net3）的 A/B
                方案在「方案对照」页；那两份是工程预设，不是智能体提出的。
              </p>
            </aside>
          </div>
        </>
      )}

      {/* 4. 设计细节 */}
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

/* ---------------------------------------------------------------- 一条事件 */
function EventRow({
  e,
  i,
  c,
  w,
  open,
  onToggle,
  inspect,
}: {
  e: RunEvent;
  i: number;
  c: Candidate | undefined;
  w: Workspace;
  open: boolean;
  onToggle: () => void;
  inspect: Inspect;
}) {
  const run = w.run!;
  return (
    <li
      className={`ag-event ${e.origin} ${open ? "open" : ""}`}
      style={{ "--i": i } as CSSProperties}
    >
      <button className="ag-event-head" onClick={onToggle} aria-expanded={open}>
        <span className="ag-seq">{String(e.sequence).padStart(2, "0")}</span>
        <span className={`ag-origin ${e.origin}`}>
          {originText[e.origin] ?? e.origin}
        </span>
        <span className="ag-what">
          <b>{eventText[e.type] ?? e.type}</b>
          <small className="mono">
            {e.type}
            {e.candidate_id ? ` · ${e.candidate_id}` : ""}
            {e.simulation_id ? ` · ${e.simulation_id}` : ""}
          </small>
        </span>
        <time>{clock(e.occurred_at)}</time>
        <Icon name="chevron" size={15} />
      </button>
      {open && (
        <div className="ag-event-body">
          {e.type === "candidate.submitted" && c && (
            <>
              <h4>提交的方案</h4>
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
                <p>不操作任何设施（故障基线）。</p>
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
                <div className="wide">
                  <dt>方案哈希</dt>
                  <dd className="mono">{c.plan.plan_hash}</dd>
                </div>
              </dl>
            </>
          )}
          {e.type === "simulation.completed" && c && (
            <>
              <h4>仿真结果</h4>
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
              <h4>独立验收</h4>
              <Gates c={c} />
              {c.verification.violations.length > 0 && (
                <div className="table-wrap ag-violations">
                  <table>
                    <thead>
                      <tr>
                        <th>违反项</th>
                        <th>节点</th>
                        <th>时段</th>
                        <th>实测</th>
                        <th>要求</th>
                      </tr>
                    </thead>
                    <tbody>
                      {c.verification.violations.slice(0, 8).map((v, k) => (
                        <tr key={k}>
                          <td>
                            {v.code === "MIN_PRESSURE"
                              ? "压力不足"
                              : "供需比不足"}
                          </td>
                          <td className="mono">{v.node_id}</td>
                          <td>
                            {time(v.start_s)}–{time(v.end_s)}
                          </td>
                          <td>
                            {v.unit === "ratio"
                              ? `${num(v.observed * 100)}%`
                              : `${num(v.observed)} m`}
                          </td>
                          <td>
                            {v.unit === "ratio"
                              ? `≥ ${num(v.threshold * 100, 0)}%`
                              : `≥ ${num(v.threshold, 0)} m`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="ag-meta">
                验收程序{" "}
                <span className="mono">{c.verification.verifier_version}</span>
                {c.verification.recommendation_blockers.length > 0 &&
                  ` · 不能推荐的原因：${c.verification.recommendation_blockers.join("、")}`}
              </p>
            </>
          )}
          {(e.type === "run.started" || e.type === "run.completed") && (
            <>
              <h4>{e.type === "run.started" ? "建立运行" : "运行结束"}</h4>
              <dl className="ag-kv">
                <div>
                  <dt>任务版本</dt>
                  <dd>第 {e.task_revision} 版</dd>
                </div>
                {e.type === "run.completed" && (
                  <>
                    <div>
                      <dt>状态</dt>
                      <dd>{runStatusText[run.status] ?? run.status}</dd>
                    </div>
                    <div>
                      <dt>结论</dt>
                      <dd>
                        {conclusionText[run.conclusion] ?? run.conclusion}
                      </dd>
                    </div>
                  </>
                )}
                <div className="wide">
                  <dt>约束哈希</dt>
                  <dd className="mono">{e.constraint_hash}</dd>
                </div>
              </dl>
            </>
          )}
          <dl className="ag-kv ag-trace-ids">
            <div>
              <dt>原生调用号</dt>
              <dd>
                {e.native_tool_call_ref ??
                  `无（${e.native_reference_missing_reason ?? "未说明原因"}）`}
              </dd>
            </div>
            <div>
              <dt>关联号</dt>
              <dd className="mono">{e.correlation_id}</dd>
            </div>
            <div>
              <dt>载荷</dt>
              <dd className="mono">{e.payload_ref}</dd>
            </div>
          </dl>
          <button
            className="ag-raw"
            onClick={() => inspect(`事件 ${e.sequence} 原文`, e)}
          >
            查看事件原文
            <Icon name="arrow" size={14} />
          </button>
        </div>
      )}
    </li>
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
