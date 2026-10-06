// Uses the current official public Node SDK. No key is written to command arguments or reports.
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { randomUUID } from 'node:crypto'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const source = process.env.WG_AGH_SOURCE || join(root, 'vendor/agnes-harness')
const entry = join(source, 'packages/cli/dist/local/agnes.mjs')
const require = createRequire(join(source, 'packages/cli/package.json'))
const { createClient, memoryJournal } = await import(pathToFileURL(require.resolve('@agnes/sdk')).href)
const aghHome = resolve(process.env.WG_AGH_HOME || join(root, '.cache/agh-waterguard'))
const env = { ...process.env, AGH_HOME: aghHome, AGNES_PROFILE: 'local-dev' }
process.env.AGH_HOME = env.AGH_HOME
process.env.AGNES_PROFILE = env.AGNES_PROFILE
const status = JSON.parse(execFileSync(process.execPath, [entry, 'daemon', 'status', '--profile', 'local-dev'],
  { env, encoding: 'utf8' }))
if (!status.running || !status.socketReachable) throw new Error('Start the WaterGuard AGH daemon first')
const client = createClient({ transport: { kind: 'unix', path: status.owner.socketPath,
  serverIdentity: { pid: status.owner.pid, processStartId: status.owner.processStartId } },
auth: { kind: 'local' }, journal: memoryJournal(), locale: 'zh-CN' })
const report = { source_sha: execFileSync('git', ['-C', source, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
  node: process.version, profile: 'local-dev', checks: [] }
const output = join(root, 'artifacts/agh-integration')
mkdirSync(output, { recursive: true })
async function operation(receipt) {
  const deadline = Date.now() + 90000
  while (Date.now() < deadline) {
    const result = await client.packages.operation.get({ profile: 'local-dev', operationId: receipt.operationId })
    if (['completed', 'failed', 'cancelled', 'rolled-back'].includes(result.state)) {
      if (result.state !== 'completed') throw new Error(JSON.stringify(result))
      return result
    }
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  throw new Error('Package operation deadline exceeded')
}
try {
  report.initialize = await client.initialize()
  report.checks.push('Official Node SDK connected through the identity-checked Windows named pipe')
  const clientId = await client.clientId()
  const command = () => ({ profile: 'local-dev', clientId, commandId: 'wg-' + randomUUID() })
  const packageSource = { type: 'file', ref: 'file:./packages/waterguard-agh' }
  const previewOperation = await operation(await client.packages.inspect({ ...command(), source: packageSource }))
  const preview = previewOperation.preview
  if (!preview || preview.blockers?.length) throw new Error('WaterGuard preview blocked: ' + JSON.stringify(preview))
  report.preview = preview
  const listed = await client.packages.list({ profile: 'local-dev' })
  const rows = Array.isArray(listed) ? listed : listed.packages
  const existing = rows.find(row => row.id === preview.id)
  if (!existing) {
    await operation(await client.packages.install({ ...command(), source: packageSource, expectedIntegrity: preview.integrity }))
  } else if (existing.integrity !== preview.integrity) {
    await operation(await client.packages.update({ ...command(), id: preview.id, source: packageSource, expectedIntegrity: preview.integrity }))
  }
  await operation(await client.packages.trust({ ...command(), id: preview.id, expectedIntegrity: preview.integrity, capabilityHash: preview.capabilityHash }))
  await operation(await client.packages.enable({ ...command(), id: preview.id, expectedInstalledIntegrity: preview.integrity }))
  report.packages = await client.packages.list({ profile: 'local-dev' })
  report.checks.push('Native WaterGuard Cordis plugin installed, trusted and enabled')
  const staged = join(root, '.cache/agh-bootstrap-key')
  const key = process.env.AGNES_API_KEY || (existsSync(staged) ? readFileSync(staged, 'utf8').trim() : undefined)
  if (key && !process.argv.includes('--tools-only')) {
    const tested = await client.config.test({ providerId: 'agnes-ai', apiKey: key })
    report.model_test = { verified: tested.verified, models: tested.models.map(m => m.id) }
    if (tested.verified) {
      const model = tested.models.find(m => m.id === 'agnes-2.5-flash')?.id || tested.models[0]?.id
      const before = await client.config.get()
      const saved = await client.config.save({ providerId: 'agnes-ai', apiKey: key, model, expectedRevision: before.revision })
      report.model_saved = { revision: saved.revision, model }
      report.checks.push('Agnes account validated and saved through official configuration service')
    }
  }
  // Credentials may already be stored by the official local backend.  Do not
  // report a false "pending" state merely because this invocation did not
  // receive a key environment variable.
  const configured = await client.config.get()
  const provider = configured?.provider
  report.saved_provider = provider ? { id: provider.id, model: provider.model,
    credentialConfigured: Boolean(provider.credentialConfigured) } : null
  report.status = report.model_test?.verified || provider?.credentialConfigured
    ? 'READY_FOR_MODEL_SMOKE' : 'PLUGIN_READY_MODEL_AUTH_PENDING'
} catch (error) {
  report.status = 'FAILED'
  report.error = error instanceof Error ? error.message : String(error)
  process.exitCode = 1
} finally {
  await client.close()
  writeFileSync(join(output, 'setup.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ status: report.status, checks: report.checks, error: report.error }))
}
