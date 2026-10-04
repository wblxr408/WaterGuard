import type { RunEvent, RunRecord } from "@waterguard/contracts";
import type { Candidate } from "./data";

/* 智能体工作流（Dify 式画布）的节点、连线，以及「运行事件 → 节点状态」的换算。
   节点按 docs/Agent设计.md 第 4–6 节：三个角色（任务建模、方案规划、反例审查）由 Agnes 承担，
   网关、仿真、验收、证据由程序和工具承担。画布是 U 形两行：上行从左往右，下行从右往左。 */

export type NodeKind =
  "start" | "llm" | "gateway" | "tool" | "condition" | "end";
export type NodeState = "idle" | "running" | "ok" | "fail" | "wait" | "off";

export interface FlowNode {
  id: string;
  kind: NodeKind;
  title: string;
  /** 卡片第二行：执行者或工具名 */
  sub: string;
  /** 由 Agnes 承担的角色节点；AGH 未接入时显示为虚线框 */
  agent?: boolean;
  /** 可调用的工具（出自 docs/Agent设计.md 第 6 节） */
  tools?: string[];
  /** 这个节点做什么、不能做什么 */
  does: string;
  bound?: string;
  col: number;
  row: number;
}

export const NODES: FlowNode[] = [
  {
    id: "start",
    kind: "start",
    title: "开始",
    sub: "接到爆管事件",
    does: "宿主建立运行记录：任务、管网和约束的哈希一起记下，后台负责生命周期与治理。",
    col: 0,
    row: 0,
  },
  {
    id: "model",
    kind: "llm",
    title: "任务建模",
    sub: "Agnes · 任务建模角色",
    agent: true,
    tools: ["wg_inspect_network", "wg_propose_task"],
    does: "读管网和动作目录，把事故、保供要求、允许的操作整理成任务草案；有实质歧义时向用户提问。",
    bound: "不修改已冻结的条件，不隐去未知项",
    col: 1,
    row: 0,
  },
  {
    id: "freeze",
    kind: "gateway",
    title: "冻结任务",
    sub: "网关 · 约束哈希校验",
    does: "检查草案：输入完整且在用户授权内时冻结 TaskSpec；之后任何人都不能改硬约束，改了就是新版本任务。",
    bound: "硬约束只读，constraint_hash 不符即拒绝（TASK_REVISION_MISMATCH）",
    col: 2,
    row: 0,
  },
  {
    id: "plan",
    kind: "llm",
    title: "方案规划",
    sub: "Agnes · 方案规划角色",
    agent: true,
    tools: [
      "wg_submit_plan",
      "wg_generate_candidates",
      "wg_compare_candidates",
    ],
    does: "先提交故障基线（不操作），再提出关阀、启泵等操作组合；按验收反馈修改，记下父方案和修改理由。",
    bound: "不降低阈值，不能自己写「验收通过」",
    col: 3,
    row: 0,
  },
  {
    id: "sim",
    kind: "tool",
    title: "水力仿真",
    sub: "wg_simulate_candidate",
    tools: ["wg_simulate_candidate", "wg_get_simulation"],
    does: "独立计算进程按方案跑水力仿真，返回指标、最差节点与时段和产物引用；不收敛算计算无效，不算通过。",
    col: 4,
    row: 0,
  },
  {
    id: "verify",
    kind: "tool",
    title: "独立验收",
    sub: "wg_verify_candidate",
    tools: ["wg_verify_candidate"],
    does: "验收程序按冻结任务逐项核对：动作合法、仿真有效、硬约束、扰动复验、推荐准入，五层分开给结论。",
    bound: "验收器不能被智能体替换或改写",
    col: 4,
    row: 1,
  },
  {
    id: "cond",
    kind: "condition",
    title: "达标？",
    sub: "硬约束 + 五层准入",
    does: "硬约束没过就回到方案规划修改；名义达标的方案再交给反例审查做扰动复验。",
    col: 3,
    row: 1,
  },
  {
    id: "review",
    kind: "llm",
    title: "反例审查",
    sub: "Agnes · 反例审查角色",
    agent: true,
    tools: [
      "wg_create_perturbation",
      "wg_simulate_candidate",
      "wg_verify_candidate",
    ],
    does: "给有希望的方案加域内扰动（比如需水上浮 10%）再仿真、再验收，指出缺失的证据。",
    bound: "不改原方案、任务或授权范围",
    col: 2,
    row: 1,
  },
  {
    id: "pack",
    kind: "tool",
    title: "打包证据",
    sub: "wg_build_evidence",
    tools: ["wg_build_evidence"],
    does: "后端校验准入条件后打包证据：任务、方案、原始时序、验收报告和文件哈希；结论只能引用证据。",
    col: 1,
    row: 1,
  },
  {
    id: "end",
    kind: "end",
    title: "结束",
    sub: "结论与终止原因",
    does: "记录终止原因和结论范围：找到可行方案、未找到、预算结束、出错或取消。",
    col: 0,
    row: 1,
  },
];

export interface FlowEdge {
  from: string;
  to: string;
  label?: string;
  /** 「没过就修改」的回路 */
  loop?: boolean;
}

export const EDGES: FlowEdge[] = [
  { from: "start", to: "model" },
  { from: "model", to: "freeze" },
  { from: "freeze", to: "plan" },
  { from: "plan", to: "sim" },
  { from: "sim", to: "verify" },
  { from: "verify", to: "cond" },
  { from: "cond", to: "plan", label: "否 · 修改方案", loop: true },
  { from: "cond", to: "review", label: "是" },
  { from: "review", to: "pack" },
  { from: "pack", to: "end" },
];

/** 画布尺寸与节点位置（像素） */
export const LAYOUT = {
  nodeW: 196,
  nodeH: 84,
  colGap: 236,
  rowGap: 230,
  padX: 40,
  padY: 56,
};
export const nodeXY = (n: FlowNode) => ({
  x: LAYOUT.padX + n.col * LAYOUT.colGap,
  y: LAYOUT.padY + n.row * LAYOUT.rowGap,
});
export const STAGE_W = LAYOUT.padX * 2 + 4 * LAYOUT.colGap + LAYOUT.nodeW;
export const STAGE_H = LAYOUT.padY * 2 + LAYOUT.rowGap + LAYOUT.nodeH;

/** 运行事件落在哪个节点 */
export const nodeOfEvent: Record<RunEvent["type"], string> = {
  "run.started": "start",
  "candidate.submitted": "plan",
  "simulation.completed": "sim",
  "verification.completed": "verify",
  "run.completed": "pack",
};

export interface FlowState {
  nodes: Record<string, { state: NodeState; count: number; note: string }>;
  /** 走过的连线（from→to） */
  edges: Set<string>;
  /** 回路走过几次 */
  loops: number;
  /** 当前正在发生的节点 */
  current: string | null;
}

export const edgeKey = (e: { from: string; to: string }) => `${e.from}>${e.to}`;

/**
 * 把前 n 条事件折算成每个节点的状态。只用事件和它引用的候选、验收结果，不推测没发生的步骤：
 * Agnes 的三个角色在没有 AGH 记录时一律标「未接入」，反例审查在复验缺失时标「待复验」。
 */
export function flowState(
  events: RunEvent[],
  run: RunRecord | null,
  candidates: Map<string, Candidate>,
  agentLive: boolean,
): FlowState {
  const nodes: FlowState["nodes"] = Object.fromEntries(
    NODES.map((n) => [
      n.id,
      {
        state: (n.agent && !agentLive ? "off" : "idle") as NodeState,
        count: 0,
        note: n.agent && !agentLive ? "AGH 未接入" : "",
      },
    ]),
  );
  const edges = new Set<string>();
  let loops = 0;
  let current: string | null = null;
  const bump = (id: string, state: NodeState, note?: string) => {
    const n = nodes[id];
    if (n.state !== "off") n.state = state;
    n.count += 1;
    if (note) n.note = note;
    current = id;
  };
  let verified = 0;
  for (const e of events) {
    if (e.type === "run.started") {
      bump("start", "ok", "运行已建立");
      // 任务在运行开始前已经冻结（运行记录带约束哈希）
      nodes.freeze.state = "ok";
      nodes.freeze.note = `任务第 ${e.task_revision} 版已冻结`;
      edges.add("start>model");
      edges.add("model>freeze");
      edges.add("freeze>plan");
    } else if (e.type === "candidate.submitted") {
      if (verified > 0) {
        loops += 1;
        edges.add("cond>plan");
      }
      const c = e.candidate_id ? candidates.get(e.candidate_id) : undefined;
      bump(
        "plan",
        "ok",
        `已提交 ${nodes.plan.count + 1} 个方案${c ? `（最新：${c.name}）` : ""}`,
      );
      edges.add("plan>sim");
    } else if (e.type === "simulation.completed") {
      bump("sim", "ok", `已仿真 ${nodes.sim.count + 1} 次`);
      edges.add("sim>verify");
    } else if (e.type === "verification.completed") {
      verified += 1;
      const c = e.candidate_id ? candidates.get(e.candidate_id) : undefined;
      const a = c?.verification.admission;
      bump("verify", "ok", `已验收 ${nodes.verify.count + 1} 次`);
      edges.add("verify>cond");
      if (a) {
        const nominal = a.nominal_constraints_pass === "pass";
        nodes.cond.count += 1;
        nodes.cond.state = nominal ? "wait" : "fail";
        nodes.cond.note = nominal
          ? "硬约束通过；扰动复验未做，不能推荐"
          : `${c?.name ?? "方案"} 硬约束未通过 → 修改`;
        if (nominal) {
          edges.add("cond>review");
          if (nodes.review.state !== "off") nodes.review.state = "wait";
          nodes.review.note =
            nodes.review.state === "off"
              ? "AGH 未接入 · 扰动复验未做"
              : "扰动复验未做";
        }
      }
    } else if (e.type === "run.completed") {
      // 反例审查没做也会结束并打包（带缺失项），所以「审查 → 打包」这条线不算走过
      bump("pack", "ok", "证据清单已生成");
      if (nodes.review.count > 0) edges.add("review>pack");
      edges.add("pack>end");
      nodes.end.state = run?.conclusion === "FEASIBLE_FOUND" ? "ok" : "wait";
      nodes.end.count = 1;
      current = "end";
    }
  }
  if (
    events.length &&
    current &&
    nodes[current].state === "ok" &&
    events.at(-1)?.type !== "run.completed"
  )
    nodes[current].state = "running";
  return { nodes, edges, loops, current };
}
