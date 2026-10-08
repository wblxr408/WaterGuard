import type { RunEvent, RunRecord } from "@waterguard/contracts";
import type { AgentCandidate as Candidate } from "./agent-stream";

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
  /** 由 Agnes 承担的角色节点；无原生调用记录时显示为虚线框 */
  agent?: boolean;
  col: number;
  row: number;
}

export const NODES: FlowNode[] = [
  {
    id: "start",
    kind: "start",
    title: "开始",
    sub: "接到爆管事件",
    col: 0,
    row: 0,
  },
  {
    id: "model",
    kind: "llm",
    title: "任务建模",
    sub: "Agnes · 任务建模角色",
    agent: true,
    col: 1,
    row: 0,
  },
  {
    id: "freeze",
    kind: "gateway",
    title: "冻结任务",
    sub: "网关 · 约束哈希校验",
    col: 2,
    row: 0,
  },
  {
    id: "plan",
    kind: "llm",
    title: "方案规划",
    sub: "Agnes · 方案规划角色",
    agent: true,
    col: 3,
    row: 0,
  },
  {
    id: "sim",
    kind: "tool",
    title: "水力仿真",
    sub: "wg_simulate_candidate",
    col: 4,
    row: 0,
  },
  {
    id: "verify",
    kind: "tool",
    title: "独立验收",
    sub: "wg_verify_candidate",
    col: 4,
    row: 1,
  },
  {
    id: "cond",
    kind: "condition",
    title: "达标？",
    sub: "硬约束 + 五层准入",
    col: 3,
    row: 1,
  },
  {
    id: "review",
    kind: "llm",
    title: "反例审查",
    sub: "Agnes · 反例审查角色",
    agent: true,
    col: 2,
    row: 1,
  },
  {
    id: "pack",
    kind: "tool",
    title: "打包证据",
    sub: "wg_build_evidence",
    col: 1,
    row: 1,
  },
  {
    id: "end",
    kind: "end",
    title: "结束",
    sub: "结论与终止原因",
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
  "clarification.requested": "model",
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
 * Agnes 的三个角色在没有 AGH 记录时一律标「无调用记录」，反例审查在复验缺失时标「待复验」。
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
        note: n.agent && !agentLive ? "无原生调用记录" : "",
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
      const result = e.candidate_id ? candidates.get(e.candidate_id)?.result : null;
      bump("sim", result ? result.artifact.status === "SUCCEEDED" ? "ok" : "fail" : "wait", `已返回 ${nodes.sim.count + 1} 次仿真`);
      edges.add("sim>verify");
    } else if (e.type === "verification.completed") {
      verified += 1;
      const c = e.candidate_id ? candidates.get(e.candidate_id) : undefined;
      const a = c?.verification?.admission;
      bump("verify", a ? "ok" : "wait", `已返回 ${nodes.verify.count + 1} 次验收${a ? "" : "，正在读取结果"}`);
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
              ? "无原生调用记录 · 扰动复验未做"
              : "扰动复验未做";
        }
      }
    } else if (e.type === "run.completed") {
      // 反例审查没做也会结束并打包（带缺失项），所以「审查 → 打包」这条线不算走过
      bump("pack", run?.status === "ERROR" ? "fail" : "wait", "运行结束；证据完整性以证据清单为准");
      if (nodes.review.count > 0) edges.add("review>pack");
      edges.add("pack>end");
      nodes.end.state = run?.conclusion === "FEASIBLE_FOUND" ? "ok" : "wait";
      nodes.end.count = 1;
      current = "end";
    }
  }
  return { nodes, edges, loops, current };
}
