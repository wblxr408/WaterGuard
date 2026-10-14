import assert from 'node:assert/strict';
import { test } from 'node:test';
import { flowState } from '../src/agent-flow.ts';
import { mindState } from '../src/agent-mindmap.ts';
import { executionFrames, executionGraph } from '../src/agent-execution.ts';
import { branchGraph } from '../src/agent-branches.ts';
import * as data from '../src/data.ts';

test('verification after simulation completion remains completed in both workflow views', () => {
  const run = { status: 'COMPLETED', phase: 'PACKAGING', conclusion: 'INCOMPLETE' };
  const events = [
    { type: 'run.started', task_revision: 1 },
    { type: 'candidate.submitted', candidate_id: 'candidate' },
    { type: 'simulation.completed', candidate_id: 'candidate' },
    { type: 'run.completed' },
    { type: 'verification.completed', candidate_id: 'candidate' },
  ];
  const candidates = new Map([['candidate', {
    name: '关闭管段 101',
    plan: { actions: [{ kind: 'link_close' }], source: 'enumeration' },
    result: { artifact: { status: 'SUCCEEDED' } },
    verification: { admission: { nominal_constraints_pass: 'fail', required_stress_tests_pass: 'pending' } },
  }]]);
  const flow = flowState(events, run, candidates, false);
  const mind = mindState(events, run, candidates, false);
  assert.equal(flow.nodes.verify.state, 'ok');
  assert.equal(flow.nodes.cond.state, 'fail');
  assert.equal(mind.nodes.check.state, 'ok');
  assert.ok(Object.values(flow.nodes).every(node => node.state !== 'running'));
  assert.ok(Object.values(mind.nodes).every(node => node.state !== 'running'));
});

test('a verification event without its report stays pending instead of inventing completion', () => {
  const run = { status: 'COMPLETED', phase: 'PACKAGING', conclusion: 'INCOMPLETE' };
  const events = [{ type: 'run.completed' }, { type: 'verification.completed', candidate_id: 'candidate' }];
  assert.equal(flowState(events, run, new Map(), false).nodes.verify.state, 'wait');
  assert.equal(mindState(events, run, new Map(), false).nodes.check.state, 'wait');
});

const event = (sequence, ref, tool_name, type = 'tool.started', extra = {}) => ({
  id: `${type}:${ref}`, sequence, native_tool_call_ref: ref, tool_name, type,
  occurred_at: new Date(Date.UTC(2026, 9, 6, 11, 0, sequence)).toISOString(), failed: false, ...extra,
});
const framesOf = events => executionFrames({ events, evidence: null }, []);

test('native invocations merge by ref, deduplicate reconnects, and never invent future roles', () => {
  const start = event(1, 'call-1', 'wg_get_task');
  const result = event(2, 'call-1', 'wg_get_task', 'tool.completed');
  const jev = event(3, 'call-2', 'wg_jev_decide');
  const frames = framesOf([jev, result, start, start]);
  const partial = executionGraph(frames.slice(0, 1), { status: 'COMPLETED' });
  assert.equal(partial.nodes.length, 1);
  assert.equal(partial.nodes[0].state, 'running'); // replay does not see future termination
  const graph = executionGraph(frames, { status: 'RUNNING' });
  assert.equal(graph.nodes.length, 2);
  assert.equal(graph.nodes[0].state, 'ok');
  assert.equal(graph.nodes[1].kind, 'jev');
  assert.equal(graph.nodes[1].state, 'running');
  assert.equal(graph.switches, 0);
  assert.equal(graph.retries, 0);
});

test('retry and new-candidate branches exist only after recorded subsequent invocations', () => {
  const events = [
    event(1, 'submit-a', 'wg_submit_plan', 'tool.started', { candidate_id: 'a' }),
    event(2, 'submit-a', 'wg_submit_plan', 'tool.completed', { candidate_id: 'a', target_run_id: 'child-a' }),
    event(3, 'sim-a', 'wg_simulate_candidate', 'tool.started', { target_run_id: 'child-a' }),
    event(4, 'sim-a', 'wg_simulate_candidate', 'tool.completed', { candidate_id: 'a', failed: true }),
  ];
  const failed = executionGraph(framesOf(events), null);
  assert.equal(failed.nodes.length, 2);
  assert.equal(failed.retries, 0);
  events.push(event(5, 'sim-a-again', 'wg_simulate_candidate', 'tool.started', { target_run_id: 'child-a' }));
  events.push(event(6, 'submit-b', 'wg_submit_plan', 'tool.started', { candidate_id: 'b', parent_candidate_id: 'a' }));
  const expanded = executionGraph(framesOf(events), null);
  assert.equal(expanded.retries, 1);
  assert.equal(expanded.switches, 1);
  assert.ok(expanded.edges.some(e => e.kind === 'retry' && e.from === 'sim-a' && e.to === 'sim-a-again'));
  assert.ok(expanded.edges.some(e => e.kind === 'candidate' && e.from === 'submit-a' && e.to === 'submit-b'));
  assert.ok(expanded.nodes[3].y > expanded.nodes[2].y);
});

test('nominal rejection is a returned verification; no phantom revision or pending packaging', () => {
  const calls = [event(2, 'verify', 'wg_verify_candidate'), event(3, 'verify', 'wg_verify_candidate', 'tool.completed', { summary: '名义约束未通过' })];
  const frames = executionFrames({ events: calls, evidence: { bundle_id: 'bundle-actual', occurred_at: event(5).occurred_at } }, [
    { type: 'run.started', occurred_at: event(1).occurred_at },
    { type: 'run.started', occurred_at: event(1).occurred_at },
    { type: 'run.completed', occurred_at: event(4).occurred_at },
  ]);
  const graph = executionGraph(frames, { status: 'COMPLETED', conclusion: 'INCOMPLETE' });
  assert.equal(graph.nodes.length, 5);
  assert.equal(graph.nodes[1].state, 'ok');
  assert.equal(graph.nodes[2].state, 'ok');
  assert.equal(graph.nodes[3].state, 'ok');
  assert.equal(graph.nodes[4].id, 'host-final-admission');
  assert.equal(graph.nodes[4].state, 'wait');
  assert.equal(graph.switches, 0);
  assert.equal(graph.retries, 0);
});

test('final host admission follows termination and preserves historical pending gates', () => {
  const calls = [event(1, 'verify', 'wg_verify_candidate'), event(2, 'verify', 'wg_verify_candidate', 'tool.completed', {
    candidate_id: 'a', assessment: { validity: 'valid', admission: { nominal_constraints_pass: 'pass', required_stress_tests_pass: 'pending' }, metrics: {}, violation_count: 0 },
  })];
  const trace = { events: calls, evidence: null };
  const run = { status: 'COMPLETED', conclusion: 'FEASIBLE_FOUND', selected_candidate_id: 'a' };
  assert.ok(!executionGraph(executionFrames(trace, []), run).nodes.some(n => n.id === 'host-final-admission'));
  const frames = executionFrames(trace, [{ type: 'run.completed', occurred_at: event(3).occurred_at }]);
  const graph = branchGraph(frames, run);
  assert.equal(graph.nodes.find(n => n.id === 'host-final-admission').state, 'ok');
  assert.equal(graph.nodes.find(n => n.id === 'host-final-admission').events.length, 0);
  assert.equal(graph.nodes.find(n => n.id.endsWith('required_stress_tests_pass')).state, 'wait');
  assert.equal(new Set(graph.nodes.flatMap(n => n.events.map(e => e.native_tool_call_ref))).size, 1);
});

test('terminal interruption and unconfigured Jev preserve actual return state', () => {
  const frames = executionFrames({ events: [event(1, 'jev', 'wg_jev_decide'),
    event(2, 'jev', 'wg_jev_decide', 'tool.completed', { jev_configured: false, summary: '未连接 · Agnes 继续规划' }),
    event(3, 'simulation', 'wg_simulate_candidate')], evidence: null }, [{ type: 'run.completed', occurred_at: event(4).occurred_at }]);
  const graph = executionGraph(frames, { status: 'ERROR' });
  assert.equal(graph.nodes[0].state, 'ok');
  assert.equal(graph.nodes[1].state, 'interrupted');
  assert.equal(graph.nodes[2].state, 'fail');
});

const offered = id => ({ candidate_id: id, parent_candidate_id: null, actions: [] });
test('opening a completed rescue selects the delivered candidate rather than the last tested one', () => {
  const candidates = [{ id: 'baseline' }, { id: 'rescue' }, { id: 'last-tested' }];
  assert.equal(data.deliveredCandidate?.({ candidates, run: { mitigation_candidate_id: 'rescue', selected_candidate_id: null } })?.id, 'rescue');
});
test('emergency rescue ends as delivered without rewriting full normal-service admission', () => {
  const run = { status: 'COMPLETED', conclusion: 'BEST_EFFORT_MITIGATION', selected_candidate_id: null, mitigation_candidate_id: 'rescue' };
  const frames = executionFrames({ events: [], evidence: null }, [{ type: 'run.completed', occurred_at: event(1).occurred_at }]);
  for (const graph of [executionGraph(frames, run), branchGraph(frames, run)]) {
    const final = graph.nodes.find(n => n.id === 'host-final-admission');
    assert.equal(final.state, 'ok');
    assert.equal(final.candidateId, 'rescue');
    assert.equal(run.selected_candidate_id, null);
  }
});
test('wall budget termination still exposes the verified mitigation in both workflow views', () => {
  const run = { status: 'BUDGET_EXHAUSTED', conclusion: 'BEST_EFFORT_MITIGATION', selected_candidate_id: null, mitigation_candidate_id: 'rescue' };
  const frames = executionFrames({ events: [], evidence: null }, [{ type: 'run.completed', occurred_at: event(1).occurred_at }]);
  for (const graph of [executionGraph(frames, run), branchGraph(frames, run)]) {
    const final = graph.nodes.find(n => n.id === 'host-final-admission');
    assert.equal(final?.candidateId, 'rescue');
    assert.equal(final.state, 'ok');
    assert.equal(run.status, 'BUDGET_EXHAUSTED');
  }
});
test('candidate branches appear only after the planning return and stay unassessed until selected', () => {
  const frames = framesOf([event(1, 'generate', 'wg_generate_candidates'),
    event(2, 'generate', 'wg_generate_candidates', 'tool.completed', { candidates: [offered('a'), offered('b')] }),
    event(3, 'submit-b', 'wg_submit_plan', 'tool.started', { candidate_id: 'b' })]);
  assert.equal(branchGraph(frames.slice(0, 1), null).nodes.filter(n => n.kind === 'candidate').length, 0);
  const generated = branchGraph(frames.slice(0, 2), null);
  assert.equal(generated.nodes.filter(n => n.kind === 'candidate').length, 2);
  assert.ok(generated.nodes.filter(n => n.kind === 'candidate').every(n => n.state === 'idle'));
  const chosen = branchGraph(frames, null);
  assert.equal(chosen.nodes.find(n => n.id === 'candidate:a').statusLabel, '未评估');
  assert.equal(chosen.nodes.find(n => n.id === 'candidate:b').statusLabel, '已选择');
  assert.ok(chosen.edges.some(e => e.from === 'candidate:b' && e.to === 'submit-b'));
});

test('returned verification expands two real gates while preserving native calls and absent Jev choice', () => {
  const calls = [event(1, 'generate', 'wg_generate_candidates'),
    event(2, 'generate', 'wg_generate_candidates', 'tool.completed', { candidates: [offered('a')] }),
    event(3, 'submit', 'wg_submit_plan', 'tool.started', { candidate_id: 'a' }),
    event(4, 'submit', 'wg_submit_plan', 'tool.completed', { target_run_id: 'child', candidate_id: 'a' }),
    event(5, 'sim', 'wg_simulate_candidate', 'tool.started', { target_run_id: 'child' }),
    event(6, 'sim', 'wg_simulate_candidate', 'tool.completed', { candidate_id: 'a' }),
    event(7, 'verify', 'wg_verify_candidate', 'tool.started', { target_run_id: 'child' }),
    event(8, 'verify', 'wg_verify_candidate', 'tool.completed', { candidate_id: 'a', assessment: {
      validity: 'valid', admission: { nominal_constraints_pass: 'pass', required_stress_tests_pass: 'pending' }, metrics: { min_pressure_m: 13.5 }, violation_count: 0 } }),
    event(9, 'jev', 'wg_jev_decide'), event(10, 'jev', 'wg_jev_decide', 'tool.completed', { jev_configured: false })];
  assert.equal(branchGraph(framesOf(calls.slice(0, 7)), null).nodes.filter(n => n.kind === 'condition').length, 0);
  const graph = branchGraph(framesOf(calls), null);
  assert.deepEqual(graph.nodes.filter(n => n.kind === 'condition').map(n => n.state), ['ok', 'wait']);
  const evaluation = graph.nodes.find(n => n.title === '仿真与验收');
  assert.equal(evaluation.state, 'ok');
  assert.equal(new Set(evaluation.events.map(e => e.native_tool_call_ref)).size, 2);
  assert.equal(new Set(graph.nodes.flatMap(n => n.events.map(e => e.native_tool_call_ref))).size, 5);
  assert.ok(!graph.edges.some(e => e.label === 'Jev 选择'));
  calls.at(-1).jev_configured = true;
  calls.at(-1).jev_choice = 'a';
  assert.ok(branchGraph(framesOf(calls), null).edges.some(e => e.from === 'jev' && e.to === 'candidate:a' && e.label === 'Jev 选择'));
});

test('semantic graph preserves actual retry and plan-switch edges without merging attempts', () => {
  const graph = branchGraph(framesOf([
    event(1, 'submit-a', 'wg_submit_plan', 'tool.started', { candidate_id: 'a' }),
    event(2, 'submit-a', 'wg_submit_plan', 'tool.completed', { candidate_id: 'a', target_run_id: 'child' }),
    event(3, 'sim-fail', 'wg_simulate_candidate', 'tool.started', { target_run_id: 'child' }),
    event(4, 'sim-fail', 'wg_simulate_candidate', 'tool.completed', { failed: true }),
    event(5, 'sim-retry', 'wg_simulate_candidate', 'tool.started', { target_run_id: 'child' }),
    event(6, 'submit-b', 'wg_submit_plan', 'tool.started', { candidate_id: 'b', parent_candidate_id: 'a' }),
  ]), null);
  assert.equal(graph.retries, 1);
  assert.equal(graph.switches, 1);
  assert.ok(graph.edges.some(e => e.kind === 'retry' && e.from === 'evaluation:sim-fail' && e.to === 'evaluation:sim-retry'));
  assert.ok(graph.edges.some(e => e.kind === 'candidate' && e.from === 'submit-a' && e.to === 'submit-b'));
  assert.equal(graph.nodes.find(n => n.id === 'candidate:b').summary, '候选操作尚未返回');
});

test('controlled invalid-action feedback remains a rejected action in both execution views', () => {
  const frames = framesOf([event(1, 'bad', 'wg_submit_plan', 'tool.started', { candidate_id: 'invalid' }),
    event(2, 'bad', 'wg_submit_plan', 'tool.completed', { candidate_id: 'invalid', failed: true, summary: '动作被拒绝 · INVALID_ACTION' })]);
  const ordered = executionGraph(frames, null).nodes[0];
  const branched = branchGraph(frames, null).nodes.find(n => n.id === 'bad');
  assert.equal(ordered.state, 'fail');
  assert.equal(branched.state, 'fail');
  assert.equal(ordered.summary, '动作被拒绝 · INVALID_ACTION');
  assert.equal(branched.summary, ordered.summary);
});

test('actual plan revision preserves returned controls and parent lineage in the graph', () => {
  const revised = { candidate_id: 'revised', parent_candidate_id: 'original', actions: [
    { kind: 'pump_on', link_id: '10', simulation_time_s: 4500 }] };
  const frames = framesOf([
    event(1, 'first', 'wg_submit_plan', 'tool.started', { candidate_id: 'original' }),
    event(2, 'first', 'wg_submit_plan', 'tool.completed', { candidate_id: 'original', target_run_id: 'source' }),
    event(3, 'diagnose', 'wg_diagnose_failure', 'tool.started', { target_run_id: 'source' }),
    event(4, 'diagnose', 'wg_diagnose_failure', 'tool.completed', { candidate_id: 'original', summary: '实际失效' }),
    event(5, 'revision', 'wg_revise_plan', 'tool.started'),
    event(6, 'revision', 'wg_revise_plan', 'tool.completed', { candidate_id: 'revised', parent_candidate_id: 'original', candidate_plan: revised }),
    event(7, 'second', 'wg_submit_plan', 'tool.started', { candidate_id: 'revised' }),
    event(8, 'second', 'wg_submit_plan', 'tool.completed', { candidate_id: 'revised', parent_candidate_id: 'original', candidate_plan: revised, target_run_id: 'new-child' }),
  ]);
  const ordered = executionGraph(frames, null);
  assert.equal(ordered.nodes.find(n => n.id === 'revision').title, '修订动作与时序');
  assert.equal(ordered.nodes.find(n => n.id === 'second').branch, '修改方案');
  const graph = branchGraph(frames, null);
  assert.deepEqual(graph.nodes.find(n => n.id === 'candidate:revised').candidatePlan, revised);
  assert.ok(graph.edges.some(e => e.kind === 'candidate' && e.to === 'second'));
});
