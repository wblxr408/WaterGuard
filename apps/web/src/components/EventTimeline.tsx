import { useEffect, useState } from 'react'
import type { RunEvent } from '@waterguard/contracts'
import { CONCLUSION_LABEL, EVENT_LABEL, ORIGIN_LABEL, PLAN_SOURCE_LABEL, STATUS_RUN_LABEL } from '../format'
import type { Workspace } from '../types'

const TYPES = Object.keys(EVENT_LABEL)

function useFixtureEvents(ws: Workspace): { events: RunEvent[]; state: string } {
  const [events, setEvents] = useState<RunEvent[]>([])
  const [state, setState] = useState('等待事件')
  useEffect(() => {
    if (ws.source !== 'fixture' || !ws.run) return
    setEvents([])
    setState('连接中')
    const es = new EventSource(`${ws.apiBase}/api/runs/${ws.run.run_id}/events`)
    const onEvent = (msg: MessageEvent) => {
      const ev = JSON.parse(msg.data) as RunEvent
      setEvents((prev) =>
        prev.some((x) => x.sequence === ev.sequence) ? prev : [...prev, ev].sort((a, b) => a.sequence - b.sequence),
      )
      setState('已接收')
    }
    TYPES.forEach((t) => es.addEventListener(t, onEvent as EventListener))
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) setState('事件流已结束')
    }
    return () => es.close()
  }, [ws])
  return { events, state }
}

export function EventTimeline({ ws }: { ws: Workspace }) {
  const { events, state } = useFixtureEvents(ws)

  if (ws.source !== 'fixture') {
    return (
      <div className="events">
        <div className="empty-block">
          <strong>没有 AGH 运行记录</strong>
          <p>
            这组数据是固定脚本驱动的真实水力计算（Benchmark），不是 Agnes 的决策过程。同学 2 接通 AGH 后，这里按时间显示：哪个角色、调用了什么工具、结果如何、为什么修改方案。
          </p>
        </div>
        {ws.index?.conclusion && (
          <p className="source">
            <span className="chip">数据自述 · 结论</span> {ws.index.conclusion}
          </p>
        )}
        {ws.index?.attribution && (
          <p className="source">
            <span className="chip">数据自述 · 来源</span> {ws.index.attribution}
          </p>
        )}
        {ws.candidates
          .filter((c) => c.plan)
          .map((c) => (
            <p className="source" key={c.key}>
              <span className="chip">{c.label} · {PLAN_SOURCE_LABEL[c.plan!.source] ?? c.plan!.source}</span> {c.plan!.reason}
            </p>
          ))}
      </div>
    )
  }

  const run = ws.run
  return (
    <div className="events">
      {run && (
        <div className="run-line">
          <span className="chip">{STATUS_RUN_LABEL[run.status] ?? run.status}</span>
          <span className="chip">结论：{CONCLUSION_LABEL[run.conclusion] ?? run.conclusion}</span>
          <span className="muted">
            仿真 {run.budget_usage.simulations} 次 · 模型调用 {run.budget_usage.model_calls ?? '未计量'}
          </span>
        </div>
      )}
      <p className="note">事件流：{state}（Fixture 样例事件，没有 AGH 原生会话）</p>
      <ol className="timeline">
        {events.map((e) => (
          <li key={e.sequence} className={`ev origin-${e.origin}`}>
            <span className="ev-seq num">#{e.sequence}</span>
            <div className="ev-body">
              <div className="ev-head">
                <strong>{EVENT_LABEL[e.type] ?? e.type}</strong>
                <span className="chip">{ORIGIN_LABEL[e.origin] ?? e.origin}</span>
                {e.candidate_id && <span className="muted">{e.candidate_id}</span>}
              </div>
              <div className="ev-meta">
                <a href={`${ws.apiBase}/api/artifacts/${e.payload_ref}`} target="_blank" rel="noreferrer">
                  {e.payload_ref}
                </a>
                {!e.agh_session_ref && <span className="muted"> · 无 AGH 记录</span>}
              </div>
            </div>
          </li>
        ))}
      </ol>
      {run && run.missing_items.length > 0 && (
        <p className="source">缺失项：{run.missing_items.join('、')}</p>
      )}
    </div>
  )
}
