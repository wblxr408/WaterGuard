// Public AGH SDK boundary: JSON stdin, sanitized JSON stdout. No model proxy loop.
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { resolve, join } from 'node:path'
import { mkdirSync, appendFileSync, writeFileSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { resolveModelConfig } from './agh_model_config.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const source = join(root, 'vendor/agnes-harness')
const require = createRequire(join(source, 'packages/cli/package.json'))
const { createClient } = await import(pathToFileURL(require.resolve('@agnes/sdk')).href)
const inputStream = createInterface({ input: process.stdin, crlfDelay: Infinity })
const inputLines = inputStream[Symbol.asyncIterator]()
const input = JSON.parse((await inputLines.next()).value)
const send = (value) => process.stdout.write(JSON.stringify(value) + '\n')
const choices = [
  { id: 'agnes-ai', label: '赛事 Agnes', base_urls: ['https://api.agnes-ai.cn/v1'] },
  { id: 'deepseek', label: 'DeepSeek', base_urls: ['https://api.deepseek.com'] },
  { id: 'zai', label: 'GLM', base_urls: ['https://open.bigmodel.cn/api/paas/v4', 'https://open.bigmodel.cn/api/coding/paas/v4', 'https://api.z.ai/api/coding/paas/v4', 'https://api.z.ai/api/paas/v4'] },
]
process.env.AGH_HOME = join(root, '.cache/agh-waterguard')
process.env.AGNES_PROFILE = 'local-dev'
let client
try {
  let statusText
  try {
    statusText = execFileSync(process.execPath, [join(source, 'packages/cli/dist/local/agnes.mjs'),
      'daemon', 'status', '--profile', 'local-dev', '--data-dir', join(process.env.AGH_HOME, 'data')], { encoding: 'utf8', timeout: 15000 })
  } catch (error) { statusText = error.stdout || '{}' }
  const status = JSON.parse(statusText)
  if (!status.running || !status.socketReachable) throw new Error('AGH_DAEMON_UNAVAILABLE')
  client = createClient({ transport: { kind: 'unix', path: status.owner.socketPath,
    serverIdentity: { pid: status.owner.pid, processStartId: status.owner.processStartId } }, locale: 'zh-CN' })
  await client.initialize()
  if (input.action === 'status') {
    const snapshot = await client.config.get()
    const providers = await client.config.providers()
    send({ kind: 'status', available: true, snapshot,
      providers: choices.filter(row => providers.providers.some(p => p.id === row.id)) })
  } else if (['test', 'save'].includes(input.action)) {
    const config = resolveModelConfig(input.config, await client.config.get())
    const choice = choices.find(row => row.id === config.providerId)
    if (!choice || (config.baseUrl && !choice.base_urls.includes(config.baseUrl.replace(/\/$/, '')))) throw new Error('MODEL_ENDPOINT_NOT_ALLOWED')
    const tested = await client.config.test(config)
    if (input.action === 'test') send({ kind: 'test', ...tested })
    else {
      if (!tested.verified) throw new Error('MODEL_AUTH_FAILED: AGH did not validate this account')
      if (!config.model || !tested.models.some(row => row.id === config.model)) throw new Error('MODEL_NOT_IN_ACCOUNT_CATALOG')
      const before = await client.config.get()
      const saved = await client.config.save({ ...config, accountId: config.accountId || config.providerId,
        label: choice.label, makeDefault: input.makeDefault !== false, expectedRevision: before.revision })
      send({ kind: 'saved', snapshot: saved })
    }
  } else if (input.action === 'run') {
    if (!/^run-[A-Za-z0-9_-]+$/.test(input.run_id)) throw new Error('Invalid run identity')
    const directory = join(process.env.WG_ARTIFACT_ROOT || join(root, 'artifacts'), input.run_id, 'agh', 'native')
    mkdirSync(directory, { recursive: true })
    const session = await client.session.new({ cwd: root, sessionKey: `waterguard:${input.run_id}` })
    session.onPermissionRequest(async () => ({ verdict: 'rejected' }))
    const model = input.model
    await session.setModel({ slot: 'primary', route: model.route, model: model.model })
    send({ kind: 'session', session_id: session.id, model })
    // The API must freeze the native session binding before any model tool executes.
    const acknowledgement = JSON.parse((await inputLines.next()).value || '{}')
    if (acknowledgement.bound_session_id !== session.id) throw new Error('AGH_SESSION_NOT_BOUND')
    const iterator = session.events()[Symbol.asyncIterator]()
    const events = []
    const consuming = (async () => {
      for (;;) {
        const { value, done } = await iterator.next()
        if (done) break
        events.push(value)
        appendFileSync(join(directory, 'events.jsonl'), JSON.stringify(value) + '\n')
        send({ kind: 'event', event: value })
      }
    })()
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), input.max_wall_time_s * 1000)
    process.on('SIGTERM', () => controller.abort())
    let result
    const jevInstruction = `管网 ID 为 ${input.network_id}。读取任务与网络并生成候选后，必须调用 wg_jev_decide。若 configured=true，结合 Jev 的候选路由、置信度与风险值选择候选，低置信度时通过真实计算比较。若 configured=false，继续 Agnes 规划。每次独立验证后再次调用 wg_jev_decide 决定继续还是完成；不要将建议当作验收。`
    try {
      result = await session.prompt(`你是 WaterGuard 方案规划助手。用户已冻结任务 ${input.task_id}。只使用 wg_ 业务工具。先读取任务和网络，生成候选。${jevInstruction}选择和提交方案，仿真后独立验证。根据实际反馈修正，仿真总次数最多 ${input.max_simulations} 次。不得修改冻结约束或自报验证通过。完成后读取证据并引用实际候选 ID 和结果说明结论。`, { signal: controller.signal, titleLocale: 'zh-CN' })
    } finally {
      clearTimeout(timer)
      await client.close()
      await consuming
    }
    const calls = new Map(events.filter(e => e.type === 'tool/call').map(e => [e.data.toolUseId, e.data]))
    const children = events.filter(e => e.type === 'tool/result' && !e.data.isError && calls.get(e.data.toolUseId)?.name === 'wg_submit_plan')
      .map(e => e.data.structured?.run_id).filter(id => /^run-[A-Za-z0-9_-]+$/.test(id || ''))
    writeFileSync(join(directory, 'outcome.json'), JSON.stringify({ session_id: session.id, result, child_runs: children }, null, 2))
    send({ kind: 'completed', result, child_runs: [...new Set(children)] })
  } else if (input.action === 'cancel') {
    const session = await client.session.load(input.session_id, { cwd: root })
    await session.cancel()
    send({ kind: 'cancel_requested', session_id: session.id })
  } else throw new Error('Unsupported bridge action')
} catch (error) {
  // Config errors are intentionally fixed text: provider errors may contain request input.
  send({ kind: 'error', code: 'AGH_ERROR', message: ['test', 'save'].includes(input.action)
    ? 'AGH_MODEL_CONFIG_FAILED: 检查账号、接口、模型和凭据。' : String(error.message || error).replace(/sk-[A-Za-z0-9_-]+/g, '[redacted]') })
  process.exitCode = 1
} finally {
  inputStream.close()
  await client?.close()
}
