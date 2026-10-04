import { useMemo } from 'react'
import type { Action, Fault, LinkSample, NetworkScene, SeriesFrame, TankSample } from '@waterguard/contracts'
import { ACTION_LABEL, FAULT_LABEL, clock, lps, num, pct, signed } from '../format'
import { geometry, linkIndex, nodeIndex, pressureLevel, ratioLevel, supplyRatio, type Level } from '../model'
import type { Layer, Selection } from '../types'

interface Props {
  scene: NetworkScene
  frame: SeriesFrame | null
  baseFrame: SeriesFrame | null
  baseLabel: string | null
  diff: boolean
  layer: Layer
  threshold: number
  ratioThreshold: number
  assessed: Set<string>
  violating: Set<string>
  fault: Fault | null
  actions: Action[]
  time: number
  selection: Selection
  onSelect: (s: Selection) => void
}

type LinkState = 'open' | 'closed' | 'leaking' | 'ruptured' | 'isolated'

const STATE_LABEL: Record<LinkState, string> = {
  open: '开启',
  closed: '关闭',
  leaking: '泄漏中',
  ruptured: '断裂',
  isolated: '已隔离',
}

function linkState(sample: LinkSample | undefined, initial: 'open' | 'closed'): LinkState {
  if (!sample) return initial
  const ps = sample.physical_state
  if (ps === 'leaking' || ps === 'ruptured' || ps === 'closed' || ps === 'isolated') return ps
  if (sample.status === 'broken') return 'ruptured'
  return sample.status === 'closed' ? 'closed' : 'open'
}

const KIND_LABEL: Record<string, string> = { pipe: '管道', pump: '水泵', valve: '阀门' }

export function NetworkView(p: Props) {
  const geo = useMemo(() => geometry(p.scene), [p.scene])
  const linkById = useMemo(() => new Map(p.scene.links.map((l) => [l.id, l])), [p.scene])
  const nodes = useMemo(() => nodeIndex(p.frame), [p.frame])
  const baseNodes = useMemo(() => nodeIndex(p.baseFrame), [p.baseFrame])
  const links = useMemo(() => linkIndex(p.frame), [p.frame])
  const tanks = useMemo(() => {
    const m = new Map<string, TankSample>()
    p.frame?.tanks?.forEach((t) => m.set(t.node_id, t))
    return m
  }, [p.frame])
  const maxFlow = useMemo(() => {
    let m = 0
    links.forEach((l) => {
      m = Math.max(m, Math.abs(l.flow_m3s))
    })
    return m || 1
  }, [links])

  const u = geo.unit
  const font = u * 3.4
  const selNode = p.selection?.type === 'node' ? p.selection.id : null
  const selLink = p.selection?.type === 'link' ? p.selection.id : null

  const along = (linkId: string, frac = 0.5) => {
    const l = linkById.get(linkId)
    const a = l && geo.pos.get(l.from_node)
    const b = l && geo.pos.get(l.to_node)
    if (!a || !b) return null
    return { x: a.x + (b.x - a.x) * frac, y: a.y + (b.y - a.y) * frac }
  }

  const faultActive = p.fault ? p.time >= p.fault.start_s && p.time < p.fault.end_s : false
  const faultPos = p.fault ? along(p.fault.link_id, p.fault.location_fraction ?? 0.5) : null

  return (
    <svg
      className="network"
      viewBox={geo.viewBox}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`管网 ${p.scene.network_id}，仿真时刻 ${clock(p.time)}`}
    >
      <g className="links">
        {p.scene.links.map((l) => {
          const a = geo.pos.get(l.from_node)
          const b = geo.pos.get(l.to_node)
          if (!a || !b) return null
          const s = links.get(l.id)
          const st = linkState(s, l.initial_status)
          const isFault = p.fault?.link_id === l.id
          const width =
            p.layer === 'flow' && s ? u * (0.3 + (2.2 * Math.abs(s.flow_m3s)) / maxFlow) : u * (l.kind === 'pump' ? 0.75 : 0.5)
          const dash =
            st === 'closed'
              ? `${u * 1.6} ${u * 1.1}`
              : st === 'isolated'
                ? `${u * 0.5} ${u * 0.9}`
                : st === 'ruptured'
                  ? `${u * 2.4} ${u * 0.9}`
                  : undefined
          const cls = `link kind-${l.kind} st-${st}${isFault ? ' is-fault' : ''}${selLink === l.id ? ' is-selected' : ''}`
          const mx = (a.x + b.x) / 2
          const my = (a.y + b.y) / 2
          return (
            <g key={l.id} className={cls} onClick={() => p.onSelect({ type: 'link', id: l.id })}>
              <title>{`${KIND_LABEL[l.kind] ?? l.kind} ${l.id}\n状态：${STATE_LABEL[st]}\n流量：${s ? lps(s.flow_m3s) : '无数据'}`}</title>
              {(isFault || selLink === l.id) && (
                <line className="halo" x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={u * 2.6} />
              )}
              <line className="pipe" x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={width} strokeDasharray={dash} />
              {l.kind === 'pump' && <circle className="pump-mark" cx={mx} cy={my} r={u * 1.5} strokeWidth={u * 0.4} />}
              <line className="hit" x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={u * 3} />
            </g>
          )
        })}
      </g>

      <g className="actions">
        {p.actions.map((act, i) => {
          const at = along(act.link_id)
          if (!at) return null
          const done = p.time >= act.simulation_time_s
          return (
            <g key={`${act.link_id}-${i}`} className={`action-mark${done ? ' done' : ''}`}>
              <circle
                cx={at.x}
                cy={at.y}
                r={u * 3}
                strokeWidth={u * 0.5}
                strokeDasharray={done ? undefined : `${u * 1.2} ${u * 0.8}`}
              />
              <text x={at.x + u * 4} y={at.y - u * 2.2} fontSize={font}>
                {`${ACTION_LABEL[act.kind] ?? act.kind} ${act.link_id} · ${clock(act.simulation_time_s)}${done ? '' : '（待执行）'}`}
              </text>
            </g>
          )
        })}
      </g>

      <g className="nodes">
        {p.scene.nodes.map((n) => {
          const pos = geo.pos.get(n.id)
          if (!pos) return null
          const selected = selNode === n.id
          if (n.kind === 'reservoir' || n.kind === 'tank') {
            const t = tanks.get(n.id)
            const size = u * 4.2
            return (
              <g
                key={n.id}
                className={`facility ${n.kind}${selected ? ' is-selected' : ''}`}
                onClick={() => p.onSelect({ type: 'node', id: n.id })}
              >
                <title>
                  {n.kind === 'reservoir'
                    ? `水源 ${n.id}（水位恒定）`
                    : `水箱 ${n.id}${t ? `\n水位 ${num(t.level_m, 2)} m · 蓄水 ${num(t.volume_m3, 0)} m³\n净流入 ${lps(t.net_inflow_m3s)}` : ''}`}
                </title>
                {n.kind === 'reservoir' ? (
                  <rect
                    x={pos.x - size / 2}
                    y={pos.y - size / 2}
                    width={size}
                    height={size}
                    rx={u * 0.6}
                    strokeWidth={u * (selected ? 0.7 : 0.35)}
                  />
                ) : (
                  <rect
                    x={pos.x - size / 2}
                    y={pos.y - size / 2}
                    width={size}
                    height={size}
                    rx={u * 0.6}
                    strokeWidth={u * (selected ? 0.7 : 0.45)}
                    transform={`rotate(45 ${pos.x} ${pos.y})`}
                  />
                )}
                <text x={pos.x + size * 0.85} y={pos.y + font * 0.35} fontSize={font}>
                  {n.kind === 'reservoir' ? `水源 ${n.id}` : `水箱 ${n.id}`}
                </text>
              </g>
            )
          }

          const s = nodes.get(n.id)
          const assessed = p.assessed.has(n.id)
          let level: Level | 'up' | 'down' | 'flat'
          let r: number
          let tip: string
          if (p.diff) {
            const bs = baseNodes.get(n.id)
            const d = s && bs ? s.pressure_m - bs.pressure_m : null
            level = d === null ? 'na' : d >= 3 ? 'up' : d <= -3 ? 'down' : 'flat'
            r = d === null ? u * 0.8 : Math.abs(d) >= 10 ? u * 1.7 : Math.abs(d) >= 3 ? u * 1.25 : u * 0.8
            tip = `节点 ${n.id}\n压力变化 ${signed(d)} m（相对${p.baseLabel ?? '对照'}）\n当前 ${num(s?.pressure_m)} m · 对照 ${num(bs?.pressure_m)} m`
          } else if (p.layer === 'supply') {
            const ratio = supplyRatio(s)
            level = ratioLevel(ratio, p.ratioThreshold)
            r = level === 'ok' || level === 'na' ? u * 0.9 : level === 'low' ? u * 1.3 : u * 1.7
            tip = `节点 ${n.id}\n供需比 ${ratio === null ? '无需水' : pct(ratio)}\n压力 ${num(s?.pressure_m)} m`
          } else {
            level = pressureLevel(s?.pressure_m, p.threshold)
            r = level === 'ok' || level === 'na' ? u * 0.9 : level === 'low' ? u * 1.3 : u * 1.7
            tip = `节点 ${n.id}\n压力 ${num(s?.pressure_m)} m（阈值 ${num(p.threshold, 0)} m）\n供需比 ${pct(supplyRatio(s))}`
          }
          if (!assessed) tip += '\n不在受评节点内'
          const violating = p.violating.has(n.id)
          return (
            <g
              key={n.id}
              className={`node lv-${level}${assessed ? '' : ' not-assessed'}${selected ? ' is-selected' : ''}`}
              onClick={() => p.onSelect({ type: 'node', id: n.id })}
            >
              <title>{tip}</title>
              {violating && !p.diff && <circle className="viol-ring" cx={pos.x} cy={pos.y} r={r + u * 0.9} strokeWidth={u * 0.35} />}
              {selected && <circle className="sel-ring" cx={pos.x} cy={pos.y} r={r + u * 1.8} strokeWidth={u * 0.45} />}
              <circle className="dot" cx={pos.x} cy={pos.y} r={r} strokeWidth={u * 0.3} />
              <circle className="hit" cx={pos.x} cy={pos.y} r={u * 2.6} />
              {selected && (
                <text x={pos.x + r + u * 2.4} y={pos.y - r - u * 1.2} fontSize={font} className="node-label">
                  {n.id}
                </text>
              )}
            </g>
          )
        })}
      </g>

      {p.fault && faultPos && (
        <g className={`fault-mark${faultActive ? ' active' : ''}`}>
          <circle cx={faultPos.x} cy={faultPos.y} r={u * 2.2} strokeWidth={u * 0.5} />
          <path
            d={`M${faultPos.x - u * 1.2} ${faultPos.y - u * 1.2} L${faultPos.x + u * 1.2} ${faultPos.y + u * 1.2} M${faultPos.x + u * 1.2} ${faultPos.y - u * 1.2} L${faultPos.x - u * 1.2} ${faultPos.y + u * 1.2}`}
            strokeWidth={u * 0.5}
          />
          <text x={faultPos.x + u * 3.4} y={faultPos.y + font * 1.1} fontSize={font}>
            {`故障 · ${FAULT_LABEL[p.fault.kind] ?? p.fault.kind} ${p.fault.link_id}${faultActive ? '（进行中）' : p.time < p.fault.start_s ? '（尚未发生）' : '（已解除）'}`}
          </text>
        </g>
      )}

      {faultPos &&
        p.frame?.leaks?.map((lk, i) => (
          <g key={`${lk.node_id}-${i}`} className="leak-mark">
            <title>{`漏水点 ${lk.node_id}（来自管段 ${lk.source_link_id}）\n漏水 ${lps(lk.leak_flow_m3s)} · 压力 ${num(lk.pressure_m)} m`}</title>
            <circle cx={faultPos.x - u * 3 * (i + 1)} cy={faultPos.y + u * 3} r={u * 1.4} strokeWidth={u * 0.3} />
            <text x={faultPos.x - u * 3 * (i + 1)} y={faultPos.y + u * 3 + font * 1.6} fontSize={font * 0.9} textAnchor="middle">
              {lps(lk.leak_flow_m3s)}
            </text>
          </g>
        ))}
    </svg>
  )
}
