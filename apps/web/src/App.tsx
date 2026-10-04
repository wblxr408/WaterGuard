import { useCallback, useEffect, useMemo, useState } from 'react'
import { SOURCES, load } from './api'
import { MODE_HINT, clock, num } from './format'
import { defaultFrame, frameAt, frameTimes, otherCandidate, violatingAt, worstNode } from './model'
import type { BenchSelection, Layer, Selection, SourceId, Workspace } from './types'
import { AdmissionSteps } from './components/AdmissionSteps'
import { CandidateCompare } from './components/CandidateCompare'
import { EventTimeline } from './components/EventTimeline'
import { EvidencePanel } from './components/EvidencePanel'
import { Inspector } from './components/Inspector'
import { KpiStrip } from './components/KpiStrip'
import { NetworkView } from './components/NetworkView'
import { NodeChart } from './components/NodeChart'
import { TaskPanel } from './components/TaskPanel'
import { TimeBar } from './components/TimeBar'
import { TopBar } from './components/TopBar'
import { ViolationList } from './components/ViolationList'

const DEFAULT_BENCH: BenchSelection = { network: 'epa-net3', engine: 'wntr', caseName: 'rupture' }

const START_HINT: Record<SourceId, string> = {
  pair: '.venv\\Scripts\\python.exe -m services.hydraulics.server --output artifacts/feedback-pair --port 8002',
  bench: '.venv\\Scripts\\python.exe -m services.hydraulics.server --output artifacts/student1 --port 8001',
  fixture: '.venv\\Scripts\\python.exe packages/contracts/scripts/mock_server.py',
}

function Legend({ layer, diff, threshold, ratio, otherLabel }: { layer: Layer; diff: boolean; threshold: number; ratio: number; otherLabel: string | null }) {
  if (diff) {
    return (
      <div className="legend">
        <span><i className="lv-up" />压力升高 ≥ 3 m</span>
        <span><i className="lv-flat" />变化不到 3 m</span>
        <span><i className="lv-down" />压力降低 ≥ 3 m</span>
        <span className="muted">圆越大变化越大 · 相对{otherLabel ?? '对照'}</span>
      </div>
    )
  }
  return (
    <div className="legend">
      {layer === 'supply' ? (
        <>
          <span><i className="lv-ok" />供需比 ≥ {num(ratio * 100, 0)}%</span>
          <span><i className="lv-low" />50%–{num(ratio * 100, 0)}%（较大圆）</span>
          <span><i className="lv-bad" />低于 50%（最大圆）</span>
        </>
      ) : (
        <>
          <span><i className="lv-ok" />压力 ≥ {num(threshold, 0)} m</span>
          <span><i className="lv-low" />0–{num(threshold, 0)} m（较大圆）</span>
          <span><i className="lv-bad" />负压（最大圆）</span>
        </>
      )}
      <span><i className="lv-hollow" />不在受评范围</span>
      <span><i className="ring" />当前时段有违规</span>
      <span><i className="sq" />水源</span>
      <span><i className="dia" />水箱</span>
      <span><i className="dash" />关闭 / 断裂（虚线）</span>
      {layer === 'flow' && <span className="muted">线越粗流量越大</span>}
    </div>
  )
}

export default function App() {
  const [source, setSource] = useState<SourceId>('pair')
  const [benchSel, setBenchSel] = useState<BenchSelection>(DEFAULT_BENCH)
  const [ws, setWs] = useState<Workspace | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [active, setActive] = useState(0)
  const [diff, setDiff] = useState(false)
  const [layer, setLayer] = useState<Layer>('pressure')
  const [frameIdx, setFrameIdx] = useState(0)
  const [selection, setSelection] = useState<Selection>(null)
  const [chartNode, setChartNode] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setPlaying(false)
    load(source, benchSel)
      .then((w) => {
        if (cancelled) return
        const a = w.candidates.length - 1
        const node = worstNode(w.candidates[a]?.verification ?? null)
        setWs(w)
        setActive(a)
        setDiff(false)
        setFrameIdx(defaultFrame(w))
        setSelection(node ? { type: 'node', id: node } : null)
        setChartNode(node)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setWs(null)
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [source, benchSel])

  const cand = ws?.candidates[active] ?? null
  const other = ws ? otherCandidate(ws, active) : null
  const times = useMemo(() => frameTimes(cand?.series ?? null), [cand])
  const time = times[frameIdx] ?? 0
  const frame = useMemo(() => frameAt(cand?.series ?? null, frameIdx), [cand, frameIdx])
  const otherFrame = useMemo(() => other?.series?.frames.find((f) => f.time_s === time) ?? null, [other, time])
  const assessed = useMemo(() => new Set(ws?.task.hard_constraints.node_ids ?? []), [ws])
  const violating = useMemo(() => violatingAt(cand?.verification ?? null, time), [cand, time])
  const violationTimes = useMemo(
    () => [...new Set(cand?.verification?.violations.map((v) => v.start_s) ?? [])],
    [cand],
  )

  const select = useCallback(
    (s: Selection) => {
      setSelection(s)
      if (s?.type === 'node' && ws?.scene.nodes.some((n) => n.id === s.id && n.kind === 'junction')) setChartNode(s.id)
    },
    [ws],
  )

  const pickViolation = useCallback(
    (nodeId: string, t: number) => {
      select({ type: 'node', id: nodeId })
      const i = times.findIndex((x) => x >= t)
      setFrameIdx(i < 0 ? 0 : i)
      setPlaying(false)
    },
    [select, times],
  )

  useEffect(() => {
    if (!playing || times.length < 2) return
    const id = window.setInterval(() => {
      setFrameIdx((i) => {
        if (i + 1 >= times.length) {
          setPlaying(false)
          return i
        }
        return i + 1
      })
    }, 700)
    return () => window.clearInterval(id)
  }, [playing, times.length])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
      if (e.key === 'ArrowRight') {
        setFrameIdx((i) => Math.min(times.length - 1, i + 1))
        e.preventDefault()
      } else if (e.key === 'ArrowLeft') {
        setFrameIdx((i) => Math.max(0, i - 1))
        e.preventDefault()
      } else if (e.key === ' ' && tag !== 'BUTTON') {
        setPlaying((p) => !p)
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [times.length])

  const thr = ws?.task.hard_constraints.min_pressure_m ?? 20
  const ratioThr = ws?.task.hard_constraints.min_supply_ratio ?? 0.95
  // 地图、时间轴、曲线用当前候选自己的任务：场景库里正常工况没有故障，不能挂故障工况的标记。
  const ctask = cand?.task ?? ws?.task ?? null

  return (
    <div className="app">
      <TopBar source={source} onSource={setSource} ws={ws} loading={loading} />

      {error && (
        <div className="error-panel" role="alert">
          <h2>读不到「{SOURCES[source].label}」的数据</h2>
          <p>{error}</p>
          <p>
            请先启动{SOURCES[source].server}。在仓库根目录运行：
          </p>
          <pre>{START_HINT[source]}</pre>
          <p className="note">真实结果来自 benchmarks/student1 里的压缩包，解压到 artifacts/ 后即可启动，详见 apps/web/README.md。</p>
        </div>
      )}

      {ws && cand && (
        <div className="workspace">
          <TaskPanel ws={ws} benchSel={benchSel} onBenchSel={setBenchSel} />

          <main className="center">
            <KpiStrip
              active={cand}
              other={other}
              threshold={thr}
              picked={selection?.type === 'kpi' ? selection.key : null}
              onPick={(k) => select({ type: 'kpi', key: k })}
            />

            <section className="panel map-panel" aria-label="管网状态">
              <div className="panel-head">
                <div className="seg" role="group" aria-label="查看方案">
                  {ws.candidates.map((c, i) => (
                    <button type="button" key={c.key} aria-pressed={i === active} onClick={() => setActive(i)}>
                      {c.label}
                    </button>
                  ))}
                </div>
                <div className="seg" role="group" aria-label="图层">
                  {(
                    [
                      ['pressure', '压力'],
                      ['supply', '供需比'],
                      ['flow', '流量'],
                    ] as [Layer, string][]
                  ).map(([k, name]) => (
                    <button type="button" key={k} aria-pressed={!diff && layer === k} onClick={() => { setLayer(k); setDiff(false) }}>
                      {name}
                    </button>
                  ))}
                  {other && (
                    <button type="button" aria-pressed={diff} onClick={() => setDiff(!diff)}>
                      相对{other.label}的变化
                    </button>
                  )}
                </div>
              </div>
              <div className="map-caption">
                <strong className="num">
                  {clock(time)} · {cand.label}
                  {diff && other ? ` 相对 ${other.label} 的压力变化` : ''}
                </strong>
                <span className="muted">
                  {ws.scene.provenance === 'derived'
                    ? '坐标是示意布局，不代表真实距离'
                    : '合成调试拓扑（开发样例）'}
                </span>
              </div>
              <NetworkView
                scene={ws.scene}
                frame={frame}
                baseFrame={otherFrame}
                baseLabel={other?.label ?? null}
                diff={diff && !!other}
                layer={layer}
                threshold={thr}
                ratioThreshold={ratioThr}
                assessed={assessed}
                violating={violating}
                fault={ctask?.fault ?? null}
                actions={cand.plan?.actions ?? []}
                time={time}
                selection={selection}
                onSelect={select}
              />
              <Legend layer={layer} diff={diff && !!other} threshold={thr} ratio={ratioThr} otherLabel={other?.label ?? null} />
              <TimeBar
                times={times}
                idx={frameIdx}
                onIdx={(i) => {
                  setFrameIdx(i)
                  setPlaying(false)
                }}
                end={ws.task.time_window.end_s}
                warmupEnd={ws.task.time_window.warmup_end_s}
                fault={ctask?.fault ?? null}
                actions={cand.plan?.actions ?? []}
                violationTimes={violationTimes}
                playing={playing}
                onPlay={setPlaying}
              />
            </section>

            <div className="center-row">
              <section className="panel" aria-label="压力曲线">
                <div className="panel-head">
                  <h2>压力曲线</h2>
                  <span className="note">{chartNode ? `节点 ${chartNode} · 全部方案叠加` : ''}</span>
                </div>
                <NodeChart
                  candidates={ws.candidates}
                  activeKey={cand.key}
                  nodeId={chartNode}
                  threshold={thr}
                  warmupEnd={ws.task.time_window.warmup_end_s}
                  fault={ws.task.fault}
                  time={time}
                  end={ws.task.time_window.end_s}
                />
              </section>
              <section className="panel" aria-label="检视">
                <div className="panel-head">
                  <h2>检视</h2>
                </div>
                <Inspector
                  ws={ws}
                  active={cand}
                  other={other}
                  selection={selection}
                  frame={frame}
                  otherFrame={otherFrame}
                  time={time}
                />
              </section>
            </div>

            <CandidateCompare source={ws.source} candidates={ws.candidates} active={active} onActive={setActive} />
          </main>

          <aside className="panel right-panel" aria-label="验证与证据">
            <section className="section">
              <h3>推荐准入 · {cand.label}</h3>
              <AdmissionSteps v={cand.verification} label={cand.label} />
            </section>
            <section className="section">
              <h3>违规定位</h3>
              <ViolationList
                v={cand.verification}
                selectedNode={selection?.type === 'node' ? selection.id : null}
                onPick={pickViolation}
              />
            </section>
            <section className="section">
              <h3>运行记录</h3>
              <EventTimeline ws={ws} />
            </section>
            <section className="section">
              <h3>证据</h3>
              <EvidencePanel ws={ws} active={cand} />
            </section>
          </aside>
        </div>
      )}

      <footer className="footer">
        <span>{ws ? MODE_HINT[ws.mode] : '等待数据'}</span>
        {ws?.scene.display_coordinate_note && <span title={ws.scene.display_coordinate_note}>坐标说明：示意布局</span>}
        <span>WaterGuard 初始界面 · Claude 版 · 所有数字来自接口</span>
      </footer>
    </div>
  )
}
