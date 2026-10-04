import assert from 'node:assert/strict';
import { test } from 'node:test';
import { flowState } from '../src/agent-flow.ts';
import { mindState } from '../src/agent-mindmap.ts';

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
