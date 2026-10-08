// 首页「我们的系统」展示用的四张界面截图：应急工作台、方案对照、建模与仿真、证据中心。
// 用无头 Edge 打开正在运行的前端，依次点顶部导航切页，等默认中国管网数据读完后截图，存成 webp。
// 用法（apps/web-gpt 目录）：node tools/capture-system.mjs [http://127.0.0.1:5173]
// 输出：public/cover/system-{overview,compare,model,evidence}.webp（1600×1000 视口，1.25 倍像素）
import { spawn } from 'node:child_process'
import { writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const base = process.argv[2] ?? 'http://127.0.0.1:5173'
const out = new URL('../public/cover/', import.meta.url)
const PAGES = [
  ['overview', '应急工作台'],
  ['compare', '方案对照'],
  ['model', '建模与仿真'],
  ['evidence', '证据中心'],
]
const PORT = 9700 + Math.floor(Math.random() * 200)
const profile = join(tmpdir(), `capture-system-${PORT}`)
const edge = spawn('C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', ['--headless=new', '--hide-scrollbars', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
let wsUrl
for (let i = 0; i < 60 && !wsUrl; i++) {
  try {
    wsUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === 'page')?.webSocketDebuggerUrl
  } catch {}
  if (!wsUrl) await sleep(200)
}
const ws = new WebSocket(wsUrl)
await new Promise((r) => ws.addEventListener('open', r, { once: true }))
let seq = 0
const waiting = new Map()
ws.addEventListener('message', (e) => {
  const m = JSON.parse(e.data)
  if (m.id && waiting.has(m.id)) {
    waiting.get(m.id)(m)
    waiting.delete(m.id)
  }
})
const send = (method, params = {}) =>
  new Promise((resolve) => {
    const id = ++seq
    waiting.set(id, resolve)
    ws.send(JSON.stringify({ id, method, params }))
  })
const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result?.result?.value

await send('Page.enable')
await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1.25, mobile: false })
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
await send('Page.navigate', { url: base })
await sleep(3000)
for (const [id, label] of PAGES) {
  const ok = await evaluate(`(() => { const b = [...document.querySelectorAll('nav button')].find((x) => x.getAttribute('aria-label') === ${JSON.stringify(label)}); b?.click(); return !!b; })()`)
  if (!ok) throw new Error(`找不到导航「${label}」`)
  await sleep(3200)
  await evaluate('window.scrollTo(0, 0)')
  await sleep(300)
  const r = await send('Page.captureScreenshot', { format: 'webp', quality: 82 })
  writeFileSync(new URL(`system-${id}.webp`, out), Buffer.from(r.result.data, 'base64'))
  console.log('saved', `system-${id}.webp`)
}
ws.close()
edge.kill()
await sleep(500)
try {
  rmSync(profile, { recursive: true, force: true })
} catch {}
