import type { RunEvent, RunRecord } from "@waterguard/contracts";
import type { AgentCandidate as Candidate } from "./agent-stream";
import type { NodeKind, NodeState } from "./agent-flow";

/* 智能体工作流「分支导图」：思维导图式的一棵树，从左往右展开，在判断处分叉，分支名写在连线上。
   回到前面步骤的地方不画长回线，而是在叶子节点上标「↺ 回到……」，图更清楚。
   出处：docs/Agent设计.md 第 5 节（执行流程）、第 7 节（边界）、第 8 节（失败与恢复）。 */

export type MindKind = NodeKind | "user";
export type TagKind = "IF" | "ELIF" | "ELSE";

export interface MindNode {
  id: string;
  kind: MindKind;
  title: string;
  sub: string;
  /** 从父节点走到这里的条件，写在连线上 */
  tag?: { kind: TagKind; text: string };
  /** 叶子：之后回到哪一步 */
  loop?: string;
  agent?: boolean;
  children?: MindNode[];
}

export const TREE: MindNode = {
  id: "start",
  kind: "start",
  title: "开始",
  sub: "接到爆管事件",
  children: [
    {
      id: "model",
      kind: "llm",
      title: "任务建模",
      sub: "Agnes · 写任务草案",
      agent: true,
      children: [
        {
          id: "ask",
          kind: "user",
          title: "等待用户补充",
          sub: "NEEDS_INPUT",
          tag: { kind: "IF", text: "有歧义" },
          loop: "回到任务建模",
        },
        {
          id: "freeze",
          kind: "gateway",
          title: "冻结任务",
          sub: "网关 · 约束哈希",
          tag: { kind: "ELSE", text: "输入完整" },
          children: [
            {
              id: "plan",
              kind: "llm",
              title: "方案规划",
              sub: "Agnes · 提出处置方案",
              agent: true,
              children: [
                {
                  id: "check",
                  kind: "tool",
                  title: "仿真与验收",
                  sub: "计算进程 + 验收程序",
                  children: [
                    {
                      id: "revise",
                      kind: "llm",
                      title: "修改方案",
                      sub: "记父方案和理由",
                      tag: { kind: "IF", text: "硬约束未通过" },
                      loop: "回到方案规划",
                      agent: true,
                    },
                    {
                      id: "budget",
                      kind: "end",
                      title: "结束 · 预算用尽",
                      sub: "返回最佳已验证方案",
                      tag: { kind: "ELIF", text: "预算用尽" },
                    },
                    {
                      id: "review",
                      kind: "llm",
                      title: "反例审查",
                      sub: "Agnes · 扰动复验",
                      tag: { kind: "ELSE", text: "名义达标" },
                      agent: true,
                      children: [
                        {
                          id: "back",
                          kind: "gateway",
                          title: "退回修改",
                          sub: "保留名义验证",
                          tag: { kind: "IF", text: "复验未通过" },
                          loop: "回到方案规划",
                        },
                        {
                          id: "pack",
                          kind: "end",
                          title: "打包证据 · 结束",
                          sub: "wg_build_evidence",
                          tag: { kind: "ELSE", text: "复验通过" },
                        },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    },
  ],
};

/* ---------------------------------------------------------------- 布局：整齐树（叶子依次排，父节点居中） */
export const M = { w: 206, h: 72, step: 246, slot: 108, padX: 40, padY: 48 };

export interface Placed extends MindNode {
  x: number;
  y: number;
  parent: string | null;
}

export const PLACED: Placed[] = [];
{
  let leaf = 0;
  const walk = (n: MindNode, depth: number, parent: string | null): number => {
    let y: number;
    if (!n.children?.length) y = leaf++ * M.slot;
    else {
      const ys = n.children.map((c) => walk(c, depth + 1, n.id));
      y = (ys[0] + ys[ys.length - 1]) / 2;
    }
    PLACED.push({ ...n, x: M.padX + depth * M.step, y: M.padY + y, parent });
    return y;
  };
  walk(TREE, 0, null);
}
export const MSTAGE_W = Math.max(...PLACED.map((n) => n.x)) + M.w + M.padX;
export const MSTAGE_H = Math.max(...PLACED.map((n) => n.y)) + M.h + M.padY;

/* ---------------------------------------------------------------- 运行事件 → 导图状态 */
export interface MindState {
  nodes: Record<string, { state: NodeState; count: number; note: string }>;
  /** 走过的连线：子节点 id（连线 = 父 → 子） */
  edges: Set<string>;
  current: string | null;
}

/** 一条事件落在导图的哪个节点 */
export function mindNodeOf(e: RunEvent): string {
  if (e.type === "run.started") return "start";
  if (e.type === "candidate.submitted") return "plan";
  if (e.type === "run.completed") return "pack";
  return "check";
}

/**
 * 只用事件和事件引用的候选、验收结果换算状态，不推测没发生的步骤：
 * 智能体角色没有记录时为「无调用记录」；样例里任务是直接给定的，任务建模没有执行；
 * 没做反例审查就结束的运行，「反例审查 → 打包」这条线不算走过。
 */
export function mindState(
  events: RunEvent[],
  run: RunRecord | null,
  candidates: Map<string, Candidate>,
  agentLive: boolean,
): MindState {
  const nodes: MindState["nodes"] = Object.fromEntries(
    PLACED.map((n) => [
      n.id,
      {
        state: (n.agent && !agentLive ? "off" : "idle") as NodeState,
        count: 0,
        note: n.agent && !agentLive ? "本次运行无原生调用记录" : "",
      },
    ]),
  );
  const edges = new Set<string>();
  let current: string | null = null;
  const mark = (id: string, state: NodeState, note?: string) => {
    const n = nodes[id];
    if (n.state !== "off") n.state = state;
    n.count += 1;
    if (note) n.note = note;
    current = id;
  };
  let reviewed = false;
  for (const e of events) {
    const c = e.candidate_id ? candidates.get(e.candidate_id) : undefined;
    const a = c?.verification?.admission;
    if (e.type === "run.started") {
      mark("start", "ok", "运行已建立");
      nodes.freeze.state = "ok";
      nodes.freeze.note = `任务第 ${e.task_revision} 版已冻结`;
      nodes.model.note =
        "任务在运行前已冻结；尚未观察到任务建模角色调用";
    } else if (e.type === "candidate.submitted") {
      edges.add("plan");
      const base = c && c.plan.actions.length === 0;
      mark(
        "plan",
        "ok",
        `${nodes.plan.state === "off" ? "无原生调用记录；" : ""}已提交 ${nodes.plan.count + 1} 个方案${c ? `（最新：${c.name}${base ? "，即故障基线" : ""}；提出者：${c.plan.source === "fixture" ? "人工样例" : c.plan.source}）` : ""}`,
      );
    } else if (e.type === "simulation.completed") {
      edges.add("check");
      const failed = c?.result && c.result.artifact.status !== "SUCCEEDED";
      mark("check", failed ? "fail" : "wait", `已返回 ${nodes.check.count + 1} 次仿真${failed ? "；计算失败" : "；等待独立验收"}`);
    } else if (e.type === "verification.completed") {
      edges.add("check");
      nodes.check.state = a ? "ok" : "wait";
      nodes.check.note = a ? "仿真和独立验收记录已返回" : "已收到验收事件，正在读取验收结果";
      current = "check";
      if (!a) continue;
      if (a.nominal_constraints_pass === "fail") {
        edges.add("revise");
        const r = nodes.revise;
        r.count += 1;
        if (r.state !== "off") r.state = "ok";
        r.note = `${c?.name ?? "方案"}：正常供水仍有缺口 → 继续挽救（第 ${r.count} 次）`;
      } else {
        edges.add("review");
        reviewed = a.required_stress_tests_pass === "pass";
        const rv = nodes.review;
        rv.count += 1;
        if (rv.state !== "off") rv.state = reviewed ? "ok" : "wait";
        rv.note = reviewed
          ? `${c?.name ?? "方案"}名义达标，扰动复验通过`
          : `${c?.name ?? "方案"}名义达标；${rv.state === "off" ? "无原生调用记录，" : ""}扰动复验未做`;
        if (reviewed) edges.add("pack");
      }
    } else if (e.type === "run.completed") {
      const done = run?.conclusion === "FEASIBLE_FOUND";
      mark(
        "pack",
        run?.status === "ERROR" ? "fail" : done ? "ok" : "wait",
        done
          ? "运行结束，找到可行方案"
          : `运行已结束；结论：未完成工程推荐${reviewed ? "" : "（扰动复验未做）"}`,
      );
      current = "pack";
    }
  }
  return { nodes, edges, current };
}
