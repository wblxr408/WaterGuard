import type {
  LinkSample,
  NetworkScene,
  NodeSample,
  Series,
  SeriesFrame,
  VerificationReport,
  Violation,
} from '@waterguard/contracts'
import type { CandidateView, Workspace } from './types'

/** 节点状态：满足 / 偏低 / 严重（负压或接近断供）/ 无数据。 */
export type Level = 'ok' | 'low' | 'bad' | 'na'

export function frameTimes(series: Series | null): number[] {
  return series ? series.frames.map((f) => f.time_s) : []
}

export function frameAt(series: Series | null, idx: number): SeriesFrame | null {
  if (!series || !series.frames.length) return null
  return series.frames[Math.max(0, Math.min(idx, series.frames.length - 1))]
}

export function nodeIndex(frame: SeriesFrame | null): Map<string, NodeSample> {
  const m = new Map<string, NodeSample>()
  frame?.nodes.forEach((n) => m.set(n.node_id, n))
  return m
}

export function linkIndex(frame: SeriesFrame | null): Map<string, LinkSample> {
  const m = new Map<string, LinkSample>()
  frame?.links.forEach((l) => m.set(l.link_id, l))
  return m
}

/** 供需比：期望需求为 0 的节点没有供需比，返回 null（不当作 100%）。 */
export function supplyRatio(n: NodeSample | undefined): number | null {
  if (!n || !(n.expected_demand_m3s > 0)) return null
  return n.delivered_demand_m3s / n.expected_demand_m3s
}

export function pressureLevel(p: number | undefined, threshold: number): Level {
  if (p === undefined || !Number.isFinite(p)) return 'na'
  if (p >= threshold) return 'ok'
  if (p >= 0) return 'low'
  return 'bad'
}

export function ratioLevel(r: number | null, threshold: number): Level {
  if (r === null) return 'na'
  if (r >= threshold) return 'ok'
  if (r >= 0.5) return 'low'
  return 'bad'
}

export function violationsByNode(v: VerificationReport | null): Map<string, Violation[]> {
  const m = new Map<string, Violation[]>()
  v?.violations.forEach((x) => {
    const list = m.get(x.node_id) ?? []
    list.push(x)
    m.set(x.node_id, list)
  })
  return m
}

/** 当前时刻落在违规区间 [start,end) 里的节点。 */
export function violatingAt(v: VerificationReport | null, t: number): Set<string> {
  const s = new Set<string>()
  v?.violations.forEach((x) => {
    if (t >= x.start_s && t < x.end_s) s.add(x.node_id)
  })
  return s
}

/** 默认选中：违规最多、观测值最差的节点，让曲线一打开就有内容。 */
export function worstNode(v: VerificationReport | null): string | null {
  if (!v || !v.violations.length) return null
  const score = new Map<string, number>()
  v.violations.forEach((x) => {
    if (x.code !== 'MIN_PRESSURE') return
    const gap = x.threshold - x.observed
    score.set(x.node_id, Math.max(score.get(x.node_id) ?? -Infinity, gap))
  })
  let best: string | null = null
  let gapMax = -Infinity
  score.forEach((gap, id) => {
    if (gap > gapMax) {
      gapMax = gap
      best = id
    }
  })
  return best ?? v.violations[0].node_id
}

export function defaultFrame(ws: Workspace): number {
  const times = frameTimes(ws.candidates[ws.candidates.length - 1]?.series ?? null)
  const target = ws.task.fault ? ws.task.fault.start_s : ws.task.time_window.evaluation_start_s
  const idx = times.findIndex((t) => t >= target)
  return idx < 0 ? 0 : idx
}

export interface Geometry {
  pos: Map<string, { x: number; y: number }>
  viewBox: string
  unit: number
}

/** 场景坐标（y 向上）→ SVG（y 向下），并按网络尺寸给出符号的基准单位。 */
export function geometry(scene: NetworkScene): Geometry {
  const xs = scene.nodes.map((n) => n.x_m)
  const ys = scene.nodes.map((n) => n.y_m)
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const span = Math.max(maxX - minX, maxY - minY) || 1
  const pad = span * 0.07
  const pos = new Map<string, { x: number; y: number }>()
  scene.nodes.forEach((n) => pos.set(n.id, { x: n.x_m, y: -n.y_m }))
  return {
    pos,
    viewBox: `${minX - pad} ${-maxY - pad} ${maxX - minX + 2 * pad} ${maxY - minY + 2 * pad}`,
    unit: span / 180,
  }
}

export function otherCandidate(ws: Workspace, active: number): CandidateView | null {
  if (ws.candidates.length < 2) return null
  return ws.candidates[active === 0 ? 1 : 0]
}

/** 节点在评估窗口内的最低压力，用于检视面板。 */
export function minPressureInWindow(series: Series | null, nodeId: string, from: number, to: number): number | null {
  if (!series) return null
  let min: number | null = null
  for (const f of series.frames) {
    if (f.time_s < from || f.time_s >= to) continue
    const n = f.nodes.find((x) => x.node_id === nodeId)
    if (n && (min === null || n.pressure_m < min)) min = n.pressure_m
  }
  return min
}
