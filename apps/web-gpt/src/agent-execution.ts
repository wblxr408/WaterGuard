import type { RunEvent, RunRecord } from '@waterguard/contracts';

export interface CandidateSummary {
  candidate_id: string;
  parent_candidate_id: string | null;
  actions: { kind: string; link_id: string; simulation_time_s: number }[];
}
export interface AssessmentSummary {
  validity: 'valid' | 'invalid' | null;
  admission: { nominal_constraints_pass?: 'pass' | 'fail' | 'pending'; required_stress_tests_pass?: 'pass' | 'fail' | 'pending'; recommendation_eligible?: 'pass' | 'fail' | 'pending' };
  metrics: { min_pressure_m?: number; supply_ratio?: number; shortage_m3?: number; min_node_time_pass_ratio?: number; mean_node_time_pass_ratio?: number };
  violation_count: number;
}

export interface ExecutionEvent {
  id: string;
  sequence: number;
  occurred_at: string;
  native_tool_call_ref: string;
  tool_name: string;
  type: 'tool.started' | 'tool.completed';
  failed: boolean;
  candidate_id?: string | null;
  parent_candidate_id?: string | null;
  target_run_id?: string | null;
  simulation_id?: string | null;
  jev_configured?: boolean;
  summary?: string | null;
  candidates?: CandidateSummary[];
  candidate_plan?: CandidateSummary;
  assessment?: AssessmentSummary;
  jev_choice?: string;
}
export interface ExecutionTrace {
  run_id: string;
  agh_session_ref: string | null;
  events: ExecutionEvent[];
  evidence: { bundle_id: string; occurred_at: string } | null;
}
export interface ExecutionFrame {
  id: string;
  occurred_at: string;
  type: 'start' | 'end' | 'evidence' | 'tool';
  event?: ExecutionEvent;
  summary?: string;
}
export interface ExecutionNode {
  id: string;
  title: string;
  tool: string;
  kind: 'start' | 'tool' | 'llm' | 'jev' | 'end' | 'gateway' | 'candidate' | 'condition';
  state: 'running' | 'ok' | 'fail' | 'interrupted' | 'idle' | 'wait';
  summary: string;
  startedAt: string;
  endedAt?: string;
  events: ExecutionEvent[];
  candidateId?: string | null;
  targetRunId?: string | null;
  branch?: string;
  x: number;
  y: number;
  height?: number;
  statusLabel?: string;
  candidatePlan?: CandidateSummary;
  assessment?: AssessmentSummary;
}
export interface ExecutionEdge { from: string; to: string; kind: 'order' | 'retry' | 'candidate' | 'offer' | 'chosen' | 'outcome'; label?: string }

const tools: Record<string, { title: string; kind: ExecutionNode['kind'] }> = {
  wg_get_task: { title: '读取任务', kind: 'gateway' },
  wg_inspect_network: { title: '分析管网', kind: 'tool' },
  wg_generate_candidates: { title: '规划候选', kind: 'llm' },
  wg_diagnose_failure: { title: '定位实际失效', kind: 'tool' },
  wg_revise_plan: { title: '修订动作与时序', kind: 'llm' },
  wg_jev_decide: { title: 'Jev 决策', kind: 'jev' },
  wg_submit_plan: { title: '提交方案', kind: 'llm' },
  wg_simulate_candidate: { title: '水力仿真', kind: 'tool' },
  wg_verify_candidate: { title: '独立验收', kind: 'tool' },
  wg_compare_candidates: { title: '比较方案', kind: 'tool' },
  wg_create_perturbation: { title: '同方案压力复验', kind: 'tool' },
  wg_build_evidence: { title: '读取证据', kind: 'gateway' },
};
export const EXECUTION_LAYOUT = { w: 232, h: 108, dx: 294, dy: 176, pad: 46, columns: 4 };

export function executionFrames(trace: ExecutionTrace | null, workflow: RunEvent[]): ExecutionFrame[] {
  const frames: ExecutionFrame[] = [];
  const start = workflow.find(e => e.type === 'run.started');
  if (start) frames.push({ id: 'execution-start', occurred_at: start.occurred_at, type: 'start' });
  const seen = new Set<string>();
  for (const event of [...(trace?.events ?? [])].sort((a, b) => a.sequence - b.sequence)) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    frames.push({ id: event.id, occurred_at: event.occurred_at, type: 'tool', event });
  }
  const end = workflow.find(e => e.type === 'run.completed');
  if (end) frames.push({ id: 'execution-end', occurred_at: end.occurred_at, type: 'end' });
  if (trace?.evidence) frames.push({ id: 'execution-evidence', occurred_at: trace.evidence.occurred_at,
    type: 'evidence', summary: trace.evidence.bundle_id });
  return frames.sort((a, b) => Date.parse(a.occurred_at) - Date.parse(b.occurred_at));
}

/** Nodes are keyed by actual invocation, not by business phase or expected future steps. */
export function executionGraph(frames: ExecutionFrame[], run: RunRecord | null, columns = EXECUTION_LAYOUT.columns) {
  const nodes: ExecutionNode[] = [];
  const byRef = new Map<string, ExecutionNode>();
  const ended = frames.some(f => f.type === 'end');
  for (const frame of frames) {
    const event = frame.event;
    if (!event) {
      nodes.push({ id: frame.id, title: frame.type === 'start' ? '运行建立' : frame.type === 'end' ? '执行结束' : '证据归档',
        tool: frame.type === 'evidence' ? '运行服务' : '任务运行服务', kind: frame.type === 'evidence' ? 'gateway' : frame.type === 'start' ? 'start' : 'end',
        state: frame.type === 'end' && run?.status === 'ERROR' ? 'fail' : 'ok',
        summary: frame.type === 'evidence' ? '证据包已生成' : frame.type === 'end' ? (run?.status === 'ERROR' ? '运行异常终止' : run?.status === 'CANCELLED' ? '已取消' : run?.status === 'BUDGET_EXHAUSTED' ? '预算用尽' : '本次执行已结束') : '开始处理冻结任务',
        startedAt: frame.occurred_at, events: [], x: 0, y: 0 });
      continue;
    }
    let node = byRef.get(event.native_tool_call_ref);
    if (!node) {
      const definition = tools[event.tool_name] ?? { title: event.tool_name, kind: 'tool' as const };
      node = { id: event.native_tool_call_ref, ...definition, tool: event.tool_name, state: 'running',
        summary: '调用中', startedAt: event.occurred_at, events: [], x: 0, y: 0 };
      nodes.push(node); byRef.set(node.id, node);
    }
    node.events.push(event);
    node.candidateId = event.candidate_id ?? node.candidateId;
    node.targetRunId = event.target_run_id ?? node.targetRunId;
    if (event.type === 'tool.completed') {
      node.endedAt = event.occurred_at;
      node.state = event.failed ? 'fail' : 'ok';
      node.summary = event.failed ? event.summary ?? '调用失败' : event.summary ?? (node.candidateId ? `候选：${node.candidateId}` : '调用已返回');
    }
  }
  if (ended) for (const node of nodes) if (node.state === 'running') {
    node.state = 'interrupted'; node.summary = '运行结束，未收到调用返回';
  }
  if (ended && run && ['COMPLETED', 'BUDGET_EXHAUSTED'].includes(run.status) && run.conclusion) {
    const feasible = run.conclusion === 'FEASIBLE_FOUND';
    const mitigation = run.conclusion === 'BEST_EFFORT_MITIGATION';
    const noFeasible = run.conclusion === 'NO_FEASIBLE_FOUND';
    nodes.push({ id: 'host-final-admission', title: '最终准入汇总', tool: '运行服务汇总', kind: 'condition',
      state: feasible || mitigation ? 'ok' : noFeasible ? 'fail' : 'wait',
      statusLabel: feasible ? '可推荐' : mitigation ? '尽力挽救 · 未完全达标' : noFeasible ? '已测范围无可行方案' : '证据不完整',
      summary: feasible ? `${run.selected_candidate_id}：名义约束、同方案扰动与原生记录均通过` :
        mitigation ? `${run.mitigation_candidate_id}：已交付基线改善方案；仍有准入未通过，不可推荐为完全达标` :
        noFeasible ? '已测候选未满足冻结约束；保留失败与改进证据' : '必要验收或关联证据尚不完整',
      candidateId: run.selected_candidate_id ?? run.mitigation_candidate_id, startedAt: frames.find(f => f.type === 'end')!.occurred_at,
      events: [], x: 0, y: 0 });
  }
  // Child IDs connect later simulations/verifications to the submission that created them.
  const childCandidates = new Map<string, string>();
  for (const node of nodes) if (node.tool === 'wg_submit_plan' && node.targetRunId && node.candidateId)
    childCandidates.set(node.targetRunId, node.candidateId);
  for (const node of nodes) node.candidateId ??= node.targetRunId ? childCandidates.get(node.targetRunId) : undefined;

  const edges: ExecutionEdge[] = [];
  let col = 0, row = 0, switches = 0, retries = 0;
  let latestSubmission: ExecutionNode | undefined;
  const previousCalls = new Map<string, ExecutionNode>();
  const submissions = new Map<string, ExecutionNode>();
  nodes.forEach((node, i) => {
    const key = `${node.tool}:${node.candidateId ?? node.targetRunId ?? ''}`;
    const previousCall = previousCalls.get(key);
    let branchFrom: ExecutionNode | undefined;
    let branchKind: ExecutionEdge['kind'] = 'order';
    if (node.events.length && previousCall?.state === 'fail') {
      node.branch = '失败后重试'; branchFrom = previousCall; branchKind = 'retry'; retries++;
    } else if (node.tool === 'wg_submit_plan' && latestSubmission && node.candidateId &&
               latestSubmission.candidateId && node.candidateId !== latestSubmission.candidateId) {
      const parent = node.events.find(e => e.parent_candidate_id)?.parent_candidate_id;
      branchFrom = parent ? submissions.get(parent) : latestSubmission;
      node.branch = parent ? '修改方案' : '切换方案'; branchKind = 'candidate'; switches++;
    }
    if (node.branch && col > 0) { row++; col = 0; }
    node.x = EXECUTION_LAYOUT.pad + col * EXECUTION_LAYOUT.dx;
    node.y = EXECUTION_LAYOUT.pad + row * EXECUTION_LAYOUT.dy;
    if (i > 0) edges.push({ from: nodes[i - 1].id, to: node.id, kind: 'order' });
    if (branchFrom) edges.push({ from: branchFrom.id, to: node.id, kind: branchKind, label: node.branch });
    if (node.tool === 'wg_submit_plan') {
      latestSubmission = node;
      if (node.candidateId) submissions.set(node.candidateId, node);
    }
    if (node.events.length) previousCalls.set(key, node);
    if (++col === columns) { col = 0; row++; }
  });
  return { nodes, edges, switches, retries,
    width: EXECUTION_LAYOUT.pad * 2 + Math.min(nodes.length || 1, columns) * EXECUTION_LAYOUT.dx - (EXECUTION_LAYOUT.dx - EXECUTION_LAYOUT.w),
    height: EXECUTION_LAYOUT.pad * 2 + (nodes.at(-1)?.y ?? EXECUTION_LAYOUT.pad) - EXECUTION_LAYOUT.pad + EXECUTION_LAYOUT.h };
}
