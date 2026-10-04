// Validates official tool declarations and exercises their real local HTTP effects.
// This is integration acceptance, not a model-generated AGH Live attestation.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { mkdirSync, writeFileSync } from 'node:fs'
import { waterguardTools } from '../packages/waterguard-agh/index.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const require = createRequire(join(root, 'vendor/agnes-harness/packages/cli/package.json'))
const { checkToolDef } = await import(pathToFileURL(require.resolve('@agnes/extension-api')).href)
const tools = new Map()
waterguardTools.apply({ extension: () => ({ registerTool(def) {
  assert.deepEqual(checkToolDef(def), { ok: true }, def.name)
  tools.set(def.name, def)
} }) })
assert.equal(tools.size, 7)
const report = { status: 'RUNNING', checks: ['Seven tool definitions pass official checkToolDef'], runs: [] }
const base = process.env.WG_API_URL || 'http://127.0.0.1:8000'
async function call(name, args) {
  return (await tools.get(name).execute(args)).structured
}
async function request(path, method = 'GET', body, expected = 200) {
  const response = await fetch(base + path, { method, headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  assert.equal(response.status, expected, await response.clone().text())
  return response.json()
}
try {
  for (const network_id of ['epa-net1', 'epa-net3']) {
    const network = await call('wg_inspect_network', { network_id })
    assert.ok(network.catalog)
    const task = await request('/api/tasks/from-proposal', 'POST', { network_id, fault_kind: 'normal', candidate_limit: 2 }, 201)
    assert.deepEqual(await call('wg_get_task', { task_id: task.task_id }), task)
    const plans = await call('wg_generate_candidates', { task_id: task.task_id })
    assert.ok(plans.length > 0)
    const plan = plans[0]
    const corrupt = { ...plan, plan_hash: 'sha256:' + '0'.repeat(64) }
    await assert.rejects(call('wg_submit_plan', { task_id: task.task_id, plan_json: JSON.stringify(corrupt) }), /WaterGuard 422/)
    const run = await call('wg_submit_plan', { task_id: task.task_id, plan_json: JSON.stringify(plan) })
    assert.equal(run.status, 'QUEUED')
    await request(`/api/runs/${run.run_id}/verify`, 'POST', undefined, 409)
    const simulation = await call('wg_simulate_candidate', { run_id: run.run_id })
    assert.equal(simulation.run.status, 'COMPLETED')
    assert.equal(simulation.results.length, 1)
    assert.equal(simulation.results[0].artifact.status, 'SUCCEEDED')
    assert.equal(simulation.results[0].verification, null)
    const verification = await call('wg_verify_candidate', { run_id: run.run_id })
    assert.equal(verification.length, 1)
    assert.equal(verification[0].admission.simulation_valid, 'pass')
    const evidence = await call('wg_build_evidence', { run_id: run.run_id })
    assert.equal(evidence.manifest.live_export_allowed, false)
    assert.ok(evidence.manifest.artifacts.some(a => a.ref.endsWith('/verification.json')))
    const download = await fetch(evidence.download_url)
    assert.equal(download.status, 200)
    assert.ok((await download.arrayBuffer()).byteLength > 1000)
    assert.deepEqual(await call('wg_get_task', { task_id: task.task_id }), task)
    report.runs.push(run.run_id)
    report.checks.push(`${network_id}: submitted plan stays queued, real simulation precedes independent verification, frozen task preserved, evidence ZIP verified by backend`)
  }
  report.status = 'PASSED'
} catch (error) {
  report.status = 'FAILED'
  report.error = String(error)
  process.exitCode = 1
} finally {
  mkdirSync(join(root, 'artifacts/agh-integration'), { recursive: true })
  writeFileSync(join(root, 'artifacts/agh-integration/tools.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report, null, 2))
}
