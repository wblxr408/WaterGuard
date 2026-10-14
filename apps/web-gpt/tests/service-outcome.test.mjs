import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as data from '../src/data.ts';

function candidate(id, actions, mean, emergency, shortage) {
  return { id, name: id, task: { task_id: 'task', revision: 1, constraint_hash: 'same',
    time_window: { evaluation_start_s: 3600, end_s: 10800 },
    emergency_floor: { min_pressure_m: 15, min_supply_ratio: .75 } }, plan: { actions },
    verification: { validity: 'valid', violations: [{ node_id: 'a' }, { node_id: 'a' }],
      metrics: { assessed_node_count: 2, fully_compliant_node_count: 1,
        mean_node_time_pass_ratio: mean, min_node_time_pass_ratio: 0,
        nodes_reaching_emergency_service: 1, emergency_mean_node_time_pass_ratio: emergency,
        emergency_min_node_time_pass_ratio: 0, shortage_m3: shortage,
        node_availability: [{ node_id: 'a', assessed_duration_s: 7200 }, { node_id: 'b', assessed_duration_s: 7200 }] } } };
}
const baseline = () => candidate('base', [], .5, .6, 100);
const rescue = () => candidate('rescue', [{ kind: 'pump_on' }], .8, .9, 40);
function outcome(c, candidates, run = null) { return data.serviceOutcome?.(c, { candidates, run }); }

test('selection changes effect comparison and separates affected nodes from violation records', () => {
  const b = baseline(), c = rescue(), run = { mitigation_candidate_id: c.id };
  const result = outcome(c, [b, c], run);
  assert.equal(result?.normal.deltaPp, 30);
  assert.equal(result.emergency.deltaPp, 30);
  assert.equal(result.shortageReduction, 60);
  assert.equal(result.residualNodeCount, 1);
  assert.equal(result.violationCount, 2);
  assert.equal(result.delivery, 'mitigation');
  const switched = outcome(b, [b, c], run);
  assert.equal(switched.delivery, 'candidate');
  assert.equal(switched.normal.mean, .5);
  assert.equal(switched.normal.deltaPp, 0);
  assert.equal(outcome(c, [b, c]).delivery, 'candidate');
});

test('no cross-window or cross-node baseline comparisons and missing data is not zero', () => {
  const b = baseline(), c = rescue();
  b.task.time_window.end_s = 12000;
  assert.equal(outcome(c, [b, c])?.baselineId, null);
  b.task.time_window.end_s = 10800;
  b.verification.metrics.node_availability[1].node_id = 'other';
  assert.equal(outcome(c, [b, c]).normal.deltaPp, null);
  delete c.verification.metrics.mean_node_time_pass_ratio;
  delete c.task.emergency_floor;
  assert.equal(outcome(c, [c]).normal.mean, null);
  assert.equal(outcome(c, [c]).emergency, null);
});

test('regression stays negative and a different emergency floor cannot produce a delta', () => {
  const b = baseline(), c = rescue();
  c.verification.metrics.mean_node_time_pass_ratio = .4;
  b.task.emergency_floor.min_pressure_m = 12;
  assert.equal(outcome(c, [b, c])?.normal.deltaPp, -10);
  assert.equal(outcome(c, [b, c]).emergency.deltaPp, null);
  c.verification.validity = 'invalid';
  assert.equal(outcome(c, [b, c]), null);
});

const gain = (over = {}) => ({ first_aid_candidate_id: 'web-candidate-1', refinement_candidate_id: 'agh-revision-1', improved: true,
  nodes_reaching_emergency_service: 3, priority_nodes_reaching_emergency_service: 0, emergency_mean_node_time_pass_ratio: .02,
  mean_node_time_pass_ratio: .01, shortage_reduction_m3: 4.5, raw_difference: {}, ...over });

test('delivery provenance names who produced the delivered plan and only claims measured model gain', () => {
  const refined = data.deliveryProvenance?.({ delivery: { source: 'refinement', refinement_gain: gain() } });
  assert.equal(refined.source, 'refinement');
  assert.equal(refined.claimsModelGain, true);
  assert.equal(refined.gain.nodes, 3);
  assert.equal(refined.gain.shortageM3, 4.5);

  const fixedWins = data.deliveryProvenance?.({ delivery: { source: 'first_aid',
    refinement_gain: gain({ improved: false, nodes_reaching_emergency_service: 0, shortage_reduction_m3: 0 }) } });
  assert.equal(fixedWins.source, 'first_aid');
  assert.equal(fixedWins.claimsModelGain, false);
  assert.equal(fixedWins.refinementTried, true);
});

test('delivery provenance is absent rather than zero when nothing was delivered or refined', () => {
  assert.equal(data.deliveryProvenance?.({ delivery: { source: null, refinement_gain: null } }), null);
  assert.equal(data.deliveryProvenance?.({}), null);
  const noModelSearch = data.deliveryProvenance?.({ delivery: { source: 'first_aid', refinement_gain: null } });
  assert.equal(noModelSearch.refinementTried, false);
  assert.equal(noModelSearch.gain, null);
});

test('emergency dispatch selects the verified preferred plan without changing full admission', () => {
  const b = baseline(), c = rescue();
  c.verification.admission = { recommendation_eligible: 'fail' };
  const w = { candidates: [b, c], run: { selected_candidate_id: null, mitigation_candidate_id: c.id },
    dispatch_recommendation: { candidate_id: c.id, tier: 'best_effort', fully_compliant: false } };
  const dispatch = data.recommendedDispatch?.(w);
  assert.equal(dispatch?.candidate.id, c.id);
  assert.equal(dispatch?.tier, 'best_effort');
  assert.equal(c.verification.admission.recommendation_eligible, 'fail');
  assert.equal(c.verification.violations.length, 2);
  const fallback = { ...w, run: { selected_candidate_id: null, mitigation_candidate_id: null },
    dispatch_recommendation: { candidate_id: b.id, tier: 'best_effort', fully_compliant: false } };
  assert.equal(data.deliveredCandidate(fallback).id, b.id);
  c.verification.validity = 'invalid';
  assert.equal(data.recommendedDispatch?.(w), null);
  assert.equal(data.recommendedDispatch?.({ candidates: [b, c] }), null);
});

test('lawful rescue remains recommended for emergency use despite unmet normal targets', () => {
  const b = baseline(), c = rescue();
  c.verification.admission = { action_valid: 'pass', simulation_valid: 'pass', nominal_constraints_pass: 'fail',
    required_stress_tests_pass: 'pending', recommendation_eligible: 'fail' };
  const w = { candidates: [b, c], run: { selected_candidate_id: null, mitigation_candidate_id: c.id },
    dispatch_recommendation: { candidate_id: c.id, tier: 'best_effort', fully_compliant: false } };
  const result = outcome(c, [b, c], w.run);
  assert.equal(data.dispatchStatus?.(c, w)?.kind, 'emergency');
  assert.equal(data.dispatchStatus(c, w).normalRestored, false);
  assert.equal(result.shortageReductionPct, 60);
  assert.equal(result.supplyGapCount, 2);
  assert.equal(result.residualNodeCount, 1);
  assert.equal(c.verification.admission.recommendation_eligible, 'fail');
  assert.equal(w.run.selected_candidate_id, null);
  assert.equal(data.dispatchStatus(b, w).kind, 'candidate');
});

test('illegal actions and invalid evidence cannot inherit a cached emergency recommendation', () => {
  const c = rescue();
  const w = { candidates: [c], run: { mitigation_candidate_id: c.id },
    dispatch_recommendation: { candidate_id: c.id, tier: 'best_effort', fully_compliant: false } };
  c.verification.admission = { action_valid: 'fail', simulation_valid: 'pass', recommendation_eligible: 'fail' };
  assert.equal(data.dispatchStatus?.(c, w)?.kind, 'illegal_action');
  assert.equal(data.recommendedDispatch(w), null);
  c.verification.admission.action_valid = 'pass';
  c.verification.validity = 'invalid';
  assert.equal(data.dispatchStatus(c, w).kind, 'invalid_evidence');
  assert.equal(data.recommendedDispatch(w), null);
});

test('rescue reduction percentage requires a positive comparable no-action shortage and preserves regressions', () => {
  const b = baseline(), c = rescue();
  c.verification.metrics.shortage_m3 = 130;
  assert.equal(outcome(c, [b, c]).shortageReductionPct, -30);
  b.verification.metrics.shortage_m3 = 0;
  assert.equal(outcome(c, [b, c]).shortageReductionPct, null);
  b.task.time_window.end_s = 12000;
  assert.equal(outcome(c, [b, c]).shortageReductionPct, null);
});
