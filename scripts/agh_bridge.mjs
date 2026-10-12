// Public AGH SDK boundary: JSON stdin, sanitized JSON stdout. No model proxy loop.
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { resolve, join } from 'node:path'
import { mkdirSync, appendFileSync, writeFileSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { resolveModelConfig } from './agh_model_config.mjs'
import { missingClosureTools, unfinishedWorkflow } from '../packages/waterguard-agh/feedback.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const source = join(root, 'vendor/agnes-harness')
const require = createRequire(join(source, 'packages/cli/package.json'))
const { createClient, memoryJournal } = await import(pathToFileURL(require.resolve('@agnes/sdk')).href)
const inputStream = createInterface({ input: process.stdin, crlfDelay: Infinity })
const inputLines = inputStream[Symbol.asyncIterator]()
const input = JSON.parse((await inputLines.next()).value)
const send = (value) => process.stdout.write(JSON.stringify(value) + '\n')
const choices = [
  { id: 'agnes-ai', label: '赛事 Agnes', base_urls: ['https://api.agnes-ai.cn/v1'] },
  { id: 'deepseek', label: 'DeepSeek', base_urls: ['https://api.deepseek.com'] },
  { id: 'zai', label: 'GLM', base_urls: ['https://open.bigmodel.cn/api/paas/v4', 'https://open.bigmodel.cn/api/coding/paas/v4', 'https://api.z.ai/api/coding/paas/v4', 'https://api.z.ai/api/paas/v4'] },
]
// Keep credentials outside the repository when a private AGH home is supplied.
// The project-local cache remains the default for clean, self-contained setup.
const aghHome = resolve(process.env.WG_AGH_HOME || join(root, '.cache/agh-waterguard'))
process.env.AGH_HOME = aghHome
process.env.AGNES_PROFILE = 'local-dev'
let client
try {
  let statusText
  try {
    statusText = execFileSync(process.execPath, [join(source, 'packages/cli/dist/local/agnes.mjs'),
      'daemon', 'status', '--profile', 'local-dev'], { encoding: 'utf8', timeout: 15000 })
  } catch (error) { statusText = error.stdout || '{}' }
  const status = JSON.parse(statusText)
  if (!status.running || !status.socketReachable) throw new Error('AGH_DAEMON_UNAVAILABLE')
  client = createClient({ transport: { kind: 'unix', path: status.owner.socketPath,
    serverIdentity: { pid: status.owner.pid, processStartId: status.owner.processStartId } },
  auth: { kind: 'local' }, journal: memoryJournal(), locale: 'zh-CN' })
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
    let budgetExhausted = false
    const timer = setTimeout(() => { budgetExhausted = true; controller.abort() }, input.max_wall_time_s * 1000)
    process.on('SIGTERM', () => controller.abort())
    let result
    const closureRepairs = []
    const jevInstruction = `管网 ID 为 ${input.network_id}。先提交并仿真无动作基线 web-candidate-0，再调用独立验证。基线失败时必须读取具体违规节点、压力和缺供，选择另一候选并用真实计算检验修正，不能只写修正文字。至少预留一次仿真给成功候选的必需压力复验。读取任务与网络并生成候选后，调用 wg_jev_decide。若 configured=true，结合 Jev 的候选路由、置信度与风险值选择候选，低置信度时通过真实计算比较。若 configured=false，继续 Agnes 规划。每次独立验证后再次调用 wg_jev_decide 决定继续还是完成；不要将建议当作验收。最终 wg_build_evidence 必须使用本次父运行 ${input.run_id}。`
    try {
      const continuation = input.continuation && typeof input.continuation === 'object'
        ? `这是一个新的关联后继会话；旧会话不会被伪造续接。已保存的连续性信息：${JSON.stringify(input.continuation)}。先通过 wg_get_task 重新读取冻结任务，再基于该信息继续。\n\n`
        : ''
      const repair = input.acceptance_scenario === 'repair_demo'
        ? `这是受控验收场景。完成 wg_get_task 和 wg_inspect_network 后，必须先调用一次 wg_submit_plan，candidate_id 固定为 repair-demo-invalid；该调用必然被服务端拒绝。必须读取返回的结构化 INVALID_ACTION/错误反馈，然后调用 wg_generate_candidates，并只提交其中返回的合法 candidate_id。不能跳过失败步骤，也不能把失败说成通过。\n\n`
        : ''
      result = await session.prompt(`你是 WaterGuard 方案规划助手。用户已冻结任务 ${input.task_id}。只使用 wg_ 业务工具，不使用 shell、代码编辑、其他文件或网络工具。每次得到反馈后尽快调用下一项必要工具，避免长篇文字分析。存在未试候选且有预算时继续真实检验；最终用简短中文给出结论。

${continuation}${repair}严格按顺序执行：第一步只能调用 wg_get_task，参数 task_id 必须为 ${input.task_id}，并等待其结果；第二步从结果的 network_ref.network_id 读取真实网络 ID，再调用 wg_inspect_network。之后生成有限候选，并调用 wg_jev_decide；${jevInstruction}先只选择一个候选；调用 wg_submit_plan 时必须直接传入 wg_generate_candidates 或 wg_revise_plan 返回的 candidate_id，绝不能重写或传递完整 plan_json。完成名义仿真和独立验证后再次调用 wg_jev_decide；如果名义验证通过且任务声明了必需压力测试，必须对同一已完成候选调用 wg_create_perturbation（需求倍率从冻结任务 perturbation_domain 的 required 标签和 demand_multipliers 读取，按任务实际增幅复验，禁止使用固定倍率），再对返回的新 run 调用 wg_simulate_candidate 和 wg_verify_candidate。如果名义失败且还有仿真与修订预算，可以调用 wg_diagnose_failure 读取真实失效节点、时段、允许设施和时刻，再用 wg_revise_plan 提出具体动作组合或时序修订；reason 必须引用实际失败，不能承诺会通过。优先计算无动作基线和一个工程动作；该动作仍失败且还剩至少两次仿真时，必须先调用 wg_diagnose_failure 和 wg_revise_plan，实际提交返回的修订 ID、仿真并独立验证，不能再跳过修订继续选静态候选。wg_verify_candidate 返回的 workflow.required_next 是服务端真实流程要求；遇到 diagnose_and_revise 或 simulate_revision 必须完成该步骤。若某次修订被拒绝，依据结构化拒绝原因纠正权限或时刻，再申请修订；被拒绝不消耗仿真预算。只能在冻结许可内组合或调整动作时刻，不能操作故障或隔离边界，不能修改阈值、物理参数或任务。修订后仍须真实仿真与独立验证，名义通过后执行同方案压力复验。按冻结 objectives 优先改善最差节点正常供水时间占比、再提高逐节点平均占比；完整逐节点时长来自独立验证报告，不能用全网供需比代替。根据全部实际反馈选择后续候选或修正方案，仿真总次数最多 ${input.max_simulations} 次。不得修改冻结约束或自报验证通过。完成后调用 wg_compare_candidates 与 wg_build_evidence，并用中文引用实际候选 ID 和结果说明结论、未完成项及适用范围。没有达标候选时如实说明。`, { signal: controller.signal, titleLocale: 'zh-CN' })
      // Repair actual omissions through the same native model session. The host
      // never calls business tools or fabricates their completion on its behalf.
      for (let attempt = 0; attempt < input.max_simulations && !controller.signal.aborted; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 0))
        if (events.some(e => e.type === 'tool/call' && e.data.name === 'wg_request_clarification')) break
        const unfinished = unfinishedWorkflow(events, input.run_id)
        if (unfinished) {
          closureRepairs.push({ kind: 'unfinished_hydraulic_review', workflow: unfinished })
          result = await session.prompt(`原生工具返回的服务端流程尚未完成：${JSON.stringify(unfinished)}。你仍在同一个真实会话中。根据这些实际反馈执行下一步；存在未试候选且有预算时继续选择并真实仿真，名义通过后按任务参数完成同方案复验，不能提前用文字结论结束。不要重试已测试的相同方案。只使用实际返回的许可候选或修订工具，由你选择动作，宿主不会代替你提交。每一步尽快调用工具，避免长篇文字分析；结束时给出简短中文结论。`, { signal: controller.signal, titleLocale: 'zh-CN' })
          continue
        }
        const missing = missingClosureTools(events)
        if (!missing.length) break
        closureRepairs.push(missing)
        const calls = new Map(events.filter(e => e.type === 'tool/call').map(e => [e.data.toolUseId, e.data.name]))
        const childIds = events.filter(e => e.type === 'tool/result' && !e.data.isError && calls.get(e.data.toolUseId) === 'wg_submit_plan')
          .map(e => e.data.structured?.run_id).filter(id => /^run-[A-Za-z0-9_-]+$/.test(id || ''))
        result = await session.prompt(`主规划已结束，但原生记录尚缺少成功收尾工具：${missing.join('、')}。现在完成这些实际调用；不要开始新的仿真。已提交子运行 ID 为 ${JSON.stringify([...new Set(childIds)])}。wg_compare_candidates 的 run_ids_json 必须使用这些真实 ID 的 JSON 字符串数组；wg_build_evidence 的 run_id 必须为父运行 ${input.run_id}。证据读取会明确返回执行中，最终归档由宿主在会话结束后生成。请依据真实结果给出结论。`, { signal: controller.signal, titleLocale: 'zh-CN' })
      }
    } catch (error) {
      if (!budgetExhausted) throw error
      result = { status: 'budget_exhausted', message: 'Frozen wall-time budget reached; partial native trace retained.' }
    } finally {
      clearTimeout(timer)
      await client.close()
      await consuming
    }
    const calls = new Map(events.filter(e => e.type === 'tool/call').map(e => [e.data.toolUseId, e.data]))
    const children = events.filter(e => e.type === 'tool/result' && !e.data.isError && calls.get(e.data.toolUseId)?.name === 'wg_submit_plan')
      .map(e => e.data.structured?.run_id).filter(id => /^run-[A-Za-z0-9_-]+$/.test(id || ''))
    writeFileSync(join(directory, 'outcome.json'), JSON.stringify({ session_id: session.id, result, child_runs: children, closure_repairs: closureRepairs }, null, 2))
    send({ kind: 'completed', result, budget_exhausted: budgetExhausted, child_runs: [...new Set(children)] })
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
