import { num, shortHash } from '../format'
import type { CandidateView, Workspace } from '../types'

export function EvidencePanel({ ws, active }: { ws: Workspace; active: CandidateView }) {
  const ev = active.evidence ?? ws.runEvidence
  const phys = active.physics
  return (
    <div className="evidence">
      {phys && (
        <div className={`phys ${phys.passed ? 'ok' : 'bad'}`}>
          <strong>物理检查：{phys.passed ? '通过' : '未通过'}</strong>
          {phys.mass_balance_max_abs_m3s !== undefined && (
            <span className="num">
              质量守恒最大误差 {num(phys.mass_balance_max_abs_m3s * 1e6, 3)} mL/s（允许 {num((phys.mass_balance_tolerance_m3s ?? 0) * 1e6, 1)} mL/s）
            </span>
          )}
        </div>
      )}
      {ev ? (
        <>
          <div className="kv">
            <span>证据包</span>
            <strong className="num">{ev.bundle_id}</strong>
          </div>
          <div className="kv">
            <span>文件</span>
            <strong className="num">{ev.artifacts.length} 个（含哈希）</strong>
          </div>
          <div className="kv">
            <span>AGH 原生记录</span>
            <strong>{ev.native_trace_ref ?? '无'}</strong>
          </div>
          <ul className="artifacts">
            {ev.artifacts.slice(0, 6).map((a) => (
              <li key={a.ref}>
                <span className="a-ref">{a.ref}</span>
                <span className="a-hash num">{shortHash(a.sha256)}</span>
              </li>
            ))}
          </ul>
          {ev.artifacts.length > 6 && <p className="note">另有 {ev.artifacts.length - 6} 个文件</p>}
          {ev.missing_items.length > 0 && <p className="source">缺失项：{ev.missing_items.join('、')}</p>}
        </>
      ) : (
        <p className="note">这个数据源没有证据清单。</p>
      )}
      <div className="endpoints">
        {active.endpoints.map((u) => (
          <a key={u} href={u} target="_blank" rel="noreferrer">
            {u.replace(/^\/(ab|bm|fx)/, '')}
          </a>
        ))}
      </div>
    </div>
  )
}
