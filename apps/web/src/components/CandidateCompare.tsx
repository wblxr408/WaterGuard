import { ACTION_LABEL, PLAN_SOURCE_LABEL, STATUS_LABEL, clock, num, pct } from '../format'
import type { CandidateView, SourceId } from '../types'

interface Props {
  source: SourceId
  candidates: CandidateView[]
  active: number
  onActive: (i: number) => void
}

function best(values: (number | null | undefined)[], lowerIsBetter: boolean): number {
  let idx = -1
  values.forEach((v, i) => {
    if (v === null || v === undefined || !Number.isFinite(v)) return
    const cur = idx < 0 ? null : values[idx]
    if (cur === null || cur === undefined || (lowerIsBetter ? v < cur : v > cur)) idx = i
  })
  const uniqueBest = values.filter((v) => v === values[idx]).length === 1
  return uniqueBest ? idx : -1
}

export function CandidateCompare({ source, candidates, active, onActive }: Props) {
  if (candidates.length < 2) return null
  const metrics = candidates.map((c) => c.verification?.metrics)
  const bShort = best(metrics.map((m) => m?.shortage_m3), true)
  const bMin = best(metrics.map((m) => m?.min_pressure_m), false)
  const bViol = best(candidates.map((c) => c.verification?.violations.length), true)
  return (
    <section className="panel compare" aria-label={source === 'bench' ? '工况对照' : '方案比较'}>
      <div className="panel-head">
        <h2>{source === 'bench' ? '工况对照' : '方案比较'}</h2>
        <span className="note">
          {source === 'bench'
            ? '同一管网、同一引擎、同一时间窗：正常工况与故障工况的差别；绿色数字是这一项更好的一方'
            : '同一任务、同一故障、同一时间窗；绿色数字是这一项更好的方案'}
        </span>
      </div>
      <div className="compare-grid" style={{ gridTemplateColumns: `150px repeat(${candidates.length}, minmax(0, 1fr))` }}>
        <div className="cg-head" />
        {candidates.map((c, i) => (
          <button
            type="button"
            key={c.key}
            className={`cg-head cand${i === active ? ' is-active' : ''}`}
            onClick={() => onActive(i)}
            aria-pressed={i === active}
          >
            <strong>{c.label}</strong>
            {c.plan && <span className="chip">{PLAN_SOURCE_LABEL[c.plan.source] ?? c.plan.source}</span>}
            {i === active && <span className="viewing">正在查看</span>}
          </button>
        ))}

        <div className="cg-label">处置动作</div>
        {candidates.map((c) => (
          <div key={c.key} className="cg-cell">
            {c.plan && c.plan.actions.length ? (
              c.plan.actions.map((a, i) => (
                <div key={i} className="num">
                  {ACTION_LABEL[a.kind] ?? a.kind} {a.link_id} · {clock(a.simulation_time_s)}
                </div>
              ))
            ) : (
              <span className="muted">不处置</span>
            )}
          </div>
        ))}

        <div className="cg-label">方案说明</div>
        {candidates.map((c) => (
          <div key={c.key} className="cg-cell small">
            {c.plan?.reason ?? '—'}
          </div>
        ))}

        <div className="cg-label">缺供水量</div>
        {candidates.map((c, i) => (
          <div key={c.key} className={`cg-cell num${i === bShort ? ' better' : ''}`}>
            {num(metrics[i]?.shortage_m3, 1)} m³
          </div>
        ))}

        <div className="cg-label">最低压力</div>
        {candidates.map((c, i) => (
          <div key={c.key} className={`cg-cell num${i === bMin ? ' better' : ''}`}>
            {num(metrics[i]?.min_pressure_m, 2)} m
          </div>
        ))}

        <div className="cg-label">供需体积比</div>
        {candidates.map((c, i) => (
          <div key={c.key} className="cg-cell num">
            {pct(metrics[i]?.supply_ratio, 2)}
          </div>
        ))}

        <div className="cg-label">违规条数</div>
        {candidates.map((c, i) => (
          <div key={c.key} className={`cg-cell num${i === bViol ? ' better' : ''}`}>
            {c.verification?.violations.length ?? '—'}
          </div>
        ))}

        <div className="cg-label">名义硬约束</div>
        {candidates.map((c) => {
          const st = c.verification?.admission.nominal_constraints_pass ?? 'pending'
          return (
            <div key={c.key} className={`cg-cell status st-${st}`}>
              {STATUS_LABEL[st] ?? st}
            </div>
          )
        })}

        <div className="cg-label">可推荐</div>
        {candidates.map((c) => {
          const st = c.verification?.admission.recommendation_eligible ?? 'pending'
          return (
            <div key={c.key} className={`cg-cell status st-${st}`}>
              {STATUS_LABEL[st] ?? st}
            </div>
          )
        })}
      </div>
    </section>
  )
}
