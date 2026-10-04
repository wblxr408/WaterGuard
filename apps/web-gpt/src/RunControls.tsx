import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import type { RunRecord } from "@waterguard/contracts";
import Icon from "./Icon";
import "./run-controls.css";

async function api<T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> {
  const response = await fetch(`/wg${path}`, {
    signal, ...(body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("无法连接任务服务，请重新连接后重试。");
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail?.message || (Array.isArray(value.detail) ? "请检查输入的参数。" : `请求失败（${response.status}）`));
  return value as T;
}
const errorText = (error: unknown) => error instanceof Error ? error.message : "请求失败，请重试。";

function OperationDialog({ title, busy, close, children }: { title: string; busy: boolean; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current!; dialog.showModal(); return () => dialog.close(); }, []);
  return <dialog ref={ref} className="json-dialog operation-dialog" aria-labelledby="operation-title"
    onCancel={(event) => { event.preventDefault(); if (!busy) close(); }}>
    <header><h2 id="operation-title">{title}</h2><button className="icon-button" aria-label="关闭操作" disabled={busy} onClick={close}><Icon name="close" /></button></header>
    {children}
  </dialog>;
}

type Fault = "normal" | "closure" | "leak" | "rupture" | "isolation";
function CreateRun({ close, created }: { close: () => void; created: (run: RunRecord) => void }) {
  const [network, setNetwork] = useState("epa-net1");
  const [networks, setNetworks] = useState<string[]>([]);
  const [links, setLinks] = useState<string[]>([]);
  const [link, setLink] = useState("");
  const [fault, setFault] = useState<Fault>("closure");
  const [pressure, setPressure] = useState(20);
  const [ratio, setRatio] = useState(95);
  const [limit, setLimit] = useState(3);
  const [mode, setMode] = useState("benchmark");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const frozen = useRef<{ signature: string; taskId: string; key: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api<{ network_id: string }[]>("/api/networks", controller.signal).then((rows) => setNetworks(rows.map((row) => row.network_id)))
      .catch((error) => { if (!controller.signal.aborted) setError(errorText(error)); });
    return () => { controller.abort(); request.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setLinks([]); setLink(""); setError("");
    api<{ fault_link_ids: string[] }>(`/api/networks/${encodeURIComponent(network)}`, controller.signal).then((details) => {
      setLinks(details.fault_link_ids); setLink(details.fault_link_ids[0] ?? ""); setLoading(false);
    }).catch((error) => { if (!controller.signal.aborted) { setError(errorText(error)); setLoading(false); } });
    return () => controller.abort();
  }, [network]);
  const requiresLink = fault !== "normal" && fault !== "isolation";
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (busy || loading) return;
    setBusy(true); setError(""); const controller = request.current = new AbortController();
    try {
      const proposal = { network_id: network, fault_kind: fault, ...(requiresLink ? { link_id: link } : {}), min_pressure_m: pressure, min_supply_ratio: ratio / 100, candidate_limit: limit };
      const signature = JSON.stringify(proposal);
      if (frozen.current?.signature !== signature) {
        const task = await api<{ task_id: string }>("/api/tasks/from-proposal", controller.signal, proposal);
        frozen.current = { signature, taskId: task.task_id, key: crypto.randomUUID() };
      }
      const run = await api<RunRecord>(`/api/tasks/${encodeURIComponent(frozen.current!.taskId)}/runs`, controller.signal, { mode, idempotency_key: frozen.current!.key });
      created(run);
    } catch (error) { if (!controller.signal.aborted) setError(errorText(error)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <OperationDialog title="新建任务" busy={busy} close={close}><form onSubmit={submit}>
    <fieldset disabled={busy} className="operation-fields">
      <label>公开管网<select value={network} onChange={(event) => setNetwork(event.target.value)}>{networks.length ? networks.map((id) => <option key={id}>{id}</option>) : <option>{network}</option>}</select></label>
      <label>故障类型<select value={fault} onChange={(event) => setFault(event.target.value as Fault)}>
        <option value="normal">正常工况</option><option value="closure">管道关闭</option><option value="leak">管道泄漏</option><option value="rupture">管道断裂</option><option value="isolation">区域隔离</option>
      </select></label>
      <label>故障管段<select value={link} disabled={!requiresLink || loading} onChange={(event) => setLink(event.target.value)}>{links.map((id) => <option key={id}>{id}</option>)}</select></label>
      <label>用户节点最低压力（m）<input type="number" min="0" max="100" step="any" required value={pressure} onChange={(event) => setPressure(event.target.valueAsNumber)} /></label>
      <label>用户节点最低供需比（%）<input type="number" min="0" max="100" step="any" required value={ratio} onChange={(event) => setRatio(event.target.valueAsNumber)} /></label>
      <label>最多仿真次数（含基线）<input type="number" min="1" max="6" step="1" required value={limit} onChange={(event) => setLimit(event.target.valueAsNumber)} /></label>
      <label className="operation-wide">运行模式<select value={mode} onChange={(event) => setMode(event.target.value)}><option value="benchmark">Benchmark · 固定脚本 / 真实水力计算</option><option value="live">Live · AGH 模型与工具调用</option></select></label>
    </fieldset>
    <div className="operation-message"><p>仿真 0–3 小时，故障 1–2 小时，评估 1–3 小时。任务与阈值由服务端冻结。</p>{mode === "live" && <p>使用“模型账号”中已验证的默认模型。</p>}{fault === "isolation" && <p>区域隔离采用管网已定义的边界管段。</p>}{loading && <p role="status">正在读取管网…</p>}{error && <p className="operation-error" role="alert">{error}</p>}</div>
    <footer><button type="button" className="button secondary" disabled={busy} onClick={close}>关闭</button><button className="button primary" disabled={busy || loading || (requiresLink && !link)}>{busy ? "正在创建运行…" : "创建任务并运行"}</button></footer>
  </form></OperationDialog>;
}

type Provider = { id: string; label: string; base_urls: string[] };
type ModelStatus = { available: boolean; message?: string; providers?: Provider[]; snapshot?: { configured: boolean; provider?: { id: string; model: string; credentialConfigured: boolean } | null } };
type TestResult = { verified: boolean; models: { id: string }[] };
function ModelAccount({ close }: { close: () => void }) {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [provider, setProvider] = useState("agnes-ai");
  const [base, setBase] = useState("");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = request.current = new AbortController();
    api<ModelStatus>("/api/agh/status", controller.signal).then((value) => {
      setStatus(value); setBase(value.providers?.find((row) => row.id === "agnes-ai")?.base_urls[0] ?? "");
    }).catch((error) => { if (!controller.signal.aborted) setError(errorText(error)); });
    return () => request.current?.abort();
  }, []);
  const invalidate = () => { setVerified(false); setModels([]); setModel(""); setMessage(""); setError(""); };
  const configure = async (action: "test" | "save") => {
    if (busy) return;
    setBusy(true); setError(""); setMessage(""); const controller = request.current = new AbortController();
    try {
      const config = { providerId: provider, baseUrl: base, ...(key.trim() ? { apiKey: key.trim() } : {}), ...(model ? { model } : {}) };
      if (action === "test") {
        const result = await api<TestResult>("/api/agh/models/test", controller.signal, config);
        setVerified(result.verified); setModels(result.verified ? result.models.map((row) => row.id) : []);
        setModel(result.verified ? result.models[0]?.id ?? "" : "");
        setMessage(result.verified ? "连接已验证，请选择模型并保存。" : "账号未通过验证，请检查接口和凭据。");
      } else {
        const result = await api<{ snapshot: ModelStatus["snapshot"] }>("/api/agh/models/save", controller.signal, config);
        setStatus((value) => ({ ...value!, snapshot: result.snapshot })); setKey(""); setVerified(false); setMessage("默认模型已保存，可新建 Live 任务。");
      }
    } catch (error) { if (!controller.signal.aborted) { setVerified(false); setError(errorText(error)); } }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const active = status?.snapshot?.provider;
  const selected = status?.providers?.find((row) => row.id === provider);
  return <OperationDialog title="AGH 模型账号" busy={busy} close={close}><form onSubmit={(event) => { event.preventDefault(); if (verified && model) void configure("save"); }}>
    <div className="operation-message" role="status"><p>{!status ? "正在读取 AGH 配置…" : !status.available ? status.message || "AGH 未连接，请启动项目 AGH 服务。" : active?.credentialConfigured ? `当前模型：${active.id} / ${active.model}` : "AGH 已连接，尚未配置可用模型账号。"}</p></div>
    <fieldset className="operation-fields" disabled={busy || !status?.available}>
      <label>提供商<select value={provider} onChange={(event) => { const id = event.target.value; setProvider(id); setBase(status?.providers?.find((row) => row.id === id)?.base_urls[0] ?? ""); setKey(""); invalidate(); }}>{status?.providers?.map((row) => <option value={row.id} key={row.id}>{row.label}</option>)}</select></label>
      <label>API 地址<select value={base} onChange={(event) => { setBase(event.target.value); invalidate(); }}>{selected?.base_urls.map((url) => <option key={url}>{url}</option>)}</select></label>
      <label className="operation-wide">API Key<input type="password" autoComplete="off" placeholder="在本机输入；已保存账号可留空" value={key} onChange={(event) => { setKey(event.target.value); invalidate(); }} /></label>
      <label className="operation-wide">模型<select value={model} disabled={!verified} onChange={(event) => setModel(event.target.value)}><option value="">{verified ? "请选择模型" : "测试连接后读取模型目录"}</option>{models.map((id) => <option key={id}>{id}</option>)}</select></label>
    </fieldset>
    <div className="operation-message"><p>凭据通过 AGH 配置服务保存，仅用于新任务的默认模型。</p>{message && <p role="status">{message}</p>}{error && <p className="operation-error" role="alert">{error}</p>}</div>
    <footer><button className="button secondary" type="button" disabled={busy || !status?.available || !base} onClick={() => void configure("test")}>{busy ? "正在处理…" : "测试连接"}</button><button className="button primary" disabled={busy || !verified || !model}>保存默认模型</button></footer>
  </form></OperationDialog>;
}

export default function RunControls({ run, created, updated }: { run: RunRecord | null; created: (run: RunRecord) => void; updated: () => void }) {
  const [dialog, setDialog] = useState<"task" | "model" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const canCancel = !!run && ["QUEUED", "RUNNING", "NEEDS_INPUT"].includes(run.status);
  const cancel = async () => {
    if (!run || busy || !canCancel) return;
    const controller = request.current = new AbortController(); setBusy(true); setError("");
    try { await api<RunRecord>(`/api/runs/${encodeURIComponent(run.run_id)}/cancel`, controller.signal, {}); updated(); }
    catch (error) { if (!controller.signal.aborted) setError(errorText(error)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <div className="run-controls">
    <button className="button primary" onClick={() => setDialog("task")}>新建任务</button>
    <button className="button secondary" onClick={() => setDialog("model")}>模型账号</button>
    {canCancel && <button className="button secondary" disabled={busy} onClick={() => void cancel()}>{busy ? "正在取消…" : "取消当前运行"}</button>}
    {error && <p className="operation-error" role="alert">{error}</p>}
    {dialog === "task" && <CreateRun close={() => setDialog(null)} created={(value) => { setDialog(null); created(value); }} />}
    {dialog === "model" && <ModelAccount close={() => setDialog(null)} />}
  </div>;
}
