import { ACTION_LABEL, CASE_LABEL, FAULT_LABEL, clock, num, shortHash } from '../format'
import type { BenchSelection, Workspace } from '../types'

interface Props {
  ws: Workspace
  benchSel: BenchSelection
  onBenchSel: (s: BenchSelection) => void
}

function uniq(xs: string[]) {
  return [...new Set(xs)]
}

function BenchPicker({ ws, benchSel, onBenchSel }: Props) {
  const cases = ws.index?.cases ?? []
  const networks = uniq(cases.map((c) => c.network_id))
  const engines = uniq(cases.filter((c) => c.network_id === benchSel.network).map((c) => c.engine ?? ''))
  const names = uniq(
    cases.filter((c) => c.network_id === benchSel.network && (c.engine ?? '') === benchSel.engine).map((c) => c.case),
  )
  const set = (patch: Partial<BenchSelection>) => {
    const next = { ...benchSel, ...patch }
    const ok = (n: string, e: string, k: string) =>
      cases.some((c) => c.network_id === n && (c.engine ?? '') === e && c.case === k)
    if (!ok(next.network, next.engine, next.caseName)) {
      const fallback = cases.find((c) => c.network_id === next.network && (patch.engine ? (c.engine ?? '') === next.engine : true))
      if (fallback) {
        next.engine = fallback.engine ?? ''
        if (!ok(next.network, next.engine, next.caseName)) next.caseName = fallback.case
      }
    }
    onBenchSel(next)
  }
  return (
    <section className="section">
      <h3>选择场景</h3>
      <label className="field">
        <span>管网</span>
        <select value={benchSel.network} onChange={(e) => set({ network: e.target.value })}>
          {networks.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>计算引擎</span>
        <select value={benchSel.engine} onChange={(e) => set({ engine: e.target.value })}>
          {engines.map((n) => (
            <option key={n} value={n}>
              {n.toUpperCase()}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>工况</span>
        <select value={benchSel.caseName} onChange={(e) => set({ caseName: e.target.value })}>
          {names.map((n) => (
            <option key={n} value={n}>
              {CASE_LABEL[n] ?? n}
            </option>
          ))}
        </select>
      </label>
      <p className="note">选中故障工况时，自动加载同一管网、同一引擎的正常工况作为对照。</p>
    </section>
  )
}

export function TaskPanel(props: Props) {
  const { ws } = props
  const t = ws.task
  const f = t.fault
  const hc = t.hard_constraints
  const al = t.action_limits
  const tw = t.time_window
  const ho = t.hydraulic_options
  return (
    <aside className="panel task-panel" aria-label="事件与任务">
      {ws.source === 'bench' && <BenchPicker {...props} />}
      <section className="section">
        <h3>事件</h3>
        {f ? (
          <>
            <div className="kv">
              <span>故障类型</span>
              <strong>{FAULT_LABEL[f.kind] ?? f.kind}</strong>
            </div>
            <div className="kv">
              <span>位置</span>
              <strong className="num">管段 {f.link_id}</strong>
            </div>
            <div className="kv">
              <span>持续</span>
              <strong className="num">
                {clock(f.start_s)}–{clock(f.end_s)}
              </strong>
            </div>
            {f.leak_area_m2 !== undefined && (
              <div className="kv">
                <span>破口面积</span>
                <strong className="num">{num(f.leak_area_m2 * 1e4, 1)} cm²</strong>
              </div>
            )}
          </>
        ) : (
          <p className="note">正常工况，没有故障。</p>
        )}
      </section>

      <section className="section">
        <h3>
          保供要求 <span className="chip">硬约束</span>
        </h3>
        <div className="kv">
          <span>节点最低压力</span>
          <strong className="num">≥ {num(hc.min_pressure_m, 0)} m</strong>
        </div>
        <div className="kv">
          <span>节点供需比</span>
          <strong className="num">≥ {num(hc.min_supply_ratio * 100, 0)}%</strong>
        </div>
        <div className="kv">
          <span>受评节点</span>
          <strong className="num">{hc.node_ids.length} 个</strong>
        </div>
        <p className="source">来源：{hc.source}</p>
      </section>

      <section className="section">
        <h3>可操作范围</h3>
        <div className="kv">
          <span>动作类型</span>
          <strong>{al.allowed_kinds.map((k) => ACTION_LABEL[k] ?? k).join('、')}</strong>
        </div>
        <div className="kv">
          <span>可操作设施</span>
          <strong className="num">{al.allowed_link_ids.length} 处</strong>
        </div>
        <div className="kv">
          <span>最多操作</span>
          <strong className="num">{al.max_actions} 次</strong>
        </div>
        <div className="kv">
          <span>允许时段</span>
          <strong className="num">
            {clock(al.earliest_s)}–{clock(al.latest_s)}
          </strong>
        </div>
      </section>

      <section className="section">
        <h3>计算设定</h3>
        <div className="kv">
          <span>评估窗口</span>
          <strong className="num">
            {clock(tw.evaluation_start_s)}–{clock(tw.end_s)}
          </strong>
        </div>
        <div className="kv">
          <span>预运行</span>
          <strong className="num">
            {clock(tw.start_s)}–{clock(tw.warmup_end_s)}
          </strong>
        </div>
        <div className="kv">
          <span>输出步长</span>
          <strong className="num">{num(tw.step_s / 60, 0)} 分钟</strong>
        </div>
        {ho && (
          <div className="kv">
            <span>压力驱动需水</span>
            <strong className="num">
              {num(ho.minimum_pressure_m, 0)}–{num(ho.required_pressure_m, 0)} m，指数 {num(ho.pressure_exponent, 1)}
            </strong>
          </div>
        )}
        {t.perturbation_domain.required.length > 0 && (
          <div className="kv">
            <span>必须复验</span>
            <strong>{t.perturbation_domain.required.join('、')}</strong>
          </div>
        )}
      </section>

      {t.control_policy && t.control_policy.overrides.length > 0 && (
        <section className="section">
          <h3>控制覆盖</h3>
          {t.control_policy.overrides.map((o) => (
            <p className="source" key={o.link_id}>
              设施 {o.link_id}：{o.reason}
            </p>
          ))}
        </section>
      )}

      <section className="section">
        <h3>假设</h3>
        <ul className="plain">
          {t.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
        <p className="source num">
          任务 {t.task_id} · 版本 {t.revision} · 约束哈希 {shortHash(t.constraint_hash)}
        </p>
      </section>
    </aside>
  )
}
