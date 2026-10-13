// Native AGH/Cordis business plugin. AGH owns tool choice and the model loop.
import { AsyncLocalStorage } from 'node:async_hooks'
import { verificationFeedback, resultFeedback, evidenceFeedback, refusalFeedback } from './feedback.mjs'
const context = new AsyncLocalStorage()
const Kind = Symbol.for('TypeBox.Kind')
const string = (maxLength = 128) => ({ [Kind]: 'String', type: 'string', minLength: 1, maxLength })
const object = (properties) => ({ [Kind]: 'Object', type: 'object', properties,
  required: Object.keys(properties), additionalProperties: false })

const base = () => {
  const url = new URL(process.env.WG_API_URL || 'http://127.0.0.1:8010')
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) throw new Error('WaterGuard API must be loopback')
  return url.origin
}
async function api(path, method = 'GET', body, refusalCode) {
  const response = await fetch(base() + path, {
    method, headers: { 'Content-Type': 'application/json', ...(context.getStore()?.session?.key
      ? { 'X-WG-AGH-Session': context.getStore().session.key } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(125000),
  })
  const value = await response.json()
  if (!response.ok && refusalCode && response.status >= 400 && response.status < 500)
    return refusalFeedback(value, response.status, refusalCode)
  if (!response.ok) throw new Error(`WaterGuard ${response.status}: ${JSON.stringify(value)}`)
  return value
}
const segment = (value) => encodeURIComponent(value)
const answer = (structured) => ({ content: [{ type: 'text', text: JSON.stringify(structured) }], structured })

export const waterguardTools = {
  inject: ['extension'],
  apply(ctx) {
    const extension = ctx.extension()
    const register = (name, description, properties, execute, readOnly = true) => extension.registerTool({
      name, description, parameters: object(properties),
      meta: { isReadOnly: readOnly, isDestructive: false, isConcurrencySafe: readOnly,
        isOpenWorld: false, replay: readOnly ? 'safe' : 'never', costHint: undefined,
        requiresApproval: 'never', deferLoading: false },
      async execute(parameters, toolContext) { return context.run(toolContext || {}, async () => answer(await execute(parameters))) },
    })
    register('wg_propose_task', 'Freeze a bounded public-network task from user-approved parameters. It cannot create field-control authority.',
      { network_id: string(), fault_kind: string(), candidate_limit: { [Kind]: 'Number', type: 'number', minimum: 1, maximum: 100 },
        demand_increase_pct: { [Kind]: 'Number', type: 'number', exclusiveMinimum: 0 } },
      ({ network_id, fault_kind, candidate_limit, demand_increase_pct }) => api('/api/tasks/from-proposal', 'POST', { network_id, fault_kind, candidate_limit, demand_increase_pct }), false)
    register('wg_inspect_network', 'Read the approved network topology, catalog and physical source. No field control.',
      { network_id: string() }, ({ network_id }) => api(`/api/networks/${segment(network_id)}`))
    register('wg_get_task', 'Read a user-frozen TaskSpec. You cannot lower its thresholds or overwrite its revision.',
      { task_id: string() }, ({ task_id }) => api(`/api/tasks/${segment(task_id)}`))
    register('wg_generate_candidates', 'Return bounded topology-ranked controls including staged restoration, under frozen action permissions. AGH selects from actual simulation feedback.',
      { task_id: string() }, ({ task_id }) => api(`/api/tasks/${segment(task_id)}/candidate-plans`))
    register('wg_diagnose_failure', 'Revalidate a completed nominal failure from original artifacts. Return exact failed node-time burden, worst nodes, nearby permitted facilities and frozen action times. facility_effects is observed from the failed run\'s original flows: for each permitted facility it lists which failed nodes lie downstream of its flow, their share of failed demand, and whether it feeds the fault; idle standby sources are listed too. Closing a facility that feeds failed nodes can worsen them. It is evidence, not a predicted benefit.',
      { run_id: string() }, ({ run_id }) => api(`/api/runs/${segment(run_id)}/failure-diagnosis`))
    register('wg_revise_plan', 'Revise controls after a valid same-session nominal failure. actions_json is an array of {kind,link_id,simulation_time_s}; use only frozen permissions/times, at most max_actions, and cite actual failure in reason. Server checks the source, action domain, native controls and revision budget; returns a new candidate ID, never a pass.',
      { task_id: string(), source_run_id: string(), actions_json: string(4096), reason: string(1000) },
      ({ task_id, source_run_id, actions_json, reason }) => api(`/api/tasks/${segment(task_id)}/candidate-revisions`, 'POST',
        { source_run_id, actions: JSON.parse(actions_json), reason }, 'INVALID_REVISION'), false)
    register('wg_jev_decide', 'Ask TypeSafe Jev for next-candidate routing probabilities and hydraulic risk from frozen server state. Call before selecting a candidate and after independent verification. If configured=false, continue Agnes planning.',
      { task_id: string() }, ({ task_id }) => api(`/api/tasks/${segment(task_id)}/jev-decision`, 'POST', {}), false)
    register('wg_request_clarification', 'Ask one concrete user question when the frozen task is materially ambiguous. The current AGH turn is stopped; a new linked session starts only after the user answers.',
      { task_id: string(), question: string(4000) }, ({ task_id, question }) => api(`/api/tasks/${segment(task_id)}/clarifications`, 'POST', { question }), false)
    register('wg_submit_plan', 'Submit exactly one candidate_id returned by wg_generate_candidates or wg_revise_plan. The server retrieves and validates the frozen ActionPlan and creates a queued calculation record.',
      { task_id: string(), candidate_id: string() }, async ({ task_id, candidate_id }) => {
        const run = await api(`/api/tasks/${segment(task_id)}/candidate-runs/${segment(candidate_id)}`, 'POST')
        if (run.accepted === false) return run
        const plans = await api(`/api/runs/${segment(run.run_id)}/candidates`)
        return { ...run, candidate_plan: plans.find(plan => plan.candidate_id === candidate_id) }
      }, false)
    register('wg_simulate_candidate', 'Dispatch only the submitted candidate. Returns actual hydraulic output; independent verification is a separate tool.',
      { run_id: string() }, async ({ run_id }) => {
        await api(`/api/runs/${segment(run_id)}/simulate`, 'POST')
        const deadline = Date.now() + 125000
        while (Date.now() < deadline) {
          const run = await api(`/api/runs/${segment(run_id)}`)
          if (['COMPLETED', 'ERROR', 'CANCELLED', 'BUDGET_EXHAUSTED'].includes(run.status)) {
            return { run, results: (await api(`/api/runs/${segment(run_id)}/results`)).map(row => resultFeedback(row, run_id)) }
          }
          await new Promise(resolve => setTimeout(resolve, 250))
        }
        throw new Error('Simulation did not reach a terminal state within the tool deadline')
      }, false)
    register('wg_verify_candidate', 'Independently recompute original hydraulic intervals. Returns exact admission and true violation counts plus the server-owned workflow. Follow workflow.required_next: first_aid lists fixed fast candidates to verify before any model search; a remaining livelihood_gap with refinement_required requires diagnosis and plan revision; refinement_required=false means compare and archive; a nominal pass requires same-plan stress.',
      { run_id: string() }, async ({ run_id }) => {
        const reports = await api(`/api/runs/${segment(run_id)}/verify`, 'POST')
        const workflow = await api(`/api/runs/${segment(run_id)}/workflow`)
        return reports.map(row => ({ ...verificationFeedback(row, run_id), workflow }))
      }, false)
    register('wg_create_perturbation', 'Create one bounded demand stress review for a completed submitted candidate. It creates a new queued run; call simulate and verify on that returned run.',
      { run_id: string(), demand_multiplier: { [Kind]: 'Number', type: 'number', exclusiveMinimum: 0 } },
      ({ run_id, demand_multiplier }) => api(`/api/runs/${segment(run_id)}/perturbations`, 'POST', { demand_multiplier }), false)
    register('wg_get_simulation', 'Read the persisted result and verification artifacts for one submitted candidate run.',
      { run_id: string() }, async ({ run_id }) => ({ run: await api(`/api/runs/${segment(run_id)}`), results: (await api(`/api/runs/${segment(run_id)}/results`)).map(row => resultFeedback(row, run_id)) }))
    register('wg_compare_candidates', 'Read comparable independently verified result records for submitted candidate runs. It never accepts model-supplied metrics or promotes an ineligible candidate.',
      { run_ids_json: string(8192) }, async ({ run_ids_json }) => {
        const runIds = JSON.parse(run_ids_json)
        if (!Array.isArray(runIds) || !runIds.length || !runIds.every(id => typeof id === 'string')) throw new Error('run_ids_json must be a non-empty JSON string array')
        return { candidates: await Promise.all(runIds.map(async run_id => ({ run_id, results: (await api(`/api/runs/${segment(run_id)}/results`)).map(row => resultFeedback(row, run_id)) }))) }
      })
    register('wg_build_evidence', 'Read a bounded inventory summary, provenance gaps, full manifest reference and download URL. Active inventories are preliminary; final archive follows termination. Export does not certify recommendation.',
      { run_id: string() }, async ({ run_id }) => ({ inventory_summary: evidenceFeedback(await api(`/api/runs/${segment(run_id)}/evidence`)),
        download_url: `${base()}/api/runs/${segment(run_id)}/evidence/download` }))
  },
}
