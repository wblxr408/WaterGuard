// Native AGH/Cordis business plugin. AGH owns tool choice and the model loop.
import { AsyncLocalStorage } from 'node:async_hooks'
const context = new AsyncLocalStorage()
const Kind = Symbol.for('TypeBox.Kind')
const string = (maxLength = 128) => ({ [Kind]: 'String', type: 'string', minLength: 1, maxLength })
const object = (properties) => ({ [Kind]: 'Object', type: 'object', properties,
  required: Object.keys(properties), additionalProperties: false })

const base = () => {
  const url = new URL(process.env.WG_API_URL || 'http://127.0.0.1:8000')
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) throw new Error('WaterGuard API must be loopback')
  return url.origin
}
async function api(path, method = 'GET', body) {
  const response = await fetch(base() + path, {
    method, headers: { 'Content-Type': 'application/json', ...(context.getStore()?.session?.key
      ? { 'X-WG-AGH-Session': context.getStore().session.key } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(125000),
  })
  const value = await response.json()
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
    register('wg_inspect_network', 'Read the approved network topology, catalog and physical source. No field control.',
      { network_id: string() }, ({ network_id }) => api(`/api/networks/${segment(network_id)}`))
    register('wg_get_task', 'Read a user-frozen TaskSpec. You cannot lower its thresholds or overwrite its revision.',
      { task_id: string() }, ({ task_id }) => api(`/api/tasks/${segment(task_id)}`))
    register('wg_generate_candidates', 'Return bounded zero/one-action proposals for a frozen task. AGH chooses what to submit.',
      { task_id: string() }, ({ task_id }) => api(`/api/tasks/${segment(task_id)}/candidate-plans`))
    register('wg_jev_decide', 'Ask TypeSafe Jev for next-candidate routing probabilities and hydraulic risk from frozen server state. Call before selecting a candidate and after independent verification. If configured=false, continue Agnes planning.',
      { task_id: string() }, ({ task_id }) => api(`/api/tasks/${segment(task_id)}/jev-decision`, 'POST', {}), false)
    register('wg_submit_plan', 'Preflight one ActionPlan JSON against the server-frozen task; creates a queued calculation record without dispatch.',
      { task_id: string(), plan_json: string(32768) }, ({ task_id, plan_json }) => api(`/api/tasks/${segment(task_id)}/candidate-runs`, 'POST', JSON.parse(plan_json)), false)
    register('wg_simulate_candidate', 'Dispatch only the submitted candidate. Returns actual hydraulic output; independent verification is a separate tool.',
      { run_id: string() }, async ({ run_id }) => {
        await api(`/api/runs/${segment(run_id)}/simulate`, 'POST')
        const deadline = Date.now() + 125000
        while (Date.now() < deadline) {
          const run = await api(`/api/runs/${segment(run_id)}`)
          if (['COMPLETED', 'ERROR', 'CANCELLED', 'BUDGET_EXHAUSTED'].includes(run.status)) {
            return { run, results: await api(`/api/runs/${segment(run_id)}/results`) }
          }
          await new Promise(resolve => setTimeout(resolve, 250))
        }
        throw new Error('Simulation did not reach a terminal state within the tool deadline')
      }, false)
    register('wg_verify_candidate', 'Independently recompute metrics and violations from the stored actual simulation. No model-authored pass flags.',
      { run_id: string() }, ({ run_id }) => api(`/api/runs/${segment(run_id)}/verify`, 'POST'), false)
    register('wg_build_evidence', 'Read the hash inventory, native provenance gaps, and download URL. Does not certify missing AGH traces.',
      { run_id: string() }, async ({ run_id }) => ({ manifest: await api(`/api/runs/${segment(run_id)}/evidence`),
        download_url: `${base()}/api/runs/${segment(run_id)}/evidence/download` }))
  },
}
