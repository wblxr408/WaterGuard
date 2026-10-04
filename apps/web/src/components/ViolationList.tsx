import { useState } from 'react'
import type { VerificationReport, Violation } from '@waterguard/contracts'
import { VIOLATION_LABEL, clock, num } from '../format'

interface Props {
  v: VerificationReport | null
  selectedNode: string | null
  onPick: (nodeId: string, t: number) => void
}

interface Group {
  node: string
  items: Violation[]
  worstPressure: number | null
  first: number
  last: number
}

export function ViolationList({ v, selectedNode, onPick }: Props) {
  const [all, setAll] = useState(false)
  if (!v) return null
  if (!v.violations.length) return <p className="note ok-text">没有违规：受评节点在评估窗口内全部满足硬约束。</p>
  const map = new Map<string, Group>()
  v.violations.forEach((x) => {
    const g = map.get(x.node_id) ?? { node: x.node_id, items: [], worstPressure: null, first: x.start_s, last: x.end_s }
    g.items.push(x)
    if (x.code === 'MIN_PRESSURE') g.worstPressure = g.worstPressure === null ? x.observed : Math.min(g.worstPressure, x.observed)
    g.first = Math.min(g.first, x.start_s)
    g.last = Math.max(g.last, x.end_s)
    map.set(x.node_id, g)
  })
  const groups = [...map.values()].sort((a, b) => (a.worstPressure ?? Infinity) - (b.worstPressure ?? Infinity))
  const shown = all ? groups : groups.slice(0, 6)
  return (
    <div className="violations">
      <p className="note">
        共 {v.violations.length} 条，涉及 {groups.length} 个节点；按最低压力从差到好排列，点击可在图上定位。
      </p>
      <ul>
        {shown.map((g) => {
          const codes = [...new Set(g.items.map((x) => VIOLATION_LABEL[x.code] ?? x.code))].join('、')
          const p = g.items.find((x) => x.code === 'MIN_PRESSURE')
          return (
            <li key={g.node}>
              <button
                type="button"
                className={selectedNode === g.node ? 'is-selected' : ''}
                onClick={() => onPick(g.node, g.first)}
              >
                <span className="v-node num">节点 {g.node}</span>
                <span className="v-what">
                  {codes} · {g.items.length} 条
                </span>
                <span className="v-detail num">
                  {g.worstPressure !== null ? `最低 ${num(g.worstPressure, 1)} m / 要求 ${num(p?.threshold ?? null, 0)} m` : ''}
                  {' · '}
                  {clock(g.first)}–{clock(g.last)}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      {groups.length > 6 && (
        <button type="button" className="link-btn" onClick={() => setAll(!all)}>
          {all ? '收起' : `展开全部 ${groups.length} 个节点`}
        </button>
      )}
    </div>
  )
}
