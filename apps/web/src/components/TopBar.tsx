import { SOURCES } from '../api'
import { MODE_HINT, MODE_LABEL } from '../format'
import type { SourceId, Workspace } from '../types'

interface Props {
  source: SourceId
  onSource: (s: SourceId) => void
  ws: Workspace | null
  loading: boolean
}

export function TopBar({ source, onSource, ws, loading }: Props) {
  const mode = ws?.mode ?? 'unknown'
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">
          Water<b>Guard</b>
        </span>
        <span className="brand-cn">水脉卫士 · 应急工作台</span>
      </div>
      <nav className="sources" aria-label="数据来源">
        {(Object.keys(SOURCES) as SourceId[]).map((id) => (
          <button
            type="button"
            key={id}
            className={id === source ? 'active' : ''}
            aria-pressed={id === source}
            onClick={() => onSource(id)}
          >
            {SOURCES[id].label}
          </button>
        ))}
      </nav>
      <div className="run-meta">
        <span className={`mode-badge mode-${mode}`} title={MODE_HINT[mode]}>
          {loading ? '加载中…' : MODE_LABEL[mode]}
        </span>
        {ws && (
          <span className="meta num">
            {ws.task.network_ref.network_id} · {ws.task.task_id} · 版本 {ws.task.revision}
          </span>
        )}
        <span className="agh-chip" title="当前数据不是 AGH 实时运行。接入后这里显示会话、模型和原生日志。">
          AGH 未接入 · 执行来源待核验
        </span>
      </div>
    </header>
  )
}
