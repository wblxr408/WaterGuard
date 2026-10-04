import type {
  ActionPlan,
  EvidenceManifest,
  Mode,
  NetworkManifest,
  NetworkScene,
  RunRecord,
  Series,
  SimulationResult,
  TaskSpec,
  VerificationReport,
  Admission,
  Metrics,
} from "@waterguard/contracts";

export type Source = "pair" | "bench" | "fixture";
export const recommendationBlockerText: Record<string, string> = {
  benchmark_only: "仅有固定脚本计算结果",
  fixture_only: "仅有人工样例结果",
  native_agh_trace_missing: "缺少原生 AGH 记录",
  required_stress_tests_pending: "扰动复验未完成",
};
export const sources = {
  pair: { base: "/ab", label: "真实 A/B 对照", port: 8002, mode: "benchmark" },
  bench: { base: "/bm", label: "真实场景库", port: 8001, mode: "benchmark" },
  fixture: { base: "/fx", label: "开发样例", port: 8000, mode: "fixture" },
} as const;
export const caseNames: Record<string, string> = {
  normal: "正常供水",
  closure: "管段关闭",
  leak: "管段泄漏",
  rupture: "管道断裂",
  isolation: "区域隔离",
  "pump-cycle": "水泵启停",
  "link-cycle": "管段启闭",
};
/** 方案名按接口返回的动作生成，不写死「启动水泵」之类的含义。 */
export function describePlan(plan: ActionPlan): string {
  const [first, ...rest] = plan.actions;
  if (!first) return "不处置";
  return `${actionText[first.kind]} ${first.link_id}${rest.length ? ` 等 ${plan.actions.length} 项` : ""}`;
}
export interface BenchCase {
  network_id: string;
  case: string;
  engine: string;
  simulation_id: string;
  metrics: Metrics;
  admission: Admission;
}
export interface BenchIndex {
  mode: Mode;
  run_id: string;
  cases: BenchCase[];
  conclusion?: string;
  attribution?: string;
}
export interface Physics {
  passed?: boolean;
  mass_balance_max_abs_m3s?: number;
  mass_balance_tolerance_m3s?: number;
  leak_law_max_relative_error?: number;
  leak_law_tolerance?: number;
  pump_curve_max_fit_error_m?: number;
  pump_curve_fit_tolerance_m?: number;
  interpretation?: string;
}
export interface Mapping {
  segments?: Record<string, string[]>;
  original_junction_ids?: string[];
  compiled_junction_ids?: string[];
  leaks?: unknown[];
  removed_native_controls?: string[];
  closure_semantics?: string;
  rupture_semantics?: string;
  flow_convention?: string;
}
export interface Candidate {
  id: string;
  name: string;
  simulationId: string;
  task: TaskSpec;
  plan: ActionPlan;
  verification: VerificationReport;
  series: Series | null;
  result: SimulationResult | null;
  evidence: EvidenceManifest;
  physics: Physics | null;
  mapping: Mapping | null;
}
export interface Workspace {
  source: Source;
  mode: Mode;
  base: string;
  scene: NetworkScene;
  manifest: NetworkManifest;
  candidates: Candidate[];
  index: BenchIndex | null;
  run: RunRecord | null;
}

export async function get<T>(
  base: string,
  path: string,
  mode: Mode,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${base}/api/${path}`, {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok)
    throw new Error(
      `接口读取失败（${response.status}）：${path}。请确认对应后端已启动。`,
    );
  const header = response.headers.get("X-WaterGuard-Mode");
  if (header !== mode)
    throw new Error(
      `数据模式异常：要求 ${mode}，响应头为 ${header ?? "缺失"}；已停止展示。`,
    );
  const data = await response.json();
  if (data.mode && data.mode !== header)
    throw new Error(`响应内容与模式响应头不一致：${path}`);
  return data as T;
}

export async function loadWorkspace(
  source: Source,
  selection: string,
  signal: AbortSignal,
): Promise<Workspace> {
  const { base, mode } = sources[source];
  const read = <T>(path: string) => get<T>(base, path, mode, signal);
  const manifests = await read<NetworkManifest[]>("networks");
  let index: BenchIndex | null = null;
  let run: RunRecord | null = null;
  let candidates: Candidate[];
  if (source === "fixture") {
    const [task, rows, evidence, record] = await Promise.all([
      read<TaskSpec>("fixture"),
      read<{ plan: ActionPlan; verification: VerificationReport }[]>(
        "runs/run-day1-fixture/candidates",
      ),
      read<EvidenceManifest>("runs/run-day1-fixture/evidence"),
      read<RunRecord>("runs/run-day1-fixture"),
    ]);
    run = record;
    candidates = await Promise.all(
      rows.map(async ({ plan, verification }, i) => ({
        id: plan.candidate_id,
        name: `方案 ${String.fromCharCode(65 + i)} · ${describePlan(plan)}`,
        simulationId: verification.simulation_id,
        task,
        plan,
        verification,
        series: await read<Series>(
          `simulations/${verification.simulation_id}/series`,
        ),
        result: null,
        evidence,
        physics: null,
        mapping: null,
      })),
    );
  } else {
    index = await read<BenchIndex>("benchmarks/current");
    if (!index.cases.length) throw new Error("场景索引为空。");
    let chosen = index.cases;
    if (source === "bench") {
      const active =
        index.cases.find((c) => c.simulation_id === selection) ??
        index.cases.find(
          (c) =>
            c.network_id === "epa-net3" &&
            c.case === "closure" &&
            c.engine === "wntr",
        ) ??
        index.cases[0];
      const normal = index.cases.find(
        (c) =>
          c.network_id === active.network_id &&
          c.engine === active.engine &&
          c.case === "normal",
      );
      chosen = normal && normal !== active ? [normal, active] : [active];
    }
    candidates = await Promise.all(
      chosen.map(async (c) => {
        const root = `simulations/${c.simulation_id}`;
        const [task, plan, verification, result, evidence, physics, mapping] =
          await Promise.all([
            read<TaskSpec>(`${root}/task`),
            read<ActionPlan>(`${root}/plan`),
            read<VerificationReport>(`${root}/verification`),
            read<SimulationResult>(root),
            read<EvidenceManifest>(`${root}/evidence`),
            read<Physics>(`${root}/physics`),
            read<Mapping>(`${root}/mapping`),
          ]);
        if (
          result.artifact.simulation_id !== c.simulation_id ||
          verification.simulation_id !== c.simulation_id ||
          verification.candidate_id !== plan.candidate_id ||
          verification.task_ref.constraint_hash !== task.constraint_hash
        )
          throw new Error("任务、方案与仿真绑定不一致，已停止展示。");
        // A/B 对照的工况名是 real-candidate-x：按序号和实际动作命名；场景库按工况类型命名。
        const letter = /^real-candidate-([a-z])$/i.exec(c.case)?.[1];
        return {
          id: plan.candidate_id,
          name: letter
            ? `方案 ${letter.toUpperCase()} · ${describePlan(plan)}`
            : (caseNames[c.case] ?? c.case),
          simulationId: c.simulation_id,
          task,
          plan,
          verification,
          series: result.series,
          result,
          evidence,
          physics,
          mapping,
        };
      }),
    );
  }
  const network = candidates.at(-1)!.task.network_ref.network_id;
  const scene = await read<NetworkScene>(`networks/${network}/scene`);
  const manifest = manifests.find((m) => m.network_id === network);
  if (!manifest) throw new Error("管网来源清单缺失。");
  return { source, mode, base, scene, manifest, candidates, index, run };
}

/**
 * 计算状态说明：只有引擎报告成功且收敛才写「已收敛」；converged 为 null 表示未测收敛，
 * 不能展示成收敛（契约 README）。Fixture 没有 SimulationResult，是人工样例。
 */
export function engineStatus(c: { result: Pick<SimulationResult, "artifact" | "diagnostics"> | null }): {
  text: string;
  tone: "ok" | "warn" | "neutral";
} {
  if (!c.result) return { text: "人工样例数据 · 非引擎计算", tone: "warn" };
  if (c.result.artifact.status !== "SUCCEEDED")
    return { text: `引擎状态 ${c.result.artifact.status}`, tone: "warn" };
  const converged = c.result.diagnostics.converged;
  return converged === true
    ? { text: "引擎计算完成 · 已收敛", tone: "ok" }
    : converged === false
      ? { text: "引擎报告未收敛", tone: "warn" }
      : { text: "引擎计算完成 · 未测收敛", tone: "neutral" };
}
export const runStatusText: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "运行中",
  NEEDS_INPUT: "等待补充信息",
  COMPLETED: "执行结束",
  BUDGET_EXHAUSTED: "预算用尽",
  ERROR: "出错",
  CANCELLED: "已取消",
};
export const conclusionText: Record<string, string> = {
  FEASIBLE_FOUND: "找到可行方案",
  NO_FEASIBLE_FOUND: "预算内未找到可行方案",
  INCOMPLETE: "未完成工程推荐",
  INVALID: "无效",
};
export const eventText: Record<string, string> = {
  "run.started": "运行开始",
  "candidate.submitted": "提交候选方案",
  "simulation.completed": "仿真完成",
  "verification.completed": "验收完成",
  "run.completed": "运行结束",
};
export const num = (n: number | null | undefined, digits = 2) =>
  n == null || !Number.isFinite(n)
    ? "—"
    : n.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const time = (s: number) =>
  `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
export const statusText = { pass: "通过", fail: "未通过", pending: "待验证" };
export const gates: [keyof Admission, string][] = [
  ["action_valid", "动作合法"],
  ["simulation_valid", "仿真有效"],
  ["nominal_constraints_pass", "硬约束"],
  ["required_stress_tests_pass", "扰动复验"],
  ["recommendation_eligible", "推荐准入"],
];
export const actionText = {
  link_open: "打开管段",
  link_close: "关闭管段",
  pump_on: "启动水泵",
  pump_off: "停止水泵",
};
export function download(name: string, data: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json;charset=utf-8",
    }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
