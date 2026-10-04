import { num, pct, signed } from '../format'
import type { CandidateView, KpiKey } from '../types'

interface Props {
  active: CandidateView
  other: CandidateView | null
  threshold: number
  picked: KpiKey | null
  onPick: (k: KpiKey) => void
}

export function KpiStrip({ active, other, threshold, picked, onPick }: Props) {
  const m = active.verification?.metrics
  const om = other?.verification?.metrics
  const violations = active.verification?.violations.length ?? null
  const otherViolations = other?.verification?.violations.length ?? null
  const minp = m?.min_pressure_m ?? null
  const items: { key: KpiKey; label: string; value: string; unit: string; delta: string | null; better: boolean | null; tone: string }[] = [
    {
      key: 'shortage',
      label: '评估窗口缺供水量',
      value: num(m?.shortage_m3, 1),
      unit: 'm³',
      delta: m && om ? signed(m.shortage_m3 - om.shortage_m3, 1) : null,
      better: m && om ? m.shortage_m3 < om.shortage_m3 : null,
      tone: m ? (m.shortage_m3 > 0 ? 'warn' : 'ok') : 'na',
    },
    {
      key: 'minp',
      label: '受评节点最低压力',
      value: num(minp, 1),
      unit: 'm',
      delta: minp !== null && om?.min_pressure_m != null ? signed(minp - om.min_pressure_m, 1) : null,
      better: minp !== null && om?.min_pressure_m != null ? minp > om.min_pressure_m : null,
      tone: minp === null ? 'na' : minp >= threshold ? 'ok' : minp >= 0 ? 'warn' : 'bad',
    },
    {
      key: 'ratio',
      label: '供需体积比',
      value: pct(m?.supply_ratio, 2),
      unit: '',
      delta: m?.supply_ratio != null && om?.supply_ratio != null ? signed((m.supply_ratio - om.supply_ratio) * 100, 2) + ' 个百分点' : null,
      better: m?.supply_ratio != null && om?.supply_ratio != null ? m.supply_ratio > om.supply_ratio : null,
      tone: 'neutral',
    },
    {
      key: 'violations',
      label: '违规条数（节点 × 时段）',
      value: violations === null ? '—' : String(violations),
      unit: '条',
      delta: violations !== null && otherViolations !== null ? signed(violations - otherViolations, 0) : null,
      better: violations !== null && otherViolations !== null ? violations < otherViolations : null,
      tone: violations ? 'bad' : violations === 0 ? 'ok' : 'na',
    },
    {
      key: 'actions',
      label: '处置动作',
      value: active.plan ? String(active.plan.actions.length) : '—',
      unit: '个',
      delta: m ? `操作成本 ${num(m.action_cost, 0)}` : null,
      better: null,
      tone: 'neutral',
    },
  ]
  return (
    <div className="kpis" role="group" aria-label={`${active.label} 的关键指标`}>
      {items.map((it) => (
        <button
          type="button"
          key={it.key}
          className={`kpi tone-${it.tone}${picked === it.key ? ' is-picked' : ''}`}
          onClick={() => onPick(it.key)}
          title="点击查看这个数字的来源"
        >
          <span className="kpi-label">{it.label}</span>
          <span className="kpi-value num">
            {it.value}
            {it.unit && <small>{it.unit}</small>}
          </span>
          <span className={`kpi-delta${it.better === true ? ' better' : it.better === false ? ' worse' : ''}`}>
            {it.delta === null ? ' ' : it.key === 'actions' ? it.delta : `比${other?.label ?? '对照'} ${it.delta}`}
          </span>
        </button>
      ))}
    </div>
  )
}
