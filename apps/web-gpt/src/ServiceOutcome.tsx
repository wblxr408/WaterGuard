import type { Candidate, Workspace } from './data';
import { deliveryProvenance, num, serviceOutcome, time } from './data';
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
  const label = effect.delivery === 'mitigation' ? '应急处置推荐（尽力挽救）'
    : effect.delivery === 'recommendation' ? '完全达标推荐' : '当前候选仿真效果 · 尚非正式交付';
  const provenance = effect.delivery === 'candidate' ? null : deliveryProvenance(w);
  const layers = [{ key: 'normal', title: '正常供水', values: effect.normal,
    floor: `≥${num(c.task.hard_constraints.min_pressure_m)} m / ≥${num(c.task.hard_constraints.min_supply_ratio * 100)}%` },
    ...(effect.emergency ? [{ key: 'emergency', title: '应急降级服务', values: effect.emergency,
      floor: `≥${num(c.task.emergency_floor!.min_pressure_m)} m / ≥${num(c.task.emergency_floor!.min_supply_ratio * 100)}%` }] : [])];
  return <section className="service-outcome" aria-label="方案供水效果">
    <header className="service-outcome-heading">
      <div><span className="service-outcome-label">{label}</span><h2>采用方案后，能达到什么效果</h2>
        <p>{c.name} · 仿真评估 {time(c.task.time_window.evaluation_start_s ?? c.task.time_window.start_s)}–{time(c.task.time_window.end_s)}</p></div>
      <a className="text-button" href="#remaining-violations">查看剩余违规 ↓</a>
    </header>
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
      <div><span>仍有未达标时段的节点</span><strong>{effect.residualNodeCount} <small>/ {effect.total ?? '—'}</small></strong>
        <small>{effect.violationCount} 条原始约束违规记录</small></div>
      <div><span>同方案扰动复验</span><strong className="service-stress">{c.verification.admission.required_stress_tests_pass === 'pass' ? '已通过' : c.verification.admission.required_stress_tests_pass === 'fail' ? '未通过' : '待验证'}</strong>
        <small>{effect.violationCount ? '仍未完全达标' : '推荐资格以完整验收为准'}</small></div>
    </div>
    <p className="service-outcome-note">{effect.delivery === 'mitigation' && '应急处置推荐依据已验证方案的民生保供排序，供人工调度复核；完整达标准入与剩余违规见下方。'}全程覆盖要求整个评估窗口达标；平均占比是各节点达标时长的等权平均，包含动作生效前的失供时段。以上为当前场景的仿真验证结果。</p>
  </section>;
}
