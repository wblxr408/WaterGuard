import { executionGraph, EXECUTION_LAYOUT as L } from './agent-execution.ts';
import type { CandidateSummary, ExecutionFrame, ExecutionNode, ExecutionEdge } from './agent-execution.ts';
import type { RunRecord } from '@waterguard/contracts';

export const actionText = (plan: CandidateSummary) => plan.actions.length ? plan.actions.map(a =>
  `${a.kind === 'link_close' ? '关闭' : a.kind === 'link_open' ? '打开' : a.kind}管段 ${a.link_id} · ${(a.simulation_time_s / 3600).toFixed(1)} h`).join('；') : '保持现有管网运行';

/** Every branch is backed by an offered plan, invocation, or returned assessment in visible frames. */
export function branchGraph(frames: ExecutionFrame[], run: RunRecord | null) {
  const actual = executionGraph(frames, run);
  const nodes: ExecutionNode[] = [], edges: ExecutionEdge[] = [];
  const plans = new Map<string, { plan: CandidateSummary; at: string; source?: string; unknown?: boolean }>();
  const mapped = new Map<string, string>();
  for (const f of frames) {
    for (const plan of f.event?.candidates ?? []) if (!plans.has(plan.candidate_id))
      plans.set(plan.candidate_id, { plan, at: f.occurred_at, source: f.event!.native_tool_call_ref });
    const plan = f.event?.candidate_plan;
    if (plan) plans.set(plan.candidate_id, { ...plans.get(plan.candidate_id), plan, at: plans.get(plan.candidate_id)?.at ?? f.occurred_at });
  }
  const calls = actual.nodes.filter(n => n.events.length);
  const assigned = new Map<string, string>();
  let latest: string | undefined;
  for (const call of calls) {
    if (call.candidateId) latest = call.candidateId;
    if (call.candidateId || (call.tool === 'wg_jev_decide' && latest)) assigned.set(call.id, call.candidateId ?? latest!);
    if (call.candidateId && !plans.has(call.candidateId)) plans.set(call.candidateId, {
      plan: { candidate_id: call.candidateId, parent_candidate_id: null, actions: [] }, at: call.startedAt, unknown: true });
  }
  const place = (node: ExecutionNode, col: number, y: number) => {
    const placed = { ...node, x: L.pad + col * L.dx, y };
    nodes.push(placed); mapped.set(node.id, placed.id); return placed;
  };
  const connect = (a: string | undefined, b: string, kind: ExecutionEdge['kind'] = 'order', label?: string) => {
    if (a && a !== b) edges.push({ from: a, to: b, kind, label });
  };
  // Preparation stays in real invocation order; Jev remains an explicit invocation.
  const originalPrep = actual.nodes.filter(n => n.kind === 'start' || (n.events.length && !assigned.has(n.id) &&
    ['wg_get_task', 'wg_inspect_network', 'wg_generate_candidates', 'wg_jev_decide'].includes(n.tool)));
  const reads = originalPrep.filter(n => ['wg_get_task', 'wg_inspect_network'].includes(n.tool));
  const preparation = reads.length ? [{ ...reads.at(-1)!, id: 'execution-preparation', title: '任务与管网',
    events: reads.flatMap(n => n.events), startedAt: reads[0].startedAt,
    state: (reads.some(n => n.state === 'running') ? 'running' : reads.some(n => n.state === 'fail') ? 'fail' : reads.some(n => n.state === 'interrupted') ? 'interrupted' : 'ok') as ExecutionNode['state'],
    summary: reads.at(-1)!.summary }, ...originalPrep.filter(n => n.kind !== 'start' && !reads.includes(n))] : originalPrep;
  preparation.forEach((n, i) => { place(n, i % 4, L.pad + Math.floor(i / 4) * L.dy); connect(preparation[i - 1]?.id, n.id); });
  if (reads.length) for (const n of originalPrep.filter(n => n.kind === 'start' || reads.includes(n))) mapped.set(n.id, 'execution-preparation');
  let y = preparation.length ? L.pad + Math.ceil(preparation.length / 4) * L.dy + 42 : L.pad;
  for (const [id, offered] of plans) {
    const lane = calls.filter(c => assigned.get(c.id) === id);
    const submissions = lane.filter(c => c.tool === 'wg_submit_plan');
    const evaluations = lane.filter(c => ['wg_simulate_candidate', 'wg_verify_candidate'].includes(c.tool));
    const selected = submissions.length > 0;
    const reported = lane.flatMap(c => c.events).reverse().find(e => e.assessment)?.assessment;
    const candidate = place({ id: `candidate:${id}`, title: id, tool: '候选方案', kind: 'candidate',
      state: selected ? 'ok' : 'idle', statusLabel: reported ? '已验收' : selected ? '已选择' : '未评估',
      summary: offered.unknown ? '候选操作尚未返回' : actionText(offered.plan), startedAt: offered.at, events: [], candidateId: id, candidatePlan: offered.unknown ? undefined : offered.plan,
      height: 144, x: 0, y: 0 }, 0, y);
    const source = offered.source ?? preparation.at(-1)?.id;
    connect(source, candidate.id, selected ? 'chosen' : 'offer', '候选');
    let bottom = y + 144;
    submissions.forEach((c, i) => { const n = place({ ...c, title: 'Agnes 选择', summary: c.state === 'running' ? '正在提交方案' : c.state === 'fail' ? c.summary : `提交 ${id}` }, 1, y + i * L.dy);
      connect(candidate.id, n.id, 'chosen'); bottom = Math.max(bottom, n.y + L.h); });
    // Pair each simulation with its subsequent verification, retaining repeated attempts.
    const groups: ExecutionNode[][] = [];
    for (const c of evaluations) {
      const last = groups.at(-1);
      if (!last || c.tool === 'wg_simulate_candidate' || last.some(n => n.tool === c.tool)) groups.push([c]);
      else last.push(c);
    }
    groups.forEach((group, i) => {
      const last = group.at(-1)!, report = group.flatMap(c => c.events).reverse().find(e => e.assessment);
      const assessment = report?.assessment;
      const state = group.some(c => c.state === 'running') ? 'running' : group.some(c => c.state === 'fail') ? 'fail' : group.some(c => c.state === 'interrupted') ? 'interrupted' : 'ok';
      const n = place({ ...last, id: `evaluation:${group[0].id}`, title: group.length > 1 ? '仿真与验收' : last.title,
        state, startedAt: group[0].startedAt, events: group.flatMap(c => c.events), assessment,
        summary: assessment ? `最低压力 ${assessment.metrics.min_pressure_m?.toFixed(2) ?? '—'} m · 违规 ${assessment.violation_count}` : last.summary }, 2, y + i * 286);
      group.forEach(c => mapped.set(c.id, n.id));
      const preceding = submissions.filter(c => Date.parse(c.startedAt) <= Date.parse(group[0].startedAt)).at(-1);
      connect(preceding?.id ?? candidate.id, n.id, 'chosen');
      bottom = Math.max(bottom, n.y + L.h);
      if (assessment) for (const [j, key, title] of [[0, 'nominal_constraints_pass', '名义约束'], [1, 'required_stress_tests_pass', '扰动复验']] as const) {
        const result = assessment.admission[key];
        if (!result) continue;
        const gate = place({ id: `${n.id}:${key}`, title: `${title}${result === 'pass' ? '通过' : result === 'fail' ? '未通过' : '待验证'}`,
          kind: 'condition', tool: '调用时验收报告', state: result === 'pass' ? 'ok' : result === 'fail' ? 'fail' : 'wait',
          statusLabel: result === 'pass' ? '通过' : result === 'fail' ? '未通过' : '待验证',
          summary: key === 'nominal_constraints_pass' ? `违规 ${assessment.violation_count} 项` : result === 'pending' ? '等待扰动验收结果' : result === 'pass' ? '扰动验收已通过' : '扰动验收未通过',
          startedAt: report!.occurred_at, events: [], candidateId: id, assessment, x: 0, y: 0 }, 3, n.y + j * 134);
        connect(n.id, gate.id, 'outcome', title); bottom = Math.max(bottom, gate.y + L.h);
      }
    });
    for (const c of lane.filter(c => !submissions.includes(c) && !evaluations.includes(c))) {
      const n = place(c, 2, bottom + 34); connect(groups.at(-1) ? mapped.get(groups.at(-1)!.at(-1)!.id) : submissions.at(-1)?.id ?? candidate.id, n.id);
      bottom = n.y + L.h;
    }
    y = bottom + 74;
  }
  // Preserve remaining actual calls, end state and archive facts.
  const tail = actual.nodes.filter(n => !mapped.has(n.id));
  tail.forEach((n, i) => { place(n, i % 4, y + Math.floor(i / 4) * L.dy);
    connect(i ? tail[i - 1].id : mapped.get(calls.filter(c => mapped.has(c.id)).at(-1)?.id ?? ''), n.id); });
  for (const edge of actual.edges.filter(e => e.kind === 'retry' || e.kind === 'candidate'))
    connect(mapped.get(edge.from), mapped.get(edge.to)!, edge.kind, edge.label);
  // A Jev selection link exists only when the configured provider actually returned that choice.
  for (const call of calls.filter(c => c.kind === 'jev')) {
    const choice = [...call.events].reverse().find(e => e.jev_configured === true && e.jev_choice)?.jev_choice;
    if (choice && plans.has(choice)) connect(mapped.get(call.id), `candidate:${choice}`, 'chosen', 'Jev 选择');
  }
  return { nodes, edges, switches: actual.switches, retries: actual.retries,
    width: L.pad * 2 + 3 * L.dx + L.w,
    height: Math.max(220, ...nodes.map(n => n.y + (n.height ?? L.h) + L.pad)) };
}
