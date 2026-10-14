import type { Candidate, Workspace } from './data';
import { deliveryProvenance, dispatchStatus, num, serviceOutcome, time } from './data';
import './service-outcome.css';

const percent = (v: number | null) => v == null ? '未提供' : `${num(v * 100)}%`;
const change = (v: number | null, unit: string) => v == null ? '无可比基线'
  : `${v > 0 ? '+' : ''}${num(v, unit === '个节点' ? 0 : 2)} ${unit}`;

function provenanceText(p: NonNullable<ReturnType<typeof deliveryProvenance>>) {
  if (p.source === 'refinement' && p.gain) {
    return `交付来源：Agnes 精修（${p.gain.refinementId}），相对最佳固定先行处置（${p.gain.firstAidId}）：基本供水全程节点 ${p.gain.nodes >= 0 ? '+' : ''}${p.gain.nodes}`
      + `${p.gain.priorityNodes ? `，关键用水点 +${p.gain.priorityNodes}` : ''}，应急平均达标占比 ${p.gain.emergencyMeanPp >= 0 ? '+' : ''}${num(p.gain.emergencyMeanPp, 2)} 个百分点，缺水量减少 ${num(p.gain.shortageM3)} m³。仅为已测候选之间的差值。`;
  }
  return p.refinementTried && p.gain
    ? `交付来源：固定先行处置。Agnes 精修候选 ${p.gain.refinementId} 没有优于先行处置 ${p.gain.firstAidId}，增益为零，已如实保留，不声称模型带来改善。`
    : '交付来源：固定先行处置（确定性、快速）。本次没有可比较的模型精修候选，不声称模型带来改善。';
}

export default function ServiceOutcome({ candidate: c, workspace: w }: { candidate: Candidate; workspace: Workspace }) {
  const effect = serviceOutcome(c, w);
  if (!effect) return <section className="service-outcome"><h2>当前方案尚无有效效果评估</h2><p>查看下方验收报告中的无效原因。</p></section>;
  const status = dispatchStatus(c, w);
  const label = status.kind === 'illegal_action' ? status.label : status.kind === 'emergency' ? '应急处置推荐（尽力挽救）'
    : effect.delivery === 'recommendation' ? '完全达标推荐' : '当前候选仿真效果 · 尚非正式交付';
  const provenance = effect.delivery === 'candidate' ? null : deliveryProvenance(w);
  const layers = [{ key: 'normal', title: '正常供水', values: effect.normal,
    floor: `≥${num(c.task.hard_constraints.min_pressure_m)} m / ≥${num(c.task.hard_constraints.min_supply_ratio * 100)}%` },
    ...(effect.emergency ? [{ key: 'emergency', title: '应急降级服务', values: effect.emergency,
      floor: `≥${num(c.task.emergency_floor!.min_pressure_m)} m / ≥${num(c.task.emergency_floor!.min_supply_ratio * 100)}%` }] : [])];
  return <section className="service-outcome" aria-label="方案供水效果">
    <header className="service-outcome-heading">
      <div><span className="service-outcome-label">{label}</span><h2>相对无动作，挽救了多少供水</h2>
        <p>{c.name} · 仿真评估 {time(c.task.time_window.evaluation_start_s ?? c.task.time_window.start_s)}–{time(c.task.time_window.end_s)}</p></div>
      <a className="text-button" href="#remaining-violations">查看剩余供水缺口 ↓</a>
    </header>
    <div className="service-rescue-highlight" aria-label="相对无动作的挽救效果">
      <div><span>相对无动作减少缺水</span><strong>{effect.shortageReduction == null ? '无可比基线' : `${num(effect.shortageReduction)} m³`}</strong>
        <small>{effect.shortageReductionPct == null ? '保留原始结果，不推断减幅' : `${effect.shortageReductionPct >= 0 ? '缺水减少' : '缺水增加'} ${num(Math.abs(effect.shortageReductionPct))}%`}</small></div>
      <div><span>全程应急供水节点增加</span><strong>{change(effect.emergency?.deltaCount ?? null, '个节点')}</strong>
        <small>正常供水节点变化 {change(effect.normal.deltaCount, '个节点')}</small></div>
      <div><span>应急平均达标时长提升</span><strong>{change(effect.emergency?.deltaPp ?? null, '个百分点')}</strong>
        <small>与同任务、同评估时窗的无动作方案比较</small></div>
    </div>
    {provenance && <p className="service-outcome-note" data-testid="delivery-provenance">{provenanceText(provenance)}</p>}
    <div className="service-outcome-grid">
      {layers.map(({ key, title, values: v, floor }) => <article className={`service-layer ${key}`} key={key}>
        <div className="service-layer-heading"><h3>{title}</h3><span>{floor}</span></div>
        <div className="service-layer-values"><div><span>全程覆盖节点</span><strong>{v.count ?? '—'}<small> / {effect.total ?? '—'}</small></strong></div>
          <div><span>平均达标时长占比</span><strong>{percent(v.mean)}</strong></div></div>
        <div className="service-coverage-track" aria-hidden="true"><span style={{ width: `${(v.mean ?? 0) * 100}%` }} /></div>
        <p className="service-delta">无动作 {percent(v.before)} → 采用后 {percent(v.mean)}<b>{change(v.deltaPp, '个百分点')}</b></p>
        <div className="service-layer-footer"><span>全程节点变化 {change(v.deltaCount, '个节点')}</span><span>最差节点 {percent(v.worst)}</span></div>
      </article>)}
    </div>
    <div className="service-outcome-residual">
      <div><span>采用后缺水量</span><strong>{num(effect.shortage)} <small>m³</small></strong>
        <small>{effect.shortageReduction == null ? '无可比基线' : `较无动作${effect.shortageReduction >= 0 ? '减少' : '增加'} ${num(Math.abs(effect.shortageReduction))} m³`}</small></div>
      <div><span>仍有供水缺口的节点</span><strong>{effect.residualNodeCount} <small>/ {effect.total ?? '—'}</small></strong>
        <small>{effect.supplyGapCount} 条剩余供水缺口记录（节点 × 区间 × 要求）</small></div>
      <div><span>同方案扰动复验</span><strong className="service-stress">{c.verification.admission.required_stress_tests_pass === 'pass' ? '已通过' : c.verification.admission.required_stress_tests_pass === 'fail' ? '未通过' : '待验证'}</strong>
        <small>{status.kind === 'emergency' ? '用于说明恢复边界，不否定应急挽救推荐' : '完整恢复状态单独核查'}</small></div>
    </div>
    <p className="service-outcome-note">{status.kind === 'emergency' && '故障后未完全恢复不否定挽救价值：应急处置推荐依据已验证的基本供水保障效果，供人工调度复核。'}剩余供水缺口表示仍有节点或时段低于正常供水目标，不是非法操作；非法操作才属于违规。全程覆盖要求整个评估窗口达标；平均占比是各节点达标时长的等权平均，包含动作生效前的失供时段。以上为当前场景的仿真验证结果。</p>
  </section>;
}
