import type { VerificationReport } from '@waterguard/contracts'
import { ADMISSION_STEPS, BLOCKER_LABEL, STATUS_LABEL } from '../format'

interface Props {
  v: VerificationReport | null
  label: string
}

export function AdmissionSteps({ v, label }: Props) {
  if (!v) return <p className="note">{label} 没有验收报告。</p>
  const cov = v.coverage
  return (
    <div className="admission">
      <ol className="steps" aria-label={`${label} 的推荐准入`}>
        {ADMISSION_STEPS.map(([key, name]) => {
          const st = v.admission[key]
          return (
            <li key={key} className={`step st-${st}`}>
              <span className="step-mark" aria-hidden="true">
                {st === 'pass' ? '✓' : st === 'fail' ? '✕' : '…'}
              </span>
              <span className="step-name">{name}</span>
              <span className="step-status">{STATUS_LABEL[st] ?? st}</span>
            </li>
          )
        })}
      </ol>
      {v.recommendation_blockers.length > 0 && (
        <ul className="blockers">
          {v.recommendation_blockers.map((b) => (
            <li key={b}>{BLOCKER_LABEL[b] ?? b}</li>
          ))}
        </ul>
      )}
      <p className="note">
        已完成复验 {cov.completed_stress_tests.length} / 要求 {cov.required_stress_tests.length}
        {cov.required_stress_tests.length > 0 ? `（${cov.required_stress_tests.join('、')}）` : ''}。「待定」不等于「失败」，也不能当作通过。
      </p>
    </div>
  )
}
