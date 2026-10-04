import type { RunEvent, RunRecord } from "@waterguard/contracts";
import type { Candidate } from "./data";
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
  tools?: string[];
  does: string;
  bound?: string;
  children?: MindNode[];
}

export const TREE: MindNode = {
  id: "start",
  kind: "start",
  title: "开始",
  sub: "接到爆管事件",
  does: "宿主建立运行记录，把故障、保供要求、允许操作和预算（仿真 ≤ 40 次、修正 ≤ 4 轮）交给智能体。",
  children: [
    {
      id: "model",
      kind: "llm",
      title: "任务建模",
      sub: "Agnes · 写任务草案",
      agent: true,
      tools: ["wg_inspect_network", "wg_propose_task"],
      does: "读管网和可操作设施目录，把用户需求整理成任务草案：目标、硬约束、允许动作、扰动域和假设，每个阈值标出来源。",
      bound: "不修改已冻结的条件，不隐去未知项",
      children: [
        {
          id: "ask",
          kind: "user",
          title: "等待用户补充",
          sub: "NEEDS_INPUT",
          tag: { kind: "IF", text: "有歧义" },
          loop: "回到任务建模",
          does: "关键条件缺失或有实质歧义时，运行暂停，向用户提出结构化的问题；答复后恢复会话，回到任务建模。",
        },
        {
          id: "freeze",
          kind: "gateway",
          title: "冻结任务",
          sub: "网关 · 约束哈希",
          tag: { kind: "ELSE", text: "输入完整" },
          tools: ["wg_propose_task"],
          does: "网关冻结 TaskSpec：之后谁也不能改硬约束，要改就是新版本任务；哈希对不上的请求一律拒绝。",
          bound: "硬约束只读（TASK_REVISION_MISMATCH）",
          children: [
            {
              id: "plan",
              kind: "llm",
              title: "方案规划",
              sub: "Agnes · 提出处置方案",
              agent: true,
              tools: [
                "wg_submit_plan",
                "wg_generate_candidates",
                "wg_compare_candidates",
              ],
              does: "先提交不操作的故障基线，量出不处置的后果；再提出关阀、启泵等动作组合，每次修改记下父方案和理由。",
              bound: "不降低阈值，不能自己写「验收通过」",
              children: [
                {
                  id: "check",
                  kind: "tool",
                  title: "仿真与验收",
                  sub: "计算进程 + 验收程序",
                  tools: ["wg_simulate_candidate", "wg_verify_candidate"],
                  does: "独立计算进程跑水力仿真（不收敛算无效，不算通过）；验收程序按冻结任务给出五层结论：动作合法、仿真有效、硬约束、扰动复验、推荐准入。",
                  bound: "验收器不能被智能体替换或改写",
                  children: [
                    {
                      id: "revise",
                      kind: "llm",
                      title: "修改方案",
                      sub: "记父方案和理由",
                      tag: { kind: "IF", text: "硬约束未通过" },
                      loop: "回到方案规划",
                      agent: true,
                      does: "按验收反馈（最差节点、时段、违规项）修改动作或策略，再算一轮，最多 4 轮；动作非法时网关直接退回。",
                    },
                    {
                      id: "budget",
                      kind: "end",
                      title: "结束 · 预算用尽",
                      sub: "返回最佳已验证方案",
                      tag: { kind: "ELIF", text: "预算用尽" },
                      does: "预算用完就停：有已验证的方案就返回最好的那个和未完成项；一个都没有就如实说明未找到。",
                    },
                    {
                      id: "review",
                      kind: "llm",
                      title: "反例审查",
                      sub: "Agnes · 扰动复验",
                      tag: { kind: "ELSE", text: "名义达标" },
                      agent: true,
                      tools: [
                        "wg_create_perturbation",
                        "wg_simulate_candidate",
                        "wg_verify_candidate",
                      ],
                      does: "只看原始约束和结果，给名义达标的方案加域内扰动（比如需水上浮 10%）再仿真、再验收，指出缺失的证据。",
                      bound: "不改原方案、任务或授权范围",
                      children: [
                        {
                          id: "back",
                          kind: "gateway",
                          title: "退回修改",
                          sub: "保留名义验证",
                          tag: { kind: "IF", text: "复验未通过" },
                          loop: "回到方案规划",
                          does: "复验没过的方案不能推荐：名义验证的结论保留，但不标记复验完成，退回规划修改。",
                        },
                        {
                          id: "pack",
                          kind: "end",
                          title: "打包证据 · 结束",
                          sub: "wg_build_evidence",
                          tag: { kind: "ELSE", text: "复验通过" },
                          tools: ["wg_build_evidence"],
                          does: "后端校验准入后打包证据（任务、方案、原始时序、验收报告、文件哈希），记录结论和终止原因；结论只能引用证据。",
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
 * 智能体角色没有记录时为「未接入」；样例里任务是直接给定的，任务建模没有执行；
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
        note: n.agent && !agentLive ? "AGH 未接入，没有调用记录" : "",
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
    const a = c?.verification.admission;
    if (e.type === "run.started") {
      mark("start", "ok", "运行已建立");
      nodes.freeze.state = "ok";
      nodes.freeze.note = `任务第 ${e.task_revision} 版已冻结`;
      nodes.model.note =
        "AGH 未接入；这次运行的任务是直接给定的，没有经过任务建模";
    } else if (e.type === "candidate.submitted") {
      edges.add("plan");
      const base = c && c.plan.actions.length === 0;
      mark(
        "plan",
        "ok",
        `${nodes.plan.state === "off" ? "AGH 未接入；" : ""}已提交 ${nodes.plan.count + 1} 个方案${c ? `（最新：${c.name}${base ? "，即故障基线" : ""}；提出者：${c.plan.source === "fixture" ? "人工样例" : c.plan.source}）` : ""}`,
      );
    } else if (e.type === "simulation.completed") {
      edges.add("check");
      mark("check", "ok", `已仿真 ${nodes.check.count + 1} 次`);
    } else if (e.type === "verification.completed") {
      edges.add("check");
      nodes.check.state = "ok";
      current = "check";
      if (!a) continue;
      if (a.nominal_constraints_pass === "fail") {
        edges.add("revise");
        const r = nodes.revise;
        r.count += 1;
        if (r.state !== "off") r.state = "ok";
        r.note = `${c?.name ?? "方案"}：硬约束未通过 → 修改（第 ${r.count} 次）`;
      } else {
        edges.add("review");
        reviewed = a.required_stress_tests_pass === "pass";
        const rv = nodes.review;
        rv.count += 1;
        if (rv.state !== "off") rv.state = reviewed ? "ok" : "wait";
        rv.note = reviewed
          ? `${c?.name ?? "方案"}名义达标，扰动复验通过`
          : `${c?.name ?? "方案"}名义达标；${rv.state === "off" ? "AGH 未接入，" : ""}扰动复验未做`;
        if (reviewed) edges.add("pack");
      }
    } else if (e.type === "run.completed") {
      const done = run?.conclusion === "FEASIBLE_FOUND";
      mark(
        "pack",
        done ? "ok" : "wait",
        done
          ? "证据已打包，找到可行方案"
          : `运行已结束并打包证据；结论：未完成工程推荐${reviewed ? "" : "（扰动复验未做）"}`,
      );
      current = "pack";
    }
  }
  if (
    events.length &&
    current &&
    nodes[current].state === "ok" &&
    events.at(-1)?.type !== "run.completed"
  )
    nodes[current].state = "running";
  return { nodes, edges, current };
}
