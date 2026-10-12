// Bounded model feedback. Full results and reports remain immutable API artifacts.
export const FEEDBACK_VERSION = 'waterguard-tool-feedback/1.0.0'

export function missingClosureTools(events) {
  const calls = new Map(events.filter(e => e.type === 'tool/call').map(e => [e.data.toolUseId, e.data.name]))
  const lastReview = events.findLastIndex(e => e.type === 'tool/result' && !e.data.isError && calls.get(e.data.toolUseId) === 'wg_verify_candidate')
  const success = new Set(events.slice(lastReview + 1).filter(e => e.type === 'tool/result' && !e.data.isError).map(e => calls.get(e.data.toolUseId)))
  return ['wg_compare_candidates', 'wg_build_evidence'].filter(name => !success.has(name))
}

export function unfinishedWorkflow(events, parentRunId) {
  const calls = new Map(events.filter(e => e.type === 'tool/call').map(e => [e.data.toolUseId, e.data.name]))
  for (const event of [...events].reverse()) {
    if (event.type !== 'tool/result' || event.data.isError || calls.get(event.data.toolUseId) !== 'wg_verify_candidate') continue
    const report = event.data.structured?.[0]
    const workflow = report?.workflow
    if (workflow?.parent_run_id !== parentRunId) continue
    if (workflow.required_next === 'compare_and_archive' || !(workflow.remaining_simulations > 0)) return null
    if (workflow.required_next === 'choose_candidate' && !workflow.candidate_ids?.length && !(workflow.revision_budget_remaining > 0)) return null
    return workflow
  }
  return null
}

export function verificationFeedback(report, runId) {
  const violations = report.violations || []
  const groups = {}
  for (const row of violations) {
    const group = groups[row.code] ||= { interval_count: 0, affected_nodes: new Set(),
      constraint_node_time_s: 0, first_s: row.start_s, last_s: row.end_s, worst_interval: row }
    group.interval_count++
    group.affected_nodes.add(row.node_id)
    group.constraint_node_time_s += row.end_s - row.start_s
    group.first_s = Math.min(group.first_s, row.start_s)
    group.last_s = Math.max(group.last_s, row.end_s)
    if (row.observed < group.worst_interval.observed) group.worst_interval = row
  }
  const excerpts = []
  for (const code of Object.keys(groups).sort()) {
    excerpts.push(...violations.filter(v => v.code === code)
      .sort((a, b) => a.observed - b.observed || a.start_s - b.start_s || a.node_id.localeCompare(b.node_id)).slice(0, 6))
  }
  const diagnostics = Object.fromEntries(Object.entries(groups).map(([code, group]) =>
    [code, { ...group, affected_nodes: group.affected_nodes.size }]))
  const { violations: _full, ...identityAndAssessment } = report
  return { ...identityAndAssessment, violations: excerpts, violation_count: violations.length,
    constraint_diagnostics: diagnostics,
    feedback: { version: FEEDBACK_VERSION, kind: 'independent_report_summary',
      violations_returned: excerpts.length, violations_truncated: excerpts.length < violations.length,
      full_report_ref: `artifact:${runId}/candidates/${report.candidate_id}/verification.json`,
      interpretation: 'Metrics and admission copied from the independent report. Excerpts are not complete violation coverage.' } }
}

export function resultFeedback(record, runId) {
  const compact = { plan: record.plan, artifact: record.artifact }
  if (record.diagnostics) {
    const d = record.diagnostics
    compact.diagnostics = { converged: d.converged, errors: d.errors, warnings: (d.warnings || []).slice(0, 8),
      warning_count: (d.warnings || []).length }
  }
  if (record.verification) compact.verification = verificationFeedback(record.verification, runId)
  compact.feedback = { version: FEEDBACK_VERSION, kind: 'stored_simulation_summary',
    full_result_ref: `artifact:${runId}/candidates/${record.plan.candidate_id}/result.json`,
    series_omitted: true, interpretation: 'Original time series stays in the saved simulation; call independent verification.' }
  return compact
}

export function evidenceFeedback(manifest) {
  return { version: FEEDBACK_VERSION, kind: 'evidence_inventory_summary',
    run_id: manifest.run_id, bundle_id: manifest.bundle_id, mode: manifest.mode,
    native_trace_ref: manifest.native_trace_ref, association_ref: manifest.association_ref,
    artifact_count: manifest.artifacts.length, missing_items: manifest.missing_items,
    live_export_allowed: manifest.live_export_allowed,
    full_manifest_ref: `artifact:${manifest.run_id}/evidence-manifest.json`,
    interpretation: manifest.missing_items.includes('execution_in_progress')
      ? 'Execution is active. This is a preliminary inventory; final archive and host admission follow execution termination.'
      : 'Final inventory is stored separately. Export provenance does not certify hydraulic recommendation.' }
}
// A received 4xx is a known business refusal, not an unknown network outcome.
// Transport failures and 5xx still throw and retain their ambiguous status.
export function refusalFeedback(value, httpStatus, code) {
  const detail = value?.detail
  const message = typeof detail?.message === 'string' ? detail.message :
    Array.isArray(detail) ? detail.map(field => `${(field.loc || []).join('.')}: ${field.msg}`).join('; ') :
    'Request was explicitly refused by the server.'
  return { accepted: false, code, http_status: httpStatus, message }
}

