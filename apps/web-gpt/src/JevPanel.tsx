import { useEffect, useState } from 'react';
import Icon from './Icon';
import './jev.css';

type Decision = { id: string; sequence: number; model: string; latency_ms: number;
  answers: { next: { choice: string; confidence: number; probabilities: Record<string, number> }; risk: { noul: number } } };
type Snapshot = { configured: boolean; decisions?: Decision[] };
const label = (id: string) => id === 'finish' ? '汇总结果' : id === 'web-candidate-0' ? '基线方案' : id.replace('web-candidate-', '候选 ');

export default function JevPanel({ runId }: { runId?: string }) {
  const [data, setData] = useState<Snapshot | null>(null);
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let stream: EventSource | null = null;
    const read = async () => {
      try {
        const response = await fetch(`/wg/api/${runId ? `runs/${encodeURIComponent(runId)}/jev` : 'jev/status'}`, { signal: controller.signal });
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail?.message || 'Jev 连接失败');
        if (!controller.signal.aborted) { setData(value); setError(''); }
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Jev 连接失败'); }
    };
    setData(null); void read();
    if (runId) {
      stream = new EventSource(`/wg/api/runs/${encodeURIComponent(runId)}/stream`);
      stream.addEventListener('info', (event) => { try { if (JSON.parse((event as MessageEvent).data).event_type === 'jev.completed') void read(); } catch { /* Main stream reports malformed events. */ } });
      stream.addEventListener('stream.complete', () => { void read(); stream?.close(); });
    }
    return () => { controller.abort(); stream?.close(); };
  }, [runId, revision]);
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const response = await fetch('/wg/api/jev/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ apiKey: key }) });
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail?.message || 'Jev 连接失败');
      setKey(''); setOpen(false); setRevision((n) => n + 1);
    } catch (e) { setError(e instanceof Error ? e.message : 'Jev 连接失败'); }
    finally { setBusy(false); }
  };
  const latest = data?.decisions?.at(-1);
  return <section className="jev-panel" aria-label="Jev 决策中枢">
    <header><div className="jev-emblem"><Icon name="agent" size={22} /></div><div><small>AGNES · CEREBELLUM</small><h3>Jev 决策中枢</h3></div><span className={`jev-status ${data?.configured ? 'ready' : ''}`}>{!data ? '正在连接' : data.configured ? '已连接' : '待连接'}</span><button className="text-button" onClick={() => setOpen(!open)}>{open ? '收起' : '配置 Jev'}</button></header>
    {latest ? <div className="jev-decision"><div className="jev-summary"><div><small>下一步建议</small><strong>{label(latest.answers.next.choice)}</strong></div><div><small>决策置信度</small><strong>{(latest.answers.next.confidence * 100).toFixed(1)}%</strong></div><div><small>未解决风险</small><strong>{(latest.answers.risk.noul * 100).toFixed(1)}%</strong></div><div><small>响应耗时</small><strong>{latest.latency_ms} ms</strong></div></div><div className="jev-probabilities">{Object.entries(latest.answers.next.probabilities).sort((a,b) => b[1]-a[1]).map(([id, probability]) => <div key={id}><span>{label(id)}</span><progress value={probability} max={1} /><b>{(probability * 100).toFixed(1)}%</b></div>)}</div><footer>{latest.model} · 第 {latest.sequence} 次决策 · Agnes 继续执行</footer></div> : <p className="jev-empty">{data?.configured ? '等待 Agnes 发起决策，候选路由与风险将随执行更新。' : '连接 TypeSafe，启用候选路由、决策置信度与风险判断。'}</p>}
    {open && <form className="jev-config" onSubmit={(event) => void save(event)}><label>TypeSafe API Key<input type="password" autoComplete="off" required disabled={busy} value={key} onChange={(event) => setKey(event.target.value)} /></label><button className="button primary" disabled={busy}>{busy ? '正在验证连接…' : '连接并保存'}</button></form>}
    {error && <p role="alert" className="operation-error">{error}</p>}
  </section>;
}
