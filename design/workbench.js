"use strict";
const $ = id => document.getElementById(id);
const terminal = new Set(["COMPLETED", "ERROR", "CANCELLED", "BUDGET_EXHAUSTED"]);
const state = {details: null, task: null, run: null, results: [], selected: null, series: null,
  node: null, events: new Map(), timer: null, epoch: 0, selectionEpoch: 0, busy: false};
const names = {pass: "通过", fail: "未通过", pending: "待完成", valid: "有效", invalid: "无效",
  QUEUED: "排队", RUNNING: "运行中", COMPLETED: "计算结束", ERROR: "运行失败",
  CANCELLED: "已取消", BUDGET_EXHAUSTED: "预算耗尽", NEEDS_INPUT: "等待输入"};
const show = value => names[value] || value || "—";
const number = (value, digits = 2) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
const percent = value => value == null ? "—" : number(value * 100) + "%";
const fmtTime = seconds => number(seconds / 3600, 2) + " 小时（" + seconds + " s）";
function element(tag, text, className) { const el = document.createElement(tag); if(text != null) el.textContent = text; if(className) el.className = className; return el; }
function error(message = "") { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, options = {}) {
  const response = await fetch(path, {cache: "no-store", ...options, headers: {"Content-Type": "application/json", ...options.headers}});
  const body = await response.json();
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === "object" && !Array.isArray(detail) ? `${detail.code || response.status}: ${detail.message}` : JSON.stringify(detail || body));
  }
  return body;
}
function controls() {
  const active = state.run && !terminal.has(state.run.status);
  $("taskfields").disabled = state.busy || active || !state.details;
  $("faultlink").disabled = $("faultkind").value === "normal";
  $("startbutton").disabled = state.busy || active || !state.details;
  $("cancelbutton").disabled = state.busy || !active;
  $("history").disabled = state.busy || active;
  $("refreshbutton").disabled = state.busy;
}
function showPage(name) {
  document.querySelectorAll(".page").forEach(p => p.classList.toggle("active", p.id === name));
  document.querySelectorAll(".nav [data-page]").forEach(b => {
    b.classList.toggle("active", b.dataset.page === name); b.setAttribute("aria-current", b.dataset.page === name ? "page" : "false");
  });
  window.scrollTo({top:0, behavior:"instant"});
}
function clearResults() {
  state.results = []; state.selected = null; state.series = null; state.node = null;
  state.selectionEpoch++;
  ["ratio", "pressure", "shortage"].forEach(id => $(id).textContent = "—");
  $("candidatebuttons").replaceChildren(element("span", "等待候选", "sub"));
  $("comparisonbody").replaceChildren();
  $("admission").textContent = "尚无验证报告"; $("violations").replaceChildren();
  $("actiontext").textContent = "等待仿真"; $("pressurechart").textContent = "选择已计算候选，再点击用户节点。";
  $("timeindex").disabled = true; $("timelabel").textContent = "—";
  $("nodedetail").textContent = "等待当前管网与候选数据。"; $("charttitle").textContent = "节点压力时序";
  $("mapstatus").textContent = "等待数据";
  $("proofdrawer").textContent = ""; $("evidence").textContent = "";
  $("evidenceDownload").hidden = true; $("evidencestatus").textContent = "等待当前运行证据清单。";
  $("verdictTitle").textContent = "尚未计算"; $("verdictBody").textContent = "等待当前候选验证报告。";
  $("verdict").className = "verdict";
}
async function loadNetwork() {
  clearTimeout(state.timer); const epoch = ++state.epoch;
  state.busy = true; state.run = null; state.task = null; state.details = null; controls(); clearResults();
  $("network").replaceChildren(); $("runstatus").textContent = "等待创建任务";
  $("runmeta").textContent = ""; $("modebadge").textContent = "未运行";
  $("tasksummary").textContent = "尚未创建任务"; $("eventlist").replaceChildren(element("li", "暂无运行事件"));
  try {
    const details = await api(`/api/networks/${encodeURIComponent($("networkselect").value)}`);
    if (epoch !== state.epoch) return;
    state.details = details;
    $("faultlink").replaceChildren(...details.fault_link_ids.map(id => { const o = element("option", id); o.value=id; return o; }));
    const defaultLink = details.manifest.network_id === "epa-net1" ? "12" : "123";
    if (details.fault_link_ids.includes(defaultLink)) $("faultlink").value = defaultLink;
    $("networktitle").textContent = details.manifest.network_id + " · " + details.scene.nodes.length + " 节点 / " + details.scene.links.length + " 管段";
    $("mapstatus").textContent = "模型拓扑 · 尚无仿真";
    $("connection").textContent = "后端已连接"; $("connection").className = "pill";
    drawNetwork(); error();
  } catch(e) { error(e.message); $("connection").textContent = "连接失败"; $("connection").className = "pill red"; }
  finally { if(epoch === state.epoch) {state.busy = false; controls();} }
}
async function history() {
  const runs = await api("/api/runs");
  $("history").replaceChildren(element("option", "选择历史运行")); $("history").firstChild.value = "";
  runs.forEach(r => {const o=element("option", `${r.mode} · ${show(r.status)} · ${r.run_id.slice(-8)}`); o.value=r.run_id; $("history").append(o);});
  if(state.run) $("history").value=state.run.run_id;
}
async function connect() {
  try {
    await api("/healthz");
    await loadModelStatus();
    const networks = await api("/api/networks");
    const previous = $("networkselect").value;
    $("networkselect").replaceChildren(...networks.map(n => {const o=element("option", n.network_id); o.value=n.network_id; return o;}));
    if(previous) $("networkselect").value=previous;
    await history();
    if(state.run) await refreshRun(state.epoch); else await loadNetwork();
  } catch(e) { error("无法连接后端：" + e.message); $("connection").textContent = "后端连接失败"; $("connection").className = "pill red"; }
}
const modelEndpoints = {
  'agnes-ai': ['https://api.agnes-ai.cn/v1'],
  'deepseek': ['https://api.deepseek.com'],
  'zai': ['https://open.bigmodel.cn/api/paas/v4', 'https://open.bigmodel.cn/api/coding/paas/v4', 'https://api.z.ai/api/coding/paas/v4', 'https://api.z.ai/api/paas/v4']
};
function modelProviderChanged() {
  $('modelbase').replaceChildren(...modelEndpoints[$('modelprovider').value].map(url => {
    const option=element('option',url); option.value=url; return option;
  }));
  $('modelkey').value=''; $('modelid').value=''; $('modelcatalog').replaceChildren();
  $('modelmessage').textContent='使用该提供商自己的 Key；GLM 地址按普通 API、Coding Plan 或国际账号选择。';
}
async function loadModelStatus() {
  const status=await api('/api/agh/status');
  const provider=status.snapshot?.provider;
  $('aghstatus').textContent=!status.available ? 'AGH 未连接 · 请启动项目服务' :
    provider?.credentialConfigured ? `AGH 已连接 · ${provider.id} / ${provider.model}` : 'AGH 已连接 · 尚未配置可用模型';
}
async function configureModel(action) {
  const config={providerId:$('modelprovider').value,baseUrl:$('modelbase').value};
  if($('modelkey').value.trim())config.apiKey=$('modelkey').value.trim();
  if($('modelid').value.trim())config.model=$('modelid').value.trim();
  $('modelmessage').textContent=action==='test' ? 'AGH 正在测试账号…' : 'AGH 正在验证并保存…';
  try {
    const result=await api(`/api/agh/models/${action}`,{method:'POST',body:JSON.stringify(config)});
    if(action==='test') {
      $('modelcatalog').replaceChildren(...result.models.map(model=>{const option=element('option',model.name);option.value=model.id;return option;}));
      if(!$('modelid').value && result.models.length)$('modelid').value=result.models[0].id;
      $('modelmessage').textContent=result.verified ? '账号验证通过。选择模型后保存。' : '账号未验证通过，请核对 Key 与接口。模型目录不代表账号鉴权成功。';
    } else {
      $('modelkey').value=''; $('modelmessage').textContent='已由 AGH 保存。新任务使用此模型；已有任务保留原选择。';
      await loadModelStatus();
    }
  } catch(e) { $('modelmessage').textContent=e.message; }
}
$('modelprovider').addEventListener('change',modelProviderChanged);
$('modeltest').addEventListener('click',()=>configureModel('test'));
$('modelform').addEventListener('submit',event=>{event.preventDefault();configureModel('save');});
modelProviderChanged();
async function start(event) {
  event.preventDefault(); if(state.busy) return;
  state.busy = true; controls(); error(); clearTimeout(state.timer); const epoch = ++state.epoch;
  clearResults(); state.events.clear(); $("eventlist").replaceChildren(); state.run = null;
  try {
    state.task = await api("/api/tasks/from-proposal", {method:"POST", body:JSON.stringify({
      network_id:$("networkselect").value, fault_kind:$("faultkind").value, link_id:$("faultlink").value,
      min_pressure_m:Number($("minpressure").value), min_supply_ratio:Number($("minsupply").value)/100,
      candidate_limit:Number($("candidatelimit").value)})});
    $("tasksummary").textContent = `${state.task.task_id} · v${state.task.revision} · 全部 ${state.task.hard_constraints.node_ids.length} 个用户节点`;
    state.run = await api(`/api/tasks/${state.task.task_id}/runs`, {method:"POST",body:JSON.stringify({mode:$("runmode").value,idempotency_key:crypto.randomUUID()})});
    localStorage.setItem("waterguard-run", state.run.run_id);
    await refreshRun(epoch); await history();
  } catch(e) { error(e.message); $("runstatus").textContent = "启动失败"; }
  finally {state.busy=false; controls();}
}
async function openRun(runId) {
  clearTimeout(state.timer); const epoch=++state.epoch; state.busy=true; controls(); error(); clearResults(); state.events.clear();
  try {
    const run=await api(`/api/runs/${encodeURIComponent(runId)}`);
    const task=await api(`/api/tasks/${encodeURIComponent(run.task_ref.task_id)}?revision=${run.task_ref.revision}`);
    const details=await api(`/api/networks/${encodeURIComponent(task.network_ref.network_id)}`);
    if(epoch!==state.epoch) return;
    state.run=run; state.task=task; state.details=details;
    $("networkselect").value=details.manifest.network_id;
    $("faultlink").replaceChildren(...details.fault_link_ids.map(id => {const o=element("option",id);o.value=id;return o;}));
    $("faultkind").value=task.fault?.kind || "normal"; $("faultlink").value=task.fault?.link_id || details.fault_link_ids[0];
    $("minpressure").value=task.hard_constraints.min_pressure_m; $("minsupply").value=task.hard_constraints.min_supply_ratio*100;
    $("candidatelimit").value=Math.min(6,task.budget.max_simulations); $("runmode").value=run.mode;
    $("networktitle").textContent=details.manifest.network_id;
    $("tasksummary").textContent=`${task.task_id} · v${task.revision} · 冻结任务`;
    localStorage.setItem("waterguard-run",run.run_id);
    await refreshRun(epoch);
  } catch(e) {error(e.message);}
  finally {if(epoch===state.epoch){state.busy=false;controls();}}
}
async function readEvents(runId, epoch) {
  const last=Math.max(0,...state.events.keys());
  const response=await fetch(`/api/runs/${runId}/events`,{headers:{"Last-Event-ID":String(last)},cache:"no-store"});
  if(!response.ok) throw new Error("事件读取失败："+response.status);
  const stream=await response.text(); if(epoch!==state.epoch) return;
  stream.split(/\r?\n\r?\n/).forEach(block => {
    const data=block.split(/\r?\n/).find(line=>line.startsWith("data: "));
    if(data){const event=JSON.parse(data.slice(6)); if(event.run_id===runId) state.events.set(event.sequence,event);}
  });
  $("eventlist").replaceChildren(...[...state.events.values()].sort((a,b)=>a.sequence-b.sequence).map(e=>element("li",`${e.sequence} · ${e.type} · ${e.origin}${e.candidate_id ? " · "+e.candidate_id : ""}`)));
}
async function refreshRun(epoch) {
  clearTimeout(state.timer);
  if(!state.run || epoch!==state.epoch) return;
  const id=state.run.run_id;
  let awaitingEvidence=false;
  try {
    const [run, results]=await Promise.all([api(`/api/runs/${id}`),api(`/api/runs/${id}/results`)]);
    if(epoch!==state.epoch) return;
    state.run=run; state.results=results;
    $("connection").textContent="后端已连接"; $("connection").className="pill";
    $("modebadge").textContent=run.mode; $("comparisonmode").textContent=run.mode;
    $("runstatus").textContent=`${show(run.status)} · ${run.phase}`;
    $("runmeta").textContent=`${id} · 仿真 ${run.budget_usage.simulations} 次 · 耗时 ${number(run.budget_usage.wall_time_s,1)} s · conclusion=${run.conclusion}`;
    await readEvents(id,epoch); if(epoch!==state.epoch) return;
    renderCandidates();
    if(!state.selected && results.length) await selectCandidate(results[0].plan.candidate_id);
    else if(state.selected){
      const current=results.find(r=>r.plan.candidate_id===state.selected);
      renderReport(current);
      if(!state.series && current?.artifact?.status==="SUCCEEDED") await selectCandidate(state.selected);
    }
    if(run.status==="ERROR"){const record=await api(`/api/runs/${id}/error`);if(epoch===state.epoch)error(record.message);}
    if(terminal.has(run.status)) {
      try {
        const evidence=await api(`/api/runs/${id}/evidence`); if(epoch!==state.epoch)return;
        $("evidence").textContent=JSON.stringify(evidence,null,2);
        $("evidencestatus").textContent=`${evidence.artifacts.length} 个文件 · 缺项：${evidence.missing_items.join("、") || "无"}`;
        $("evidenceDownload").href=`/api/runs/${id}/evidence`; $("evidenceDownload").download=`${id}-evidence.json`; $("evidenceDownload").hidden=false;
      } catch(e) {awaitingEvidence=true;$("evidencestatus").textContent="正在整理运行证据，完成后自动更新。";}
      await history();
    }
    error(run.status==="ERROR" ? $("error").textContent : "");
  } catch(e) {if(epoch===state.epoch){error(e.message);$("connection").textContent="连接中断 · 正在重试";$("connection").className="pill warn";}}
  finally {
    if(epoch===state.epoch){controls(); if(state.run && (!terminal.has(state.run.status) || awaitingEvidence)) state.timer=setTimeout(()=>refreshRun(epoch),1500);}
  }
}
function renderCandidates() {
  $("candidatebuttons").replaceChildren(...state.results.map(r=>{
    const b=element("button",`${r.plan.candidate_id} · ${r.verification ? show(r.verification.admission.nominal_constraints_pass) : terminal.has(state.run?.status) ? show(r.artifact?.status || state.run.status) : "计算中"}`);
    b.classList.toggle("active",r.plan.candidate_id===state.selected);b.setAttribute("aria-pressed",r.plan.candidate_id===state.selected);
    b.onclick=()=>selectCandidate(r.plan.candidate_id).catch(e=>error(e.message));return b;
  }));
  $("comparisonbody").replaceChildren(...state.results.map(r=>{
    const v=r.verification, row=element("tr");
    [r.plan.candidate_id,percent(v?.metrics.supply_ratio),number(v?.metrics.min_pressure_m)+" m",number(v?.metrics.shortage_m3)+" m³",String(r.plan.actions.length),show(v?.admission.simulation_valid),show(v?.admission.nominal_constraints_pass),show(v?.admission.recommendation_eligible)].forEach(t=>row.append(element("td",t)));
    row.tabIndex=0;const select=()=>{showPage("console");selectCandidate(r.plan.candidate_id).catch(e=>error(e.message));}; row.onclick=select;row.onkeydown=e=>{if(e.key==="Enter")select();};return row;
  }));
}
async function selectCandidate(cid) {
  const entry=state.results.find(r=>r.plan.candidate_id===cid); if(!entry)return;
  state.selected=cid; state.series=null;const token=++state.selectionEpoch; const epoch=state.epoch;
  renderCandidates();renderReport(entry);drawNetwork();renderDetail();drawChart();
  if(entry.artifact?.status!=="SUCCEEDED")return;
  const series=await api(`/api/simulations/${entry.artifact.simulation_id}/series`);
  if(token!==state.selectionEpoch || epoch!==state.epoch)return;
  state.series=series;
  if(!series.frames.some(f=>f.nodes.some(n=>n.node_id===state.node))) state.node=series.frames[0]?.nodes[0]?.node_id || null;
  $("timeindex").max=Math.max(0,series.frames.length-1);$("timeindex").value=series.frames.findIndex(f=>f.time_s>=state.task.time_window.evaluation_start_s);
  $("timeindex").disabled=!series.frames.length;drawNetwork();renderDetail();drawChart();
}
function renderReport(entry) {
  if(!entry)return;
  const v=entry.verification;
  $("ratio").textContent=percent(v?.metrics.supply_ratio);$("pressure").textContent=number(v?.metrics.min_pressure_m)+" m";$("shortage").textContent=number(v?.metrics.shortage_m3)+" m³";
  $("actiontext").textContent=entry.plan.actions.length ? entry.plan.actions.map(a=>`${a.kind} · ${a.link_id} · ${fmtTime(a.simulation_time_s)}`).join("；") : "零动作故障基线。";
  $("mapstatus").textContent=entry.plan.candidate_id;
  $("admission").replaceChildren();$("violations").replaceChildren();
  if(v){
    const labels={action_valid:"动作合法",simulation_valid:"仿真有效",nominal_constraints_pass:"名义约束",required_stress_tests_pass:"必要扰动复验",recommendation_eligible:"推荐准入"};
    Object.entries(labels).forEach(([key,label])=>{const row=element("div",null,"kv");row.append(element("span",label),element("strong",show(v.admission[key])));$("admission").append(row);});
    v.violations.slice(0,80).forEach(item=>{const b=element("button",`${item.code} · ${item.node_id} · ${item.start_s} s · ${number(item.observed)} / ${number(item.threshold)}`);b.onclick=()=>{state.node=item.node_id;if(state.series){$("timeindex").value=state.series.frames.findIndex(f=>f.time_s===item.start_s);drawNetwork();renderDetail();drawChart();}};$("violations").append(b);});
    if(v.violations.length>80)$("violations").append(element("p",`另有 ${v.violations.length-80} 项；完整报告见记录字段。`));
    $("verdictTitle").textContent=v.admission.nominal_constraints_pass==="pass" ? "名义工况满足约束" : "候选未满足全部要求";
    const blockers={benchmark_only:"固定枚举计算",native_agh_trace_missing:"缺少 AGH 原生记录",required_stress_tests_pending:"必要扰动复验待完成",fixture_only:"开发样例"};
    $("verdictBody").textContent=`推荐准入：${show(v.admission.recommendation_eligible)}。原因：${v.recommendation_blockers.map(b=>blockers[b] || b).join("、")}。`;
  } else {$("admission").textContent="等待该候选独立验证";$("verdictTitle").textContent="尚无验证结论";$("verdictBody").textContent="等待该候选的原始计算与校验报告。";}
  $("proofdrawer").textContent=JSON.stringify({task:state.task,run:state.run,candidate:entry},null,2);
}
const NS="http://www.w3.org/2000/svg";
function svgElement(tag,attrs={},text){const e=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,String(v)));if(text!=null)e.textContent=text;return e;}
function frame(){return state.series?.frames[Number($("timeindex").value)] || null;}
function drawNetwork() {
  const svg=$("network");svg.replaceChildren();if(!state.details)return;
  const scene=state.details.scene; const xs=scene.nodes.map(n=>n.x_m),ys=scene.nodes.map(n=>n.y_m);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const scale=Math.min(680/Math.max(1,maxX-minX),300/Math.max(1,maxY-minY));
  const positions=new Map(scene.nodes.map(n=>[n.id,[40+(n.x_m-minX)*scale,340-(n.y_m-minY)*scale]]));
  const current=frame();const samples=new Map(current?.nodes.map(n=>[n.node_id,n]) || []);
  const linkSamples=new Map(current?.links.map(l=>[l.link_id,l]) || []);
  const fault=state.task?.fault || ($("faultkind").value!=="normal" ? {link_id:$("faultlink").value} : null);
  scene.links.forEach(l=>{
    const a=positions.get(l.from_node),b=positions.get(l.to_node);const broken=l.id===fault?.link_id;
    const line=svgElement("line",{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:broken?"#b43d39":"#718f81","stroke-width":broken?5:2.5});
    if(linkSamples.get(l.id)?.status==="closed" || broken)line.setAttribute("stroke-dasharray","7 5");
    line.append(svgElement("title",{},`${l.id} · ${l.kind}${broken?" · 故障目标":""}`));svg.append(line);
  });
  scene.nodes.forEach(n=>{
    const [x,y]=positions.get(n.id),sample=samples.get(n.id);const selected=n.id===state.node;
    const color=sample ? sample.pressure_m>=Number(state.task?.hard_constraints.min_pressure_m ?? $("minpressure").value)?"#268978":"#c1783d" : "#a3b7ad";
    const g=svgElement("g",{class:"mapnode",tabindex:0,role:"button","aria-label":"查看节点 "+n.id});
    g.append(svgElement("circle",{cx:x,cy:y,r:selected?9:n.kind==="junction"?5:8,fill:color,stroke:selected?"#183532":color,"stroke-width":selected?3:1}));
    g.append(svgElement("title",{},n.id+" · "+n.kind));
    if(selected || scene.nodes.length<20 || n.kind!=="junction")g.append(svgElement("text",{x:x+9,y:y-8,class:"maplabel"},n.id));
    const select=()=>{state.node=n.id;drawNetwork();renderDetail();drawChart();};g.onclick=select;g.onkeydown=e=>{if(e.key==="Enter" || e.key===" "){e.preventDefault();select();}};svg.append(g);
  });
  $("timelabel").textContent=current?fmtTime(current.time_s):"—";
}
function renderDetail(){
  const sample=frame()?.nodes.find(n=>n.node_id===state.node);
  $("nodedetail").textContent=sample ? `${sample.node_id} · 压力 ${number(sample.pressure_m)} m · 需求 ${number(sample.expected_demand_m3s,5)} m³/s · 实际供水 ${number(sample.delivered_demand_m3s,5)} m³/s` : state.node ? `${state.node} · 所选候选暂无该节点压力样本` : "点击用户节点查看原始压力和供水。";
}
function drawChart(){
  $("charttitle").textContent=(state.node || "节点")+" · 压力时序";
  const points=(state.series?.frames || []).map(f=>({t:f.time_s,p:f.nodes.find(n=>n.node_id===state.node)?.pressure_m})).filter(p=>typeof p.p==="number");
  const box=$("pressurechart");box.replaceChildren();if(!points.length){box.append(element("p","暂无该节点原始压力时序。","sub"));return;}
  const threshold=state.task.hard_constraints.min_pressure_m;
  const low=Math.min(0,threshold,...points.map(p=>p.p)),high=Math.max(threshold,...points.map(p=>p.p),low+1)+2;
  const end=Math.max(1,points.at(-1).t);const x=t=>48+t/end*620,y=p=>145-(p-low)/(high-low)*125;
  const svg=svgElement("svg",{viewBox:"0 0 710 180",role:"img","aria-label":`${state.node} 原始压力曲线，阈值 ${threshold} 米`});
  svg.append(svgElement("line",{x1:48,y1:145,x2:668,y2:145,stroke:"#657874"}),svgElement("line",{x1:48,y1:20,x2:48,y2:145,stroke:"#657874"}));
  svg.append(svgElement("line",{x1:48,y1:y(threshold),x2:668,y2:y(threshold),stroke:"#b55b26","stroke-dasharray":"6 4"}));
  svg.append(svgElement("text",{x:52,y:y(threshold)-4,class:"maplabel"},`${threshold} m`));
  svg.append(svgElement("polyline",{points:points.map(p=>`${x(p.t)},${y(p.p)}`).join(" "),fill:"none",stroke:"#14776b","stroke-width":2}));
  points.forEach(p=>{const c=svgElement("circle",{cx:x(p.t),cy:y(p.p),r:3,fill:"#14776b"});c.append(svgElement("title",{},`${p.t} s · ${number(p.p)} m`));svg.append(c);});
  svg.append(svgElement("text",{x:5,y:24,class:"maplabel"},number(high,1)),svgElement("text",{x:5,y:146,class:"maplabel"},number(low,1)),svgElement("text",{x:48,y:171,class:"maplabel"},"0 s"),svgElement("text",{x:610,y:171,class:"maplabel"},end+" s"));box.append(svg);
}
document.querySelectorAll("[data-page]").forEach(b=>b.onclick=()=>showPage(b.dataset.page));
$("taskform").onsubmit=start;
$("networkselect").onchange=loadNetwork;
$("faultkind").onchange=()=>{controls();drawNetwork();};$("faultlink").onchange=drawNetwork;
$("timeindex").oninput=()=>{drawNetwork();renderDetail();};
$("history").onchange=()=>{if($("history").value)openRun($("history").value);};
$("refreshbutton").onclick=connect;
$("cancelbutton").onclick=async()=>{state.busy=true;controls();try{state.run=await api(`/api/runs/${state.run.run_id}/cancel`,{method:"POST"});await refreshRun(state.epoch);}catch(e){error(e.message);}finally{state.busy=false;controls();}};
$("proofbutton").onclick=function(){$("proofdrawer").hidden=!$("proofdrawer").hidden;this.textContent=$("proofdrawer").hidden?"展开记录字段":"收起记录字段";this.setAttribute("aria-expanded",!$("proofdrawer").hidden);};
$("boundarybutton").onclick=function(){$("boundaryexample").classList.toggle("visible");this.setAttribute("aria-expanded",$("boundaryexample").classList.contains("visible"));};
connect().then(()=>{const saved=localStorage.getItem("waterguard-run");if(saved && state.details)openRun(saved);});
