import type { ReactNode } from 'react'
import type { SeriesFrame } from '@waterguard/contracts'
import { CASE_LABEL, VIOLATION_LABEL, clock, lps, num, pct, shortHash } from '../format'
import { minPressureInWindow, supplyRatio, violationsByNode } from '../model'
import type { CandidateView, KpiKey, Selection, Workspace } from '../types'

interface Props {
  ws: Workspace
  active: CandidateView
  other: CandidateView | null
  selection: Selection
  frame: SeriesFrame | null
  otherFrame: SeriesFrame | null
  time: number
}

const KPI_FIELD: Record<KpiKey, string> = {
  shortage: 'verification.metrics.shortage_m3',
  minp: 'verification.metrics.min_pressure_m',
  ratio: 'verification.metrics.supply_ratio',
  violations: 'verification.violations（条数）',
  actions: 'plan.actions / verification.metrics.action_cost',
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="kv">
      <span>{k}</span>
      <strong>{v}</strong>
    </div>
  )
}

export function Inspector({ ws, active, other, selection, frame, otherFrame, time }: Props) {
  if (!selection) {
    return (
      <div className="inspector empty">
        <p>点管网图上的节点或管段，或者点上方任一指标卡，这里显示详情和数据来源。</p>
      </div>
    )
  }

  if (selection.type === 'kpi') {
    const v = active.verification
    const art = active.result?.artifact
    return (
      <div className="inspector">
        <h3>数字从哪里来</h3>
        <Row k="字段" v={<code>{KPI_FIELD[selection.key]}</code>} />
        <Row k="所属方案" v={active.label} />
        <Row k="仿真编号" v={<code>{active.simulationId}</code>} />
        <Row k="验收报告" v={<code>{v?.verification_id ?? '—'}</code>} />
        <Row k="验收程序版本" v={v?.verifier_version ?? '—'} />
        {art && <Row k="计算引擎" v={art.engine_version.split(';')[0]} />}
        {art && <Row k="输入哈希" v={<code>{shortHash(art.input_hash)}</code>} />}
        {active.result && (
          <Row k="引擎收敛" v={active.result.diagnostics.converged === null ? '未测（样例）' : active.result.diagnostics.converged ? '是' : '否'} />
        )}
        {active.physics && <Row k="物理检查" v={active.physics.passed ? '通过' : '未通过'} />}
        <div className="endpoints">
          {active.endpoints.map((u) => (
            <a key={u} href={u} target="_blank" rel="noreferrer">
              {u}
            </a>
          ))}
        </div>
        <p className="note">
          所有数字由后端验收程序从原始时序复算，前端只做显示。
          {ws.mode === 'fixture' ? '当前是人工设定的开发样例，不是引擎计算结果。' : ''}
        </p>
      </div>
    )
  }

  if (selection.type === 'link') {
    const link = ws.scene.links.find((l) => l.id === selection.id)
    if (!link) return <div className="inspector empty">管网里没有管段 {selection.id}</div>
    const s = frame?.links.find((l) => l.link_id === link.id)
    const os = otherFrame?.links.find((l) => l.link_id === link.id)
    const isFault = ws.task.fault?.link_id === link.id
    const acts = active.plan?.actions.filter((a) => a.link_id === link.id) ?? []
    return (
      <div className="inspector">
        <h3>
          {link.kind === 'pump' ? '水泵' : link.kind === 'valve' ? '阀门' : '管段'} {link.id}
          {isFault && <span className="chip bad">故障位置</span>}
          {acts.length > 0 && <span className="chip ok">本方案操作</span>}
        </h3>
        <Row k="连接" v={`${link.from_node} → ${link.to_node}`} />
        <Row k="初始状态" v={link.initial_status === 'open' ? '开启' : '关闭'} />
        <Row k={`${clock(time)} 状态 · ${active.label}`} v={s ? `${s.physical_state ?? s.status}` : '无数据'} />
        <Row k={`${clock(time)} 流量 · ${active.label}`} v={s ? lps(s.flow_m3s) : '—'} />
        {other && <Row k={`${clock(time)} 流量 · ${other.label}`} v={os ? lps(os.flow_m3s) : '—'} />}
        {acts.map((a, i) => (
          <Row key={i} k="动作" v={`${a.kind} @ ${clock(a.simulation_time_s)}`} />
        ))}
        {isFault && ws.task.fault && <p className="note">故障映射：{ws.task.fault.mapping}</p>}
      </div>
    )
  }

  const node = ws.scene.nodes.find((n) => n.id === selection.id)
  if (!node) return <div className="inspector empty">管网里没有节点 {selection.id}</div>
  if (node.kind !== 'junction') {
    const t = frame?.tanks?.find((x) => x.node_id === node.id)
    return (
      <div className="inspector">
        <h3>
          {node.kind === 'reservoir' ? '水源' : '水箱'} {node.id}
        </h3>
        <Row k="高程 / 水头" v={`${num(node.elevation_m)} m`} />
        {t && <Row k={`${clock(time)} 水位`} v={`${num(t.level_m, 2)} m`} />}
        {t && <Row k={`${clock(time)} 蓄水量`} v={`${num(t.volume_m3, 0)} m³`} />}
        {t && <Row k={`${clock(time)} 净流入`} v={lps(t.net_inflow_m3s)} />}
      </div>
    )
  }
  const s = frame?.nodes.find((n) => n.node_id === node.id)
  const os = otherFrame?.nodes.find((n) => n.node_id === node.id)
  const tw = ws.task.time_window
  const assessed = ws.task.hard_constraints.node_ids.includes(node.id)
  const viols = violationsByNode(active.verification).get(node.id) ?? []
  return (
    <div className="inspector">
      <h3>
        节点 {node.id}
        {!assessed && <span className="chip">不在受评范围</span>}
        {node.critical && <span className="chip ok">关键节点</span>}
      </h3>
      <Row k="高程" v={`${num(node.elevation_m)} m`} />
      <Row k={`${clock(time)} 压力 · ${active.label}`} v={`${num(s?.pressure_m, 2)} m`} />
      {other && <Row k={`${clock(time)} 压力 · ${other.label}`} v={`${num(os?.pressure_m, 2)} m`} />}
      <Row k={`${clock(time)} 供需比`} v={supplyRatio(s) === null ? '无需水' : pct(supplyRatio(s))} />
      <Row
        k="评估窗口最低压力"
        v={`${num(minPressureInWindow(active.series, node.id, tw.evaluation_start_s, tw.end_s), 2)} m`}
      />
      <Row k="违规条数" v={`${viols.length} 条`} />
      {viols.slice(0, 4).map((x, i) => (
        <div className="viol-line" key={i}>
          {VIOLATION_LABEL[x.code] ?? x.code} · {clock(x.start_s)}–{clock(x.end_s)} · 实测 {num(x.observed, 2)}
          {x.unit === 'm' ? ' m' : ''} / 要求 {num(x.threshold, x.unit === 'm' ? 0 : 2)}
          {x.unit === 'm' ? ' m' : ''}
        </div>
      ))}
      {active.caseName && CASE_LABEL[active.caseName] && <p className="note">工况：{CASE_LABEL[active.caseName]}</p>}
    </div>
  )
}
