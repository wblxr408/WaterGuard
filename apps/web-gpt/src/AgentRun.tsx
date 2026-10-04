import type { RunRecord } from "@waterguard/contracts";
import type { Workspace } from "./data";
import Icon from "./Icon";
import "./agent-run.css";

/** 运行阶段（契约 RunPhase 的顺序）。首页智能体板块也用它。 */
export const PHASES: [RunRecord["phase"], string][] = [
  ["INTAKE", "接收"],
  ["MODELING", "任务建模"],
  ["BASELINE", "基线"],
  ["PLANNING", "方案规划"],
  ["SIMULATING", "仿真"],
  ["VERIFYING", "验证"],
  ["REVIEWING", "反例审查"],
  ["PACKAGING", "打包证据"],
];
const sourceText: Record<string, string> = {
  agh: "智能体（AGH）",
  rule: "规则（工程预设）",
  enumeration: "枚举",
  graph: "图算法",
  fixture: "人工样例",
};
export const missingText: Record<string, string> = {
  native_agh_trace: "原生 AGH 记录",
  agh_host_not_configured: "AGH 会话",
  real_hydraulic_results: "真实水力结果",
  required_stress_tests: "扰动复验",
  "demand-plus-10pct": "需水 +10% 复验",
};
const short = (name: string) => name.split(" · ")[0];

/**
 * 工作台上的智能体运行条：只读运行记录（RunRecord）和方案的提出者（ActionPlan.source / parent_candidate_id）。
 * 没有运行记录时如实说明结果由固定脚本计算；样例记录持续标明「人工样例」；不按计时器编造执行步骤。
 */
export function AgentRunStrip({
  w,
  onOpen,
}: {
  w: Workspace;
  onOpen: () => void;
}) {
  const run = w.run;
  const real = run?.mode === "live" || run?.mode === "replay";
  const reached = run ? PHASES.findIndex(([p]) => p === run.phase) : -1;
  const sources = [...new Set(w.candidates.map((c) => c.plan.source))];
  const revisions = w.candidates.flatMap((c) => {
    const parent = w.candidates.find(
      (p) => p.id === c.plan.parent_candidate_id,
    );
    return parent ? [`${short(c.name)} 由 ${short(parent.name)} 修正`] : [];
  });
  const tone = real ? "real" : run ? "sample" : "none";
  const headline = real
    ? `AGH 会话 ${run?.agh_session_ref ?? "未关联，执行来源待核验"}`
    : run
      ? "人工样例回放，不是智能体的真实运行"
      : "本结果未经智能体，由固定脚本计算";
  return (
    <section className={`agent-strip ${tone}`} aria-label="智能体运行">
      <div className="agent-strip-head">
        <span className="agent-mark">
          <Icon name="pulse" size={17} />
        </span>
        <div>
          <strong>智能体运行</strong>
          <span>{headline}</span>
        </div>
      </div>
      <ol className="agent-phases" aria-label="运行阶段">
        {PHASES.map(([p, label], i) => (
          <li key={p} className={reached >= 0 && i <= reached ? "reached" : ""}>
            {label}
          </li>
        ))}
      </ol>
      <dl className="agent-facts">
        <div>
          <dt>方案提出者</dt>
          <dd>
            {sources.map((s) => sourceText[s] ?? s).join("、")}
            {revisions.length > 0 && ` · ${revisions.join("；")}`}
          </dd>
        </div>
        {run && (
          <>
            <div>
              <dt>预算用量</dt>
              <dd>
                仿真 {run.budget_usage.simulations} 次 · 模型调用{" "}
                {run.budget_usage.model_calls ?? "未记录"}
              </dd>
            </div>
            {run.missing_items.length > 0 && (
              <div>
                <dt>缺少</dt>
                <dd>
                  {run.missing_items.map((m) => missingText[m] ?? m).join("、")}
                </dd>
              </div>
            )}
          </>
        )}
      </dl>
      {run && (
        <button className="text-button" onClick={onOpen}>
          运行记录
          <Icon name="chevron" size={15} />
        </button>
      )}
    </section>
  );
}
