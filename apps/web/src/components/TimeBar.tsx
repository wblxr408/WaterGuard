import type { Action, Fault } from '@waterguard/contracts'
import { ACTION_LABEL, clock } from '../format'

interface Props {
  times: number[]
  idx: number
  onIdx: (i: number) => void
  end: number
  warmupEnd: number
  fault: Fault | null
  actions: Action[]
  violationTimes: number[]
  playing: boolean
  onPlay: (v: boolean) => void
}

export function TimeBar(p: Props) {
  if (!p.times.length) return <div className="timebar empty">没有时序数据</div>
  const first = p.times[0]
  const last = p.times[p.times.length - 1]
  const span = last - first || 1
  const pos = (t: number) => `${Math.max(0, Math.min(1, (t - first) / span)) * 100}%`
  const width = (a: number, b: number) => `${(Math.max(0, Math.min(last, b) - Math.max(first, a)) / span) * 100}%`
  const t = p.times[p.idx] ?? first

  return (
    <div className="timebar">
      <button type="button" className="play" onClick={() => p.onPlay(!p.playing)} aria-pressed={p.playing}>
        {p.playing ? '暂停' : '播放'}
      </button>
      <div className="track-wrap">
        <div className="track" aria-hidden="true">
          {p.warmupEnd > first && (
            <span className="band warmup" style={{ left: pos(first), width: width(first, p.warmupEnd) }} />
          )}
          {p.fault && (
            <span className="band fault" style={{ left: pos(p.fault.start_s), width: width(p.fault.start_s, p.fault.end_s) }} />
          )}
          {p.violationTimes.map((vt) => (
            <span key={`v${vt}`} className="tick viol" style={{ left: pos(vt) }} />
          ))}
          {p.actions.map((a, i) => (
            <span
              key={`a${i}`}
              className="tick action"
              style={{ left: pos(a.simulation_time_s) }}
              title={`${ACTION_LABEL[a.kind] ?? a.kind} ${a.link_id} · ${clock(a.simulation_time_s)}`}
            />
          ))}
          <span className="cursor" style={{ left: pos(t) }} />
        </div>
        <input
          type="range"
          min={0}
          max={p.times.length - 1}
          step={1}
          value={p.idx}
          onChange={(e) => p.onIdx(Number(e.target.value))}
          aria-label="仿真时刻"
          aria-valuetext={clock(t)}
        />
        <div className="track-legend">
          <span>
            <i className="sw warmup" />
            预运行（不计入评估）
          </span>
          {p.fault && (
            <span>
              <i className="sw fault" />
              故障持续 {clock(p.fault.start_s)}–{clock(p.fault.end_s)}
            </span>
          )}
          {p.actions.length > 0 && (
            <span>
              <i className="sw action" />
              处置动作
            </span>
          )}
          {p.violationTimes.length > 0 && (
            <span>
              <i className="sw viol" />
              有违规的时段
            </span>
          )}
          <span className="muted">← → 逐帧 · 空格播放</span>
        </div>
      </div>
      <div className="clock">
        <span className="num">{clock(t)}</span>
        <small>仿真时刻 / 共 {clock(p.end)}</small>
      </div>
    </div>
  )
}
