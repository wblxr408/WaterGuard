import assert from 'node:assert/strict'
import { test } from 'node:test'
import { verificationFeedback, resultFeedback, evidenceFeedback, missingClosureTools, refusalFeedback, unfinishedWorkflow } from '../feedback.mjs'

test('a premature model finish preserves pending server work and requires fresh closure after more verification', () => {
  const workflow = { parent_run_id: 'parent', required_next: 'choose_candidate', remaining_simulations: 6,
    candidate_ids: ['untested'], revision_budget_remaining: 0 }
  const call = (id, name) => ({ type: 'tool/call', data: { toolUseId: id, name } })
  const result = (id, structured) => ({ type: 'tool/result', data: { toolUseId: id, structured } })
  const events = [call('c', 'wg_compare_candidates'), result('c', {}), call('b', 'wg_build_evidence'), result('b', {}),
    call('v', 'wg_verify_candidate'), result('v', [{ workflow }])]
  assert.deepEqual(unfinishedWorkflow(events, 'parent'), workflow)
  assert.equal(unfinishedWorkflow(events, 'other-parent'), null)
  assert.deepEqual(missingClosureTools(events), ['wg_compare_candidates', 'wg_build_evidence'])
  workflow.required_next = 'compare_and_archive'
  assert.equal(unfinishedWorkflow(events, 'parent'), null)
  workflow.required_next = 'create_perturbation'
  workflow.demand_multiplier = 1.075
  assert.equal(unfinishedWorkflow(events, 'parent').demand_multiplier, 1.075)
  workflow.remaining_simulations = 0
  assert.equal(unfinishedWorkflow(events, 'parent'), null)
})

test('a pending first-aid stage keeps the session working, and a no-gap archive verdict ends the search', () => {
  const call = (id, name) => ({ type: 'tool/call', data: { toolUseId: id, name } })
  const result = (id, structured) => ({ type: 'tool/result', data: { toolUseId: id, structured } })
  const workflow = { parent_run_id: 'parent', required_next: 'first_aid', remaining_simulations: 7,
    first_aid_candidate_ids: ['web-candidate-1', 'web-candidate-2'] }
  const events = [call('v', 'wg_verify_candidate'), result('v', [{ workflow }])]
  assert.deepEqual(unfinishedWorkflow(events, 'parent'), workflow)
  workflow.required_next = 'compare_and_archive'
  workflow.refinement_required = false
  assert.equal(unfinishedWorkflow(events, 'parent'), null)
})

test('received gateway refusals retain the actionable reason instead of claiming an unknown outcome', () => {
  const refusal = refusalFeedback({ detail: { code: 'INPUT_INVALID', message: 'Unauthorized revision facility: 330' } }, 422, 'INVALID_REVISION')
  assert.deepEqual(refusal, { accepted: false, code: 'INVALID_REVISION', http_status: 422, message: 'Unauthorized revision facility: 330' })
  const fields = refusalFeedback({ detail: [{ loc: ['body', 'actions', 0, 'simulation_time_s'], msg: 'Input should be a valid integer' }] }, 422, 'INVALID_REVISION')
  assert.match(fields.message, /actions.0.simulation_time_s/)
  assert.equal(fields.accepted, false)
})

test('large failure feedback keeps exact verdict and total, with bounded real witnesses', () => {
  const violations = Array.from({ length: 5000 }, (_, i) => ({ node_id: String(i % 100),
    code: i % 2 ? 'MIN_PRESSURE' : 'MIN_SUPPLY_RATIO', unit: i % 2 ? 'm' : 'ratio',
    start_s: i * 75, end_s: (i + 1) * 75, threshold: i % 2 ? 20 : .95, observed: i % 2 ? 10-i*.001 : .7-i*.00001 }))
  const report = { candidate_id: 'a', simulation_id: 'sim-real', metrics: { shortage_m3: 300 },
    admission: { nominal_constraints_pass: 'fail', recommendation_eligible: 'fail' }, violations }
  const before = JSON.stringify(report)
  const summary = verificationFeedback(report, 'run-real')
  assert.equal(JSON.stringify(report), before)
  assert.deepEqual(summary.metrics, report.metrics)
  assert.deepEqual(summary.admission, report.admission)
  assert.equal(summary.violation_count, 5000)
  assert.equal(summary.violations.length, 12)
  assert.equal(summary.feedback.violations_truncated, true)
  assert.ok(summary.violations.every(v => violations.includes(v)))
  assert.equal(summary.constraint_diagnostics.MIN_PRESSURE.constraint_node_time_s, 2500 * 75)
  assert.equal(summary.constraint_diagnostics.MIN_PRESSURE.worst_interval.observed, Math.min(...violations.filter(v => v.code === 'MIN_PRESSURE').map(v => v.observed)))
  assert.ok(JSON.stringify(summary).length < before.length / 30)
})

test('simulation summary retains binding and points to full series without forging verification', () => {
  const record = { plan: { candidate_id: 'a' }, artifact: { simulation_id: 's', status: 'SUCCEEDED', input_hash: 'frozen' },
    series: { frames: Array(10000).fill({}) } }
  const summary = resultFeedback(record, 'run-real')
  assert.equal(summary.artifact, record.artifact)
  assert.ok(!('series' in summary))
  assert.ok(!('verification' in summary))
  assert.equal(summary.feedback.full_result_ref, 'artifact:run-real/candidates/a/result.json')
})

test('active inventory remains preliminary and cannot be presented as certified export', () => {
  const manifest = { run_id: 'run-real', artifacts: Array(4000).fill({ sha256: 'real' }),
    missing_items: ['execution_in_progress'], live_export_allowed: false }
  const summary = evidenceFeedback(manifest)
  assert.equal(summary.artifact_count, 4000)
  assert.equal(summary.live_export_allowed, false)
  assert.ok(summary.interpretation.includes('preliminary'))
  assert.ok(!('artifacts' in summary))
})

test('failed and orphan returns cannot satisfy the native closure', () => {
  const events = [{ type: 'tool/call', data: { toolUseId: 'a', name: 'wg_compare_candidates' } },
    { type: 'tool/result', data: { toolUseId: 'a', isError: true } },
    { type: 'tool/result', data: { toolUseId: 'orphan' } }]
  assert.deepEqual(missingClosureTools(events), ['wg_compare_candidates', 'wg_build_evidence'])
  events.push({ type: 'tool/result', data: { toolUseId: 'a', isError: false } })
  assert.deepEqual(missingClosureTools(events), ['wg_build_evidence'])
})
