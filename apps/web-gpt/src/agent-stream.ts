import { useEffect, useState } from "react";
import type { ActionPlan, RunEvent, RunRecord, SimulationResult, VerificationReport } from "@waterguard/contracts";
import { describePlan, eventText, loadWorkspace } from "./data";

export type StreamSource = "runtime" | "fixture";
export type ConnectionState = "loading" | "streaming" | "reconnecting" | "done" | "error" | "empty";
export interface AgentCandidate {
  id: string;
  name: string;
  plan: ActionPlan;
  verification: VerificationReport | null;
  result: Pick<SimulationResult, "artifact" | "diagnostics"> | null;
  series: SimulationResult["series"];
}
export interface AgentWorkspace {
  base: string;
  mode: RunRecord["mode"];
  run: RunRecord;
  candidates: AgentCandidate[];
}
export interface InfoLog {
  id: number;
  run_id: string;
  occurred_at: string;
  level: "INFO" | "WARN" | "ERROR";
  origin: string;
  message: string;
  tool_name?: string | null;
  event_type?: string;
  event_sequence?: number;
  candidate_id?: string | null;
  simulation_id?: string | null;
  native_tool_call_ref?: string | null;
  payload_ref?: string;
}
const terminal = new Set(["COMPLETED", "ERROR", "CANCELLED", "BUDGET_EXHAUSTED"]);
const phases = new Set(["INTAKE", "MODELING", "BASELINE", "PLANNING", "SIMULATING", "VERIFYING", "REVIEWING", "PACKAGING"]);
const modes = new Set(["live", "benchmark", "fixture", "replay"]);
export const isTerminal = (run: RunRecord) => terminal.has(run.status);
const message = (error: unknown) => error instanceof Error ? error.message : "运行数据读取失败";
class StreamDataError extends Error {}

async function read<T>(base: string, path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${base}/api/${path}`, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`运行服务返回 ${response.status}（${path}）`);
  return response.json();
}
function validateRun(value: RunRecord, runId: string, previous?: RunRecord) {
  if (value.run_id !== runId || !modes.has(value.mode) || !phases.has(value.phase) ||
      !["QUEUED", "RUNNING", "NEEDS_INPUT", ...terminal].includes(value.status) ||
      !value.task_ref?.constraint_hash || !value.budget_usage ||
      (previous && (value.mode !== previous.mode || value.task_ref.constraint_hash !== previous.task_ref.constraint_hash ||
        value.task_ref.revision !== previous.task_ref.revision || value.task_ref.task_id !== previous.task_ref.task_id)))
    throw new StreamDataError("运行来源或冻结任务不一致，已停止订阅。");
}
export function validateEvent(event: RunEvent, run: RunRecord) {
  if (event.run_id !== run.run_id || event.mode !== run.mode || event.task_revision !== run.task_ref.revision ||
      event.constraint_hash !== run.task_ref.constraint_hash || !Number.isSafeInteger(event.sequence) || event.sequence < 1 ||
      !Object.hasOwn(eventText, event.type) || !["agh", "gateway", "worker", "verifier", "user"].includes(event.origin))
    throw new StreamDataError("SSE 事件与所选运行不一致，已停止订阅。");
}
async function loadRun(base: string, runId: string, signal: AbortSignal): Promise<AgentWorkspace> {
  const run = await read<RunRecord>(base, `runs/${encodeURIComponent(runId)}`, signal);
  validateRun(run, runId);
  const results = await read<{
    plan: ActionPlan; artifact: SimulationResult["artifact"] | null;
    diagnostics: SimulationResult["diagnostics"] | null; verification: VerificationReport | null;
  }[]>(base, `runs/${encodeURIComponent(runId)}/results`, signal);
  const candidates = results.map(({ plan, artifact, diagnostics, verification }): AgentCandidate => {
    if (plan.task_ref.constraint_hash !== run.task_ref.constraint_hash || plan.task_ref.revision !== run.task_ref.revision ||
        plan.task_ref.task_id !== run.task_ref.task_id || (verification &&
          (verification.candidate_id !== plan.candidate_id || verification.task_ref.constraint_hash !== run.task_ref.constraint_hash ||
            verification.task_ref.task_id !== run.task_ref.task_id || verification.task_ref.revision !== run.task_ref.revision ||
            (artifact && verification.simulation_id !== artifact.simulation_id))))
      throw new StreamDataError("候选与冻结运行不一致，已停止展示。");
    if (artifact && (artifact.candidate_id !== plan.candidate_id || artifact.task_ref.constraint_hash !== run.task_ref.constraint_hash ||
        artifact.task_ref.task_id !== run.task_ref.task_id || artifact.task_ref.revision !== run.task_ref.revision ||
        (diagnostics && diagnostics.simulation_id !== artifact.simulation_id)))
      throw new StreamDataError("仿真与冻结运行不一致，已停止展示。");
    return { id: plan.candidate_id, name: describePlan(plan), plan, verification,
      result: artifact && diagnostics ? { artifact, diagnostics } : null, series: null };
  });
  return { base, mode: run.mode, run, candidates };
}

/** One subscription feeds both workflow and logs. EventSource retains Last-Event-ID across automatic retries. */
export function useAgentRun() {
  const [source, setSource] = useState<StreamSource>("runtime");
  const [runId, setRunId] = useState(() => new URLSearchParams(window.location.search).get("run") || "");
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [workspace, setWorkspace] = useState<AgentWorkspace | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [logs, setLogs] = useState<InfoLog[]>([]);
  const [state, setState] = useState<ConnectionState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [connectKey, setConnectKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    if (source === "fixture") { setRuns([]); setRunId("run-day1-fixture"); return () => controller.abort(); }
    setError(null);
    read<RunRecord[]>("/wg", "runs", controller.signal).then((rows) => {
      rows.forEach((row) => validateRun(row, row.run_id));
      if (controller.signal.aborted) return;
      setRuns(rows);
      setRunId((current) => current || rows.find((row) => !isTerminal(row))?.run_id || rows[0]?.run_id || "");
      if (!rows.length && !runId) setState("empty");
    }).catch((err) => {
      if (!controller.signal.aborted) { setError(message(err)); setState("error"); }
    });
    return () => controller.abort();
  }, [source, refreshKey]);

  useEffect(() => {
    setWorkspace(null); setEvents([]); setLogs([]);
    if (!runId) return;
    const controller = new AbortController();
    let stream: EventSource | undefined;
    let snapshot: RunRecord | undefined;
    let refreshTimer = 0;
    let refreshInFlight = false;
    let refreshAgain = false;
    const base = source === "runtime" ? "/wg" : "/fx";
    setState("loading"); setError(null);
    const hydrate = async () => {
      if (refreshInFlight) { refreshAgain = true; return; }
      refreshInFlight = true;
      try {
        const value = source === "fixture"
          ? await loadWorkspace("fixture", "", controller.signal)
          : await loadRun(base, runId, controller.signal);
        if (!value.run || controller.signal.aborted) return;
        validateRun(value.run, runId, snapshot);
        snapshot ??= value.run;
        setWorkspace({ base, mode: value.mode, run: snapshot, candidates: value.candidates });
      } catch (err) {
        if (!controller.signal.aborted) {
          if (err instanceof StreamDataError) { setWorkspace(null); fail(err); }
          else { setError(message(err)); if (!snapshot) setState("error"); }
        }
      } finally {
        refreshInFlight = false;
        if (refreshAgain && !controller.signal.aborted) { refreshAgain = false; void hydrate(); }
      }
    };
    const scheduleRefresh = () => { clearTimeout(refreshTimer); refreshTimer = window.setTimeout(() => void hydrate(), 120); };
    const fail = (err: unknown) => { controller.abort(); stream?.close(); clearTimeout(refreshTimer); setState("error"); setError(message(err)); };
    const appendLog = (log: InfoLog) => setLogs((rows) => rows.some((row) => row.id === log.id) ? rows : [...rows, log].slice(-1000));
    const acceptRun = (value: RunRecord) => {
      validateRun(value, runId, snapshot); snapshot = value;
      setWorkspace((value) => value ? { ...value, run: snapshot! } : value);
      setRuns((rows) => rows.map((row) => row.run_id === runId ? value : row));
    };
    void hydrate().then(() => {
      if (!snapshot || controller.signal.aborted) return;
      stream = new EventSource(`${base}/api/runs/${encodeURIComponent(runId)}/${source === "runtime" ? "stream" : "events"}`);
      stream.onopen = () => { setState("streaming"); setError(null); };
      stream.onerror = () => { setState("reconnecting"); setError("连接中断，正在自动续读；已收到的记录保留。"); };
      const onWorkflow = (event: MessageEvent) => {
        try {
          const value = JSON.parse(event.data) as RunEvent;
          validateEvent(value, snapshot!);
          setEvents((rows) => rows.some((row) => row.sequence === value.sequence) ? rows : [...rows, value].sort((a, b) => a.sequence - b.sequence));
          if (source === "fixture") {
            appendLog({ ...value, id: value.sequence, level: "INFO", message: eventText[value.type], event_sequence: value.sequence, event_type: value.type });
            if (value.type === "run.completed") { stream?.close(); setState("done"); }
          }
          scheduleRefresh();
        } catch (err) { fail(err); }
      };
      if (source === "fixture") Object.keys(eventText).forEach((name) => stream!.addEventListener(name, onWorkflow as EventListener));
      else {
        stream.addEventListener("workflow", onWorkflow as EventListener);
        stream.addEventListener("run", (event) => {
          try { acceptRun(JSON.parse((event as MessageEvent).data)); } catch (err) { fail(err); }
        });
        stream.addEventListener("info", (event) => {
          try {
            const e = event as MessageEvent;
            const value = JSON.parse(e.data) as InfoLog;
            if (value.run_id !== runId || !["INFO", "WARN", "ERROR"].includes(value.level) || typeof value.message !== "string" ||
                !Number.isSafeInteger(Number(e.lastEventId)) || Number(e.lastEventId) < 1)
              throw new Error("SSE 日志来源或格式不一致，已停止订阅。");
            appendLog({ ...value, id: Number(e.lastEventId) });
          } catch (err) { fail(err); }
        });
        stream.addEventListener("stream.complete", (event) => {
          try {
            acceptRun(JSON.parse((event as MessageEvent).data));
            stream?.close(); setState("done"); setError(null); scheduleRefresh();
          } catch (err) { fail(err); }
        });
      }
    });
    return () => { controller.abort(); stream?.close(); clearTimeout(refreshTimer); };
  }, [source, runId, connectKey]);

  return { w: workspace, events, logs, state, error, source, runs, runId,
    selectSource: (next: StreamSource) => { if (next !== source) { setRunId(""); setSource(next); setState("loading"); } },
    selectRun: (next: string) => {
      setRunId(next);
      const url = new URL(window.location.href); url.searchParams.set("run", next);
      window.history.replaceState(null, "", url);
    }, refreshRuns: () => setRefreshKey((key) => key + 1),
    reconnect: () => { setRefreshKey((key) => key + 1); setConnectKey((key) => key + 1); } };
}
