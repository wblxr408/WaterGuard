import type { Admission, Mode } from '@waterguard/contracts'

const formatters = new Map<number, Intl.NumberFormat>()
function nf(digits: number) {
  let f = formatters.get(digits)
  if (!f) {
    f = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    formatters.set(digits, f)
  }
  return f
}

export function num(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || !Number.isFinite(v) ? '—' : nf(digits).format(v)
}

export function signed(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return (v > 0 ? '+' : '') + nf(digits).format(v)
}

export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || !Number.isFinite(v) ? '—' : `${nf(digits).format(v * 100)}%`
}

/** 仿真时钟：秒 → hh:mm（不是现实时间）。 */
export function clock(s: number): string {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

export function lps(m3s: number | null | undefined): string {
  return m3s === null || m3s === undefined ? '—' : `${num(m3s * 1000, 1)} L/s`
}

export function shortHash(h: string | null | undefined): string {
  if (!h) return '—'
  const v = h.replace(/^sha256:/, '')
  return `${v.slice(0, 10)}…`
}

export const MODE_LABEL: Record<Mode | 'unknown', string> = {
  live: 'Live 实时运行',
  replay: 'Replay 历史回放',
  fixture: 'Fixture 开发样例',
  benchmark: 'Benchmark 固定计算',
  unknown: '模式未知',
}

export const MODE_HINT: Record<Mode | 'unknown', string> = {
  live: '本次任务在 AGH 中实时运行，可核对会话与模型。',
  replay: '回放真实历史运行记录，不产生新调用。',
  fixture: '人工设定的开发样例，不是任何引擎或 Agent 的执行结果，不能作为实测证据。',
  benchmark: '固定脚本驱动的真实水力计算（WNTR/EPANET），不是 Agent 运行。',
  unknown: '响应头里没有运行模式，数据来源待核验。',
}

export const FAULT_LABEL: Record<string, string> = {
  closure: '管段关闭',
  leak: '泄漏',
  rupture: '断裂',
  isolation: '隔离',
}

export const ACTION_LABEL: Record<string, string> = {
  link_open: '打开管段',
  link_close: '关闭管段',
  pump_on: '启动水泵',
  pump_off: '停止水泵',
}

export const PLAN_SOURCE_LABEL: Record<string, string> = {
  fixture: '开发样例',
  agh: 'AGH / Agnes',
  rule: '固定规则',
  enumeration: '枚举搜索',
  graph: '图分析',
}

export const CASE_LABEL: Record<string, string> = {
  normal: '正常工况',
  closure: '管段关闭',
  leak: '泄漏',
  rupture: '断裂',
  isolation: '隔离',
  'pump-cycle': '水泵启停',
  'link-cycle': '管段启闭',
}

export const ADMISSION_STEPS: [keyof Admission, string][] = [
  ['action_valid', '动作合法'],
  ['simulation_valid', '仿真有效'],
  ['nominal_constraints_pass', '名义硬约束'],
  ['required_stress_tests_pass', '扰动复验'],
  ['recommendation_eligible', '可推荐'],
]

export const STATUS_LABEL: Record<string, string> = { pass: '通过', fail: '未通过', pending: '待定' }

export const BLOCKER_LABEL: Record<string, string> = {
  benchmark_only: '只是固定计算，不是 Agent 运行',
  fixture_only: '只是开发样例数据',
  native_agh_trace_missing: '缺少 AGH 原生运行记录',
  required_stress_tests_pending: '扰动复验还没完成',
}

export const EVENT_LABEL: Record<string, string> = {
  'run.started': '运行开始',
  'candidate.submitted': '提交候选方案',
  'simulation.completed': '仿真完成',
  'verification.completed': '验收完成',
  'run.completed': '运行结束',
}

export const ORIGIN_LABEL: Record<string, string> = {
  agh: 'AGH',
  gateway: '工具网关',
  worker: '计算进程',
  verifier: '验收程序',
  user: '用户',
}

export const VIOLATION_LABEL: Record<string, string> = {
  MIN_PRESSURE: '压力不足',
  MIN_SUPPLY_RATIO: '供水不足',
}

export const CONCLUSION_LABEL: Record<string, string> = {
  FEASIBLE_FOUND: '找到可行方案',
  NO_FEASIBLE_FOUND: '预算内未找到可行方案',
  INCOMPLETE: '未完成',
  INVALID: '无效',
}

export const STATUS_RUN_LABEL: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '运行中',
  NEEDS_INPUT: '等待补充信息',
  COMPLETED: '执行结束',
  BUDGET_EXHAUSTED: '预算用尽',
  ERROR: '出错',
  CANCELLED: '已取消',
}
