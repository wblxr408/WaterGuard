import { useEffect, useMemo, useRef, useState } from "react";
import type { InfoLog } from "./agent-stream";
import { download } from "./data";
import Icon from "./Icon";

const origins: Record<string, string> = { agh: "AGH", gateway: "网关", worker: "计算进程", verifier: "验证器", user: "运行服务" };
export default function AgentLogs({ logs, runId, onInspect, onEvent }: {
  logs: InfoLog[]; runId: string; onInspect: (title: string, data: unknown) => void;
  onEvent: (sequence: number) => void;
}) {
  const [level, setLevel] = useState("all");
  const [origin, setOrigin] = useState("all");
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const scroll = useRef<HTMLDivElement>(null);
  const visible = useMemo(() => logs.filter((log) => (level === "all" || log.level === level) &&
    (origin === "all" || log.origin === origin) &&
    `${log.message} ${log.tool_name ?? ""} ${log.candidate_id ?? ""} ${log.native_tool_call_ref ?? ""} ${log.event_type ?? ""}`
      .toLowerCase().includes(query.trim().toLowerCase())), [logs, level, origin, query]);
  useEffect(() => {
    if (follow && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [visible, follow]);
  useEffect(() => { setQuery(""); setFollow(true); }, [runId]);
  return <section className="agent-log" aria-label="Info log 日志">
    <header>
      <div><Icon name="file" size={18} /><h2>Info log</h2><span>运行事件与工具调用 · {visible.length} / {logs.length} 条</span></div>
      <button className="button secondary" disabled={!visible.length} onClick={() => download(`${runId}-logs.json`, visible)}>
        <Icon name="download" size={14} />导出日志
      </button>
    </header>
    <div className="agent-log-tools">
      <label>级别<select aria-label="日志级别" value={level} onChange={(e) => setLevel(e.target.value)}>
        <option value="all">全部级别</option><option>INFO</option><option>WARN</option><option>ERROR</option>
      </select></label>
      <label>来源<select aria-label="日志来源" value={origin} onChange={(e) => setOrigin(e.target.value)}>
        <option value="all">全部来源</option>{Object.entries(origins).map(([id, text]) => <option key={id} value={id}>{text}</option>)}
      </select></label>
      <input aria-label="搜索日志" placeholder="搜索工具、候选或调用号" value={query} onChange={(e) => setQuery(e.target.value)} />
      <label className="agent-log-follow"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />跟随最新</label>
    </div>
    <div className="agent-log-scroll" ref={scroll} tabIndex={0} aria-label="运行日志列表">
      {!visible.length ? <p className="agent-log-empty">{logs.length ? "没有匹配的日志。" : "等待服务端推送日志…"}</p> :
        <ol>{visible.map((log) => <li key={log.id} className={`level-${log.level.toLowerCase()}`}>
          <time dateTime={log.occurred_at}>{new Date(log.occurred_at).toLocaleTimeString("zh-CN", { hour12: false })}</time>
          <span className="log-level">{log.level}</span><span className="log-origin">{origins[log.origin] ?? log.origin}</span>
          <div className="log-message"><span>{log.message}</span>
            {(log.candidate_id || log.native_tool_call_ref) && <small>{log.candidate_id}{log.candidate_id && log.native_tool_call_ref ? " · " : ""}{log.native_tool_call_ref}</small>}
          </div>
          <button title="查看日志原文" aria-label={`查看日志 ${log.id} 原文`} onClick={() => onInspect(`日志 ${log.id}`, log)}><Icon name="file" size={14} /></button>
          {log.event_sequence != null && <button title="定位工作流事件" aria-label={`定位事件 ${log.event_sequence}`} onClick={() => onEvent(log.event_sequence!)}><Icon name="target" size={14} /></button>}
        </li>)}</ol>}
    </div>
    <footer>运行事件与工具调用持续更新。</footer>
  </section>;
}
