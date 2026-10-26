import {useEffect, useState} from 'react';
import type {ActionPlan, TaskSpec, VerificationReport} from '@waterguard/contracts';
import type {Candidate, RescueChange, StudyKind} from './data';
import {actionText, decisionStudyInput, describePlan, download, num, time} from './data';
import './decision-lab.css';

type Base = {task: TaskSpec; plan: ActionPlan};
type State = 'unknown'|'normal'|'basic'|'gap'|'invalid';
interface Point extends Base {
  point_id: string; state: State; demand_multiplier: number; repair_end_s: number; label: string; cost: number;
  metrics?: VerificationReport['metrics']; report?: VerificationReport; evidence_ref?: string; sampling_reason?: string;
  low_fidelity?: {state: State; metrics: VerificationReport['metrics']; evidence_ref: string};
  prediction?: {min_pressure_m: number; min_supply_ratio: number; uncertainty_m: number; training_pairs: number};
  remaining?: {shortage_m3: number; mean_node_time_pass_ratio: number; emergency_mean_node_time_pass_ratio: number|null};
}
interface Study {
  study_id: string; kind: StudyKind; status: string; points: Point[]; simulations: number; max_simulations: number;
  selected_point_id: string|null; elapsed_s: number; budget_exhausted?: boolean; error?: string;
  fidelity_counts: {low: number; high: number};
  surrogate?: {training_pairs: number; prequential_mae_m: number|null};
}
interface Facility {link_id: string; kind: string; initial_status: string; allowed_actions: string[]}
const labels: Record<State,string>={unknown:'未精验',normal:'正常层达标',basic:'基本供水达标',gap:'存在供水缺口',invalid:'计算无效'};
const statuses: Record<string,string>={QUEUED:'等待计算',RUNNING:'正在计算',COMPLETED:'研究完成',CANCELLED:'已取消',ERROR:'计算失败',INTERRUPTED:'计算中断',BUDGET_EXHAUSTED:'时间预算结束'};
const running=(s?:Study|null)=>s && ['QUEUED','RUNNING'].includes(s.status);
async function api<T>(path:string, body?:unknown, signal?:AbortSignal):Promise<T> {
  const response=await fetch(`/wg/api${path}`,{...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),signal});
  const value=await response.json();
  if(!response.ok) throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));
  return value;
}

export default function DecisionLab({candidate,runId,inspect}:{candidate?:Candidate;runId?:string;inspect:(title:string,value:unknown)=>void}) {
  const [sample,setSample]=useState<Base|null>(null),[plans,setPlans]=useState<ActionPlan[]>([]);
  const base=sample ?? (candidate?.task.fault?candidate:null);
  const [network,setNetwork]=useState('epa-net1'),[kind,setKind]=useState<StudyKind>('boundary');
  const [study,setStudy]=useState<Study|null>(null),[history,setHistory]=useState<Study[]>([]),[pointId,setPointId]=useState('');
  const [error,setError]=useState(''),[loading,setLoading]=useState(false),[budget,setBudget]=useState(12);
  const [surrogate,setSurrogate]=useState(true),[highBudget,setHighBudget]=useState(4);
  const [demands,setDemands]=useState(''),[repairs,setRepairs]=useState('');
  const [rescueOptions,setRescueOptions]=useState<RescueChange[]>([]),[target,setTarget]=useState<'normal'|'basic'>('normal');
  const [facilities,setFacilities]=useState<Facility[]>([]),[facilityId,setFacilityId]=useState(''),[cost,setCost]=useState(3);
  const [actionTime,setActionTime]=useState(3600),[eventTime,setEventTime]=useState(4500),[responseTime,setResponseTime]=useState(600),[repairEnd,setRepairEnd]=useState(8100);
  const busy=Boolean(running(study)||loading);
  const selected=study?.points.find(p=>p.point_id===pointId) ?? study?.points.find(p=>p.state!=='unknown') ?? study?.points[0];
  useEffect(()=>{api<Study[]>('/decision-studies').then(setHistory).catch(e=>setError(String(e)));},[]);
  useEffect(()=>{
    if(!base?.task.fault)return;
    const t=base.task,f=t.fault!,step=t.time_window.step_s;
    setDemands([.9,1,1.1,1.2,1.3].map(x=>Number((x*t.hydraulic_options!.demand_multiplier).toFixed(3))).join(', '));
    setRepairs([...new Set([-2,-1,0,1,2].map(x=>f.end_s+x*step).filter(x=>x>f.start_s&&x<=t.time_window.end_s))].join(', '));
    setRescueOptions([1,2].map((x)=>({label:`抢修提前 ${x*step/60} 分钟`,cost:x,repair_end_s:f.end_s-x*step})).filter(x=>x.repair_end_s>f.start_s));
    setActionTime(t.action_limits.earliest_s);setEventTime(f.start_s+step);setRepairEnd(Math.min(t.time_window.end_s,f.end_s+step));
    const controller=new AbortController();
    api<{catalog:{facilities:Facility[]}}>(`/networks/${t.network_ref.network_id}`,undefined,controller.signal).then(d=>{
      setFacilities(d.catalog.facilities);setFacilityId(d.catalog.facilities.find(f=>f.kind==='pump')?.link_id??d.catalog.facilities[0].link_id);
    }).catch(e=>{if(!controller.signal.aborted)setError(String(e));});
    return()=>controller.abort();
  },[base?.task.task_id]);
  useEffect(()=>{
    if(!running(study))return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const refresh=async()=>{
      try{const value=await api<Study>(`/decision-studies/${study!.study_id}`,undefined,controller.signal);
        if(controller.signal.aborted)return;setStudy(value);
        if(running(value))timer=setTimeout(refresh,700);
        else api<Study[]>('/decision-studies').then(setHistory);
      }catch(e){if(!controller.signal.aborted)setError(String(e));}
    };
    timer=setTimeout(refresh,300);
    return()=>{controller.abort();clearTimeout(timer);};
  },[study?.study_id,study?.status]);
  async function sampleTask(){
    setLoading(true);setError('');
    try{const task=await api<TaskSpec>('/tasks/from-proposal',{network_id:network,fault_kind:'closure',threshold_mode:'manual',min_pressure_m:20,min_supply_ratio:.95,candidate_limit:6});
      const candidates=await api<ActionPlan[]>(`/tasks/${task.task_id}/candidate-plans`);setPlans(candidates);setSample({task,plan:candidates[0]});
    }catch(e){setError(String(e));}finally{setLoading(false);}
  }
  async function start(){
    if(!base)return;setLoading(true);setError('');
    try{
      const request=decisionStudyInput(base.task,base.plan,kind,{budget,rescueOptions,target,eventTime,responseTime,repairEnd,useSurrogate:surrogate,highBudget});
      if(kind==='boundary'){
        request.demand_multipliers=demands.split(/[,，\s]+/).filter(Boolean).map(Number);
        request.repair_end_times_s=repairs.split(/[,，\s]+/).filter(Boolean).map(Number);
      }
      const result=await api<Study>('/decision-studies',{...request,source_run_id:sample?null:runId});setStudy(result);setPointId('');
    }catch(e){setError(String(e));}finally{setLoading(false);}
  }
  function addOperation(){
    const f=facilities.find(f=>f.link_id===facilityId)!;
    const operation=(f.kind==='pump'?(f.initial_status==='closed'?'pump_on':'pump_off'):(f.initial_status==='closed'?'link_open':'link_close')) as NonNullable<RescueChange['extra_action']>['kind'];
    setRescueOptions([...rescueOptions,{label:`${actionText[operation]} ${f.link_id}`,cost,extra_action:{kind:operation,link_id:f.link_id,simulation_time_s:actionTime}}]);
  }
  const verified=study?.points.filter(p=>p.state!=='unknown').length??0;
  const axes=study?{demands:[...new Set(study.points.map(p=>p.demand_multiplier))].sort((a,b)=>a-b),repairs:[...new Set(study.points.map(p=>p.repair_end_s))].sort((a,b)=>a-b)}:null;
  const best=study?.points.find(p=>p.point_id===study.selected_point_id);
  return <div className="decision-lab">
    <header className="decision-heading"><div><span className="eyebrow">DECISION STUDIO</span><h1>决策实验</h1><p>预演变化，找到边界，比较下一步。</p></div>
      <div className="decision-source"><select aria-label="示例管网" value={network} onChange={e=>setNetwork(e.target.value)}><option value="epa-net1">EPA Net1</option><option value="epa-net3">EPA Net3</option><option value="china-zhijiang">中国 Zhi Jiang</option></select><button className="button secondary" disabled={busy} onClick={sampleTask}>载入情景任务</button></div></header>
    {error&&<div className="decision-error" role="alert">{error}</div>}
    <div className="decision-layout"><aside className="decision-controls">
      <div className="decision-card"><span className="eyebrow">研究对象</span>{base?<><h3>{base.task.network_ref.network_id}</h3><p>{describePlan(base.plan)}</p><small>故障 {time(base.task.fault!.start_s)} · 抢修完成 {time(base.task.fault!.end_s)}</small>
        {sample&&<label>原始候选<select aria-label="原始候选" disabled={busy} value={sample.plan.candidate_id} onChange={e=>setSample({...sample,plan:plans.find(p=>p.candidate_id===e.target.value)!})}>{plans.map(p=><option key={p.candidate_id} value={p.candidate_id}>{describePlan(p)}</option>)}</select></label>}
        {sample&&candidate?.task.fault&&<button className="button secondary" disabled={busy} onClick={()=>setSample(null)}>使用工作台所选方案</button>}</>:<p>载入情景任务，或从工作台带入已有候选。</p>}</div>
      <div className="decision-card"><div className="decision-tabs" role="group" aria-label="研究类型">{(['boundary','rescue','event'] as StudyKind[]).map((k,i)=><button aria-pressed={kind===k} key={k} disabled={busy} onClick={()=>setKind(k)}>{['失效边界','最小补救','事件重评'][i]}</button>)}</div>
        {kind==='boundary'?<>
          <label>需求倍率<input aria-label="需求倍率" value={demands} disabled={busy} onChange={e=>setDemands(e.target.value)}/></label>
          <label>抢修完成时刻 / s<input aria-label="抢修完成时刻" value={repairs} disabled={busy} onChange={e=>setRepairs(e.target.value)}/></label>
          <label className="decision-toggle"><input type="checkbox" checked={surrogate} disabled={busy} onChange={e=>setSurrogate(e.target.checked)}/>物理信息 GNN＋多保真筛选</label>
          {surrogate&&<label>高精度复验配额<input type="number" aria-label="高精度复验配额" min={2} max={budget-2} value={highBudget} disabled={busy} onChange={e=>setHighBudget(+e.target.value)}/></label>}
        </>:kind==='rescue'?<>
          <label>补救目标<select aria-label="补救目标" value={target} disabled={busy} onChange={e=>setTarget(e.target.value as typeof target)}><option value="normal">正常供水达标</option><option value="basic">基本供水达标</option></select></label>
          <p>所有选项按同一成本分值比较。</p>
          {rescueOptions.map((o,i)=><div className="decision-option" key={i}><span>{o.label}</span><input aria-label={`${o.label}成本`} type="number" min={0} value={o.cost} disabled={busy} onChange={e=>setRescueOptions(rescueOptions.map((v,j)=>j===i?{...v,cost:+e.target.value}:v))}/><button disabled={busy} aria-label={`移除${o.label}`} onClick={()=>setRescueOptions(rescueOptions.filter((_,j)=>j!==i))}>×</button></div>)}
          <label>增加假设操作权限<select aria-label="补救设施" value={facilityId} disabled={busy} onChange={e=>setFacilityId(e.target.value)}>{facilities.filter(f=>f.allowed_actions.length).map(f=><option value={f.link_id} key={f.link_id}>{f.kind==='pump'?'泵':'管段'} {f.link_id}</option>)}</select></label>
          <div className="decision-pair"><label>动作时刻 / s<input type="number" value={actionTime} disabled={busy} onChange={e=>setActionTime(+e.target.value)}/></label><label>成本分值<input aria-label="新增操作成本" type="number" min={0} value={cost} disabled={busy} onChange={e=>setCost(+e.target.value)}/></label></div>
          <button className="button secondary" disabled={busy||!facilityId} onClick={addOperation}>加入比较</button>
        </>:<>
          <label>事件发生时刻 / s<input aria-label="事件发生时刻" type="number" value={eventTime} disabled={busy} onChange={e=>setEventTime(+e.target.value)}/></label>
          <label>响应时间 / s<input aria-label="响应时间" type="number" min={0} value={responseTime} disabled={busy} onChange={e=>setResponseTime(+e.target.value)}/></label>
          <label>新的抢修完成时刻 / s<input aria-label="新的抢修完成时刻" type="number" value={repairEnd} disabled={busy} onChange={e=>setRepairEnd(+e.target.value)}/></label>
          <p>重放已执行动作，比较事件后剩余时段。</p>
        </>}
        <label>总仿真预算<input aria-label="总仿真预算" type="number" min={kind==='boundary'&&surrogate?4:1} max={64} value={budget} disabled={busy} onChange={e=>{setBudget(+e.target.value);setHighBudget(Math.min(highBudget,+e.target.value-2));}}/></label>
        <button className="button primary decision-start" disabled={!base||busy} onClick={start}>{loading?'提交中…':'开始研究'}</button>
      </div>
      <div className="decision-card"><h3>研究记录</h3>{history.length===0?<p>暂无研究记录</p>:history.map(s=><button className="decision-history" key={s.study_id} disabled={busy} onClick={()=>{setStudy(s);setPointId('');}}><span>{s.kind==='boundary'?'边界探索':s.kind==='rescue'?'补救比较':'事件重评'}</span><small>{statuses[s.status]} · {s.simulations} 次</small></button>)}</div>
    </aside><section className="decision-results" aria-live="polite">
      {!study?<div className="decision-empty"><span className="eyebrow">EXPLORE THE POSSIBILITIES</span><h2>当前方案，能应对多大的变化？</h2><p>选择需求和抢修范围，精确计算一部分情景，逐步勾勒方案的适用边界。</p><div><b>01 预演条件</b><b>02 复验边界</b><b>03 比较补救</b></div></div>:<>
        <div className="decision-card"><div className="decision-result-title"><h2>{study.kind==='boundary'?'方案适用边界':study.kind==='rescue'?'补救选项比较':'事件后处置比较'}</h2><span className="badge neutral">{statuses[study.status]}</span></div>
          <div className="decision-stats"><div><strong>{verified}<small> / {study.points.length}</small></strong><span>已精验情景</span></div><div><strong>{study.simulations}<small> / {study.max_simulations}</small></strong><span>总仿真调用</span></div><div><strong>{num(study.elapsed_s,1)}<small> s</small></strong><span>已用时间</span></div></div>
          {study.fidelity_counts.low>0&&<div className="decision-model"><span>粗算 {study.fidelity_counts.low} · 精算 {study.fidelity_counts.high}</span><span>代理配对 {study.surrogate?.training_pairs??0}</span><span>复验前压力 MAE {study.surrogate?.prequential_mae_m==null?'—':`${num(study.surrogate.prequential_mae_m,3)} m`}</span></div>}
          {study.error&&<p role="alert" className="decision-error">{study.error}</p>}
          <div className="decision-actions">{running(study)&&<button className="button secondary" onClick={()=>api<Study>(`/decision-studies/${study.study_id}/cancel`,{}).then(setStudy).catch(e=>setError(String(e)))}>取消研究</button>}<button className="button secondary" onClick={()=>download(`${study.study_id}.json`,study)}>导出研究</button></div>
        </div>
        {study.kind==='boundary'&&axes?<div className="decision-card decision-map-card"><div className="decision-legend">{Object.entries(labels).map(([key,value])=><span key={key}><i className={`decision-dot ${key}`}/>{value}</span>)}</div>
          <div className="decision-map-scroll"><table className="decision-map"><caption>需求倍率 × 抢修完成时刻</caption><thead><tr><th>完成时刻</th>{axes.demands.map(d=><th key={d}>{d}×</th>)}</tr></thead><tbody>{axes.repairs.map(r=><tr key={r}><th>{time(r)}</th>{axes.demands.map(d=>{const p=study.points.find(p=>p.demand_multiplier===d&&p.repair_end_s===r)!;return <td key={d}><button className={`decision-cell ${p.state} ${selected?.point_id===p.point_id?'selected':''}`} aria-label={`需求${d}倍，抢修${time(r)}，${labels[p.state]}`} aria-pressed={selected?.point_id===p.point_id} onClick={()=>setPointId(p.point_id)}><b>{p.state==='unknown'?p.prediction?'预测':'未测':p.state==='normal'?'正常':p.state==='basic'?'基本':p.state==='gap'?'缺口':'无效'}</b><small>{p.metrics?`${num(p.metrics.shortage_m3,1)} m³`:p.prediction?`${num(p.prediction.min_pressure_m,1)} m`:'—'}</small></button></td>;})}</tr>)}</tbody></table></div>
          <p className="decision-note">实色仅表示高精度已验证结果；带预测值的点仍待精验。</p>
        </div>:<div className="decision-card decision-options"><table><thead><tr><th>方案</th><th>{study.kind==='rescue'?'成本分值':'剩余缺供 / m³'}</th><th>正常平均达标</th><th>状态</th></tr></thead><tbody>{study.points.map(p=><tr key={p.point_id} className={selected?.point_id===p.point_id?'active':''}><td><button onClick={()=>setPointId(p.point_id)}>{p.label}{p.point_id===study.selected_point_id?' · 已测优选':''}</button></td><td>{study.kind==='rescue'?p.cost:num(p.remaining?.shortage_m3)}</td><td>{p.metrics?`${num((p.remaining?.mean_node_time_pass_ratio??p.metrics.mean_node_time_pass_ratio??0)*100,1)}%`:'—'}</td><td>{labels[p.state]}</td></tr>)}</tbody></table>
          {best?<div className="decision-best"><b>{study.kind==='rescue'?'最小已测达标补救':'已测优选后续计划'}：{best.label}</b><p>{describePlan(best.plan)}</p></div>:<p className="decision-note">尚未找到符合目标的已测选项。</p>}</div>}
        {selected&&<div className="decision-card"><div className="decision-result-title"><h3>{selected.label||`${selected.demand_multiplier}×需求 · ${time(selected.repair_end_s)}抢修完成`}</h3><span className={`decision-state ${selected.state}`}>{labels[selected.state]}</span></div>
          {selected.sampling_reason&&<p className="decision-note">{selected.sampling_reason}</p>}
          {selected.prediction&&<div className="decision-model"><span>代理最低压力 {num(selected.prediction.min_pressure_m,2)} m</span><span>PDD供水比 {num(selected.prediction.min_supply_ratio*100,1)}%</span><span>经验不确定度 {num(selected.prediction.uncertainty_m,2)} m</span></div>}
          {selected.metrics&&<div className="decision-stats"><div><strong>{num(selected.metrics.shortage_m3,2)}</strong><span>全窗缺供 / m³</span></div><div><strong>{num(selected.metrics.min_pressure_m,2)}</strong><span>最低压力 / m</span></div><div><strong>{num((selected.metrics.mean_node_time_pass_ratio??0)*100,1)}%</strong><span>平均达标供水时长</span></div></div>}
          {selected.low_fidelity&&<p className="decision-note">粗解：{labels[selected.low_fidelity.state]} · 缺供 {num(selected.low_fidelity.metrics.shortage_m3,2)} m³</p>}
          {selected.report&&<><div className="decision-actions"><button className="button secondary" onClick={()=>inspect('独立验证报告',selected.report)}>查看验证报告</button><button className="button secondary" onClick={()=>inspect('情景任务与动作',{task:selected.task,plan:selected.plan,evidence_ref:selected.evidence_ref})}>查看情景与证据位置</button></div>{selected.report.violations.length>0&&<div className="decision-violations">{selected.report.violations.slice(0,6).map((v,i)=><div key={i}><b>节点 {v.node_id}</b><span>{time(v.start_s)}—{time(v.end_s)}</span><span>{v.code==='MIN_PRESSURE'?'压力':'供水比例'} {num(v.observed,3)} / {v.threshold}</span></div>)}</div>}</>}
        </div>}
      </>}
    </section></div>
  </div>;
}
