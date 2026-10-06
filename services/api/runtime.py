"""SQLite metadata and immutable artifact/event storage for WaterGuard."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
import sys
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))

from waterguard_contracts.core import require, task_hash, task_ref, validate

RUN_STATUS = {"QUEUED", "RUNNING", "NEEDS_INPUT", "COMPLETED", "BUDGET_EXHAUSTED", "ERROR", "CANCELLED"}
RUN_PHASE = {"INTAKE", "MODELING", "BASELINE", "PLANNING", "SIMULATING", "VERIFYING", "REVIEWING", "PACKAGING"}
TERMINAL = {"COMPLETED", "BUDGET_EXHAUSTED", "ERROR", "CANCELLED"}
ALLOWED_TRANSITIONS = {
    "QUEUED": {"RUNNING", "CANCELLED", "ERROR"},
    "RUNNING": {"NEEDS_INPUT", "COMPLETED", "BUDGET_EXHAUSTED", "ERROR", "CANCELLED"},
    "NEEDS_INPUT": {"RUNNING", "CANCELLED", "ERROR"},
    "COMPLETED": set(), "BUDGET_EXHAUSTED": set(), "ERROR": set(), "CANCELLED": set(),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class ConflictError(ValueError):
    pass


class NotFoundError(ValueError):
    pass


class RunStore:
    """Small single-node store; SQLite transactions serialize metadata writes."""

    def __init__(self, database: Path, artifacts: Path):
        self.database, self.artifacts = Path(database), Path(artifacts)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._event_lock = threading.Lock()
        self._init()

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _db(self):
        """Commit (or roll back) and always close SQLite handles on Windows."""
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init(self):
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS live_links (parent_id TEXT, child_id TEXT PRIMARY KEY, reservation_id TEXT)')
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
              constraint_hash TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY (task_id, revision)
            );
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, revision INTEGER NOT NULL,
              status TEXT NOT NULL, phase TEXT NOT NULL, mode TEXT NOT NULL,
              payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY (task_id, revision) REFERENCES tasks(task_id, revision)
            );
            CREATE TABLE IF NOT EXISTS events (
              run_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY (run_id, sequence), FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS run_stream (
              run_id TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY (run_id, sequence), FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS idempotency (
              scope TEXT NOT NULL, key TEXT NOT NULL, response TEXT NOT NULL,
              PRIMARY KEY (scope, key)
            );
            CREATE TABLE IF NOT EXISTS simulation_reservations (
              reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, request_key TEXT NOT NULL,
              state TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id, request_key),
              FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS simulation_jobs (
              job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
              reservation_id TEXT, status TEXT NOT NULL, payload TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY (run_id) REFERENCES runs(run_id),
              FOREIGN KEY (reservation_id) REFERENCES simulation_reservations(reservation_id)
            );
            """)

    def _write_json(self, relative: str, value: Any) -> str:
        from services.hydraulics.storage import write
        target = (self.artifacts / relative).resolve()
        require(target.is_relative_to(self.artifacts.resolve()), "Artifact path escapes configured storage")
        write(target, value)
        return "artifact:" + relative.replace("\\", "/")

    def _run_directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"run-[A-Za-z0-9_-]+", run_id):
            raise ValueError("Invalid run identifier")
        root = self.artifacts.resolve()
        target = (root / run_id).resolve()
        require(target.is_relative_to(root), "Run artifact path escapes configured storage")
        return target

    @staticmethod
    def _read_json(path: Path) -> dict:
        from waterguard_contracts.core import load
        value = load(path)
        if not isinstance(value, dict):
            raise ValueError("Artifact JSON must contain an object")
        return value

    def _append_event(self, run_id: str, event: dict) -> None:
        path = self.artifacts / run_id / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def save_task(self, task: dict) -> dict:
        validate("TaskSpec", task)
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task["task_id"])), "Invalid task identifier")
        require(task_hash(task) == task["constraint_hash"], "Task hash mismatch")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT payload FROM tasks WHERE task_id=? AND revision=?", (task["task_id"], task["revision"])).fetchone()
            if existing:
                if existing["payload"] != canonical(task):
                    raise ConflictError("Task revision already exists with different content")
                return json.loads(existing["payload"])
            latest = db.execute("SELECT MAX(revision) AS revision FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone()["revision"]
            if latest is not None and task["revision"] != latest + 1:
                raise ConflictError("Task revisions must be consecutive")
            if latest is None and task["revision"] != 1:
                raise ConflictError("First task revision must be 1")
            db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)", (task["task_id"], task["revision"], canonical(task), task["constraint_hash"], now()))
        self._write_json(f"tasks/{task['task_id']}/revision-{task['revision']}.json", task)
        return task

    def get_task(self, task_id: str, revision: int | None = None) -> dict:
        with self._db() as db:
            if revision is None:
                row = db.execute("SELECT payload FROM tasks WHERE task_id=? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
            else:
                row = db.execute("SELECT payload FROM tasks WHERE task_id=? AND revision=?", (task_id, revision)).fetchone()
        if not row:
            raise NotFoundError("Unknown task revision")
        return json.loads(row["payload"])

    def recent_runs(self, limit: int = 20) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT payload FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def active_benchmarks(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT payload FROM runs WHERE mode IN ('benchmark','fixture','replay') AND status IN ('QUEUED','RUNNING')").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def active_lives(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT payload FROM runs WHERE mode='live' AND status IN ('QUEUED','RUNNING')").fetchall()
        return [json.loads(row['payload']) for row in rows]

    def create_run(self, task: dict, mode: str, idempotency_key: str | None = None, source_run_id: str | None = None) -> dict:
        if mode not in {"fixture", "benchmark", "replay", "live"}:
            raise ConflictError("Unsupported run mode")
        scope = f"run:{task['task_id']}:{task['revision']}"
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                found = db.execute("SELECT response FROM idempotency WHERE scope=? AND key=?", (scope, idempotency_key)).fetchone()
                if found:
                    saved = json.loads(found["response"])
                    run = saved.get("run", saved)
                    if run["mode"] != mode or saved.get("source_run_id") != source_run_id:
                        raise ConflictError("Idempotency key already used with different run parameters")
                    current = db.execute("SELECT payload FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()
                    return json.loads(current["payload"])
            run_id = "run-" + uuid.uuid4().hex
            run = dict(schema_version=task["schema_version"], run_id=run_id, task_ref=task_ref(task), mode=mode,
                       status="QUEUED", phase="INTAKE", agh_session_ref=None, model_config_ref=None,
                       budget_usage={"simulations": 0, "model_calls": None, "wall_time_s": 0.0},
                       conclusion="INCOMPLETE", selected_candidate_id=None,
                       missing_items=["native_agh_trace", "agh_host_not_configured", "required_stress_tests"])
            validate("RunRecord", run)
            timestamp = now()
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (run_id, task["task_id"], task["revision"], run["status"], run["phase"], mode, canonical(run), timestamp, timestamp))
            if idempotency_key:
                db.execute("INSERT INTO idempotency VALUES (?, ?, ?)", (scope, idempotency_key, canonical(dict(run=run, source_run_id=source_run_id))))
        if source_run_id is not None:
            self._write_json(f"{run_id}/replay-source.json", dict(source_run_id=source_run_id))
        ref = self._write_json(f"{run_id}/run.json", run)
        self.emit(run, "user", "run.started", ref)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise NotFoundError("Unknown run")
        return json.loads(row["payload"])

    def bind_agh(self, run_id: str, session_id: str, model: dict) -> dict:
        """Bind identity returned by the SDK, never a model-proposed session reference."""
        ref = self._write_json(f'{run_id}/agh/model.json', model)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            run = json.loads(db.execute('SELECT payload FROM runs WHERE run_id=?', (run_id,)).fetchone()['payload'])
            run['agh_session_ref'] = session_id
            run['model_config_ref'] = ref
            run['missing_items'] = [item for item in run['missing_items'] if item != 'agh_host_not_configured']
            validate('RunRecord', run)
            db.execute('UPDATE runs SET payload=?, updated_at=? WHERE run_id=?', (canonical(run), now(), run_id))
        self._write_json(f'{run_id}/run.json', run)
        return run

    def live_parent(self, session_id: str | None, task_id: str) -> dict | None:
        if not session_id:
            return None
        with self._db() as db:
            rows = db.execute("SELECT payload FROM runs WHERE mode='live' AND task_id=?", (task_id,)).fetchall()
        for row in rows:
            run = json.loads(row['payload'])
            if run['agh_session_ref'] == session_id:
                if run['status'] != 'RUNNING':
                    raise ConflictError('AGH run is no longer active')
                return run
        if any(json.loads(row['payload'])['status'] == 'RUNNING' for row in rows):
            raise ConflictError('AGH session does not match the active frozen task')
        return None

    def link_live_child(self, parent_id: str, child_id: str):
        with self._db() as db:
            db.execute('INSERT INTO live_links VALUES (?, ?, NULL)', (parent_id, child_id))

    def reserve_live_child(self, child_id: str):
        with self._db() as db:
            row = db.execute('SELECT * FROM live_links WHERE child_id=?', (child_id,)).fetchone()
        if row:
            reservation = self.reserve_simulation(row['parent_id'], child_id)
            with self._db() as db:
                db.execute('UPDATE live_links SET reservation_id=? WHERE child_id=?', (reservation['reservation_id'], child_id))

    def settle_live_child(self, child_id: str):
        with self._db() as db:
            row = db.execute('SELECT reservation_id FROM live_links WHERE child_id=?', (child_id,)).fetchone()
        if row and row['reservation_id']:
            self.settle_simulation(row['reservation_id'])

    def live_children(self, parent_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute('SELECT runs.payload FROM live_links JOIN runs ON runs.run_id=live_links.child_id WHERE parent_id=?', (parent_id,)).fetchall()
        return [json.loads(row['payload']) for row in rows]

    def bind_agh_session(self, run_id: str, agh_session_ref: str, model_config_ref: str) -> dict:
        """Record host-verified AGH provenance before a strict gateway starts.

        This is intentionally a store operation, not an MCP tool: only the
        local AGH host owns the native session identity and model reference.
        """
        require(isinstance(agh_session_ref, str) and agh_session_ref, "AGH session reference is required")
        require(isinstance(model_config_ref, str) and model_config_ref, "Model configuration reference is required")
        with self._db() as db:
            row = db.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise NotFoundError("Unknown run")
            run = json.loads(row["payload"])
            if run["status"] in TERMINAL:
                raise ConflictError("Cannot bind AGH session to a terminal run")
            if run["agh_session_ref"] not in {None, agh_session_ref}:
                raise ConflictError("Run is already bound to a different AGH session")
            run["agh_session_ref"] = agh_session_ref
            run["model_config_ref"] = model_config_ref
            run["missing_items"] = [item for item in run["missing_items"] if item not in {"agh_host_not_configured", "native_agh_trace"}]
            validate("RunRecord", run)
            db.execute("UPDATE runs SET payload=?, updated_at=? WHERE run_id=?", (canonical(run), now(), run_id))
        self._write_json(f"{run_id}/run.json", run)
        return run

    def transition(self, run_id: str, status: str, phase: str | None = None, *, wall_time_s: float | None = None, model_calls: int | None = None, progress_only: bool = False) -> dict:
        if status not in RUN_STATUS or (phase is not None and phase not in RUN_PHASE):
            raise ValueError("Invalid status or phase")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise NotFoundError("Unknown run")
            run = json.loads(row["payload"])
            if status != run["status"] and status not in ALLOWED_TRANSITIONS[run["status"]]:
                raise ConflictError(f"Invalid state transition {run['status']} -> {status}")
            run["status"] = status
            if phase is not None:
                run["phase"] = phase
            if wall_time_s is not None:
                run["budget_usage"]["wall_time_s"] = wall_time_s
            if model_calls is not None:
                run['budget_usage']['model_calls'] = model_calls
            validate("RunRecord", run)
            db.execute("UPDATE runs SET status=?, phase=?, payload=?, updated_at=? WHERE run_id=?", (run["status"], run["phase"], canonical(run), now(), run_id))
            if progress_only:
                self._backfill_stream(db, run_id)
                self._insert_stream(db, run_id, 'run', run)
        ref = self._write_json(f"{run_id}/run.json", run)
        if not progress_only:
            self.emit(run, "user", "run.completed" if status in TERMINAL else "run.started", ref)
        return run

    def emit(self, run: dict, origin: str, event_type: str, payload_ref: str, *, candidate_id: str | None = None, simulation_id: str | None = None, correlation_id: str | None = None, native_tool_call_ref: str | None = None) -> dict:
        with self._event_lock:
            return self._emit_serial(run, origin, event_type, payload_ref, candidate_id=candidate_id,
                                     simulation_id=simulation_id, correlation_id=correlation_id, native_tool_call_ref=native_tool_call_ref)

    def _emit_serial(self, run: dict, origin: str, event_type: str, payload_ref: str, *, candidate_id: str | None = None, simulation_id: str | None = None, correlation_id: str | None = None, native_tool_call_ref: str | None = None) -> dict:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._backfill_stream(db, run["run_id"])
            sequence = db.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM events WHERE run_id=?", (run["run_id"],)).fetchone()["sequence"]
            event = dict(schema_version=run["schema_version"], event_id="evt-" + uuid.uuid4().hex, sequence=sequence,
                         run_id=run["run_id"], task_revision=run["task_ref"]["revision"], constraint_hash=run["task_ref"]["constraint_hash"],
                         mode=run["mode"], agh_session_ref=run["agh_session_ref"], native_tool_call_ref=native_tool_call_ref,
                         native_reference_missing_reason="No native tool call associated with this application event" if native_tool_call_ref is None else None,
                         correlation_id=correlation_id or "corr-" + uuid.uuid4().hex, origin=origin, type=event_type,
                         candidate_id=candidate_id, simulation_id=simulation_id, occurred_at=now(), simulation_time_s=None,
                         payload_ref=payload_ref)
            validate("RunEvent", event)
            db.execute("INSERT INTO events VALUES (?, ?, ?)", (run["run_id"], sequence, canonical(event)))
            self._insert_stream(db, run["run_id"], "workflow", event)
            snapshot = json.loads(db.execute("SELECT payload FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()["payload"])
            self._insert_stream(db, run["run_id"], "info", self._event_log(event, snapshot))
            self._insert_stream(db, run["run_id"], "run", snapshot)
        self._append_event(run["run_id"], event)
        return event

    @staticmethod
    def _insert_stream(db, run_id: str, kind: str, payload: dict):
        sequence = db.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_stream WHERE run_id=?", (run_id,)).fetchone()[0]
        db.execute("INSERT INTO run_stream VALUES (?, ?, ?, ?)", (run_id, sequence, kind, canonical(payload)))
        return sequence

    @staticmethod
    def _event_log(event: dict, run: dict | None = None) -> dict:
        labels = {"run.started": "运行状态更新", "candidate.submitted": "已提交候选方案",
                  "simulation.completed": "仿真结果已返回", "verification.completed": "独立验证结果已返回",
                  "run.completed": "运行已结束"}
        message, level = labels.get(event["type"], event["type"]), "INFO"
        if run and event["type"] in {"run.started", "run.completed"}:
            message += f" · {run['status']} / {run['phase']}"
            level = "ERROR" if run['status'] == "ERROR" else "WARN" if run['status'] in {"CANCELLED", "BUDGET_EXHAUSTED"} else "INFO"
        return dict(run_id=event["run_id"], occurred_at=event["occurred_at"], level=level, origin=event["origin"],
                    message=message, event_type=event["type"],
                    event_sequence=event["sequence"], candidate_id=event["candidate_id"], simulation_id=event["simulation_id"],
                    native_tool_call_ref=event["native_tool_call_ref"], payload_ref=event["payload_ref"], tool_name=None)

    def _backfill_stream(self, db, run_id: str):
        # Migrate before the first new write, so resumed/recovered old runs retain their complete history.
        if not db.execute("SELECT 1 FROM run_stream WHERE run_id=? AND kind='workflow' LIMIT 1", (run_id,)).fetchone():
            completed = False
            for row in db.execute("SELECT payload FROM events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall():
                event = json.loads(row["payload"])
                self._insert_stream(db, run_id, "workflow", event)
                self._insert_stream(db, run_id, "info", self._event_log(event))
                completed = completed or event['type'] == 'run.completed'
            if completed:
                snapshot = json.loads(db.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()["payload"])
                self._insert_stream(db, run_id, "run", snapshot)

    def log(self, run_id: str, message: str, *, level: str = "INFO", origin: str = "agh",
            tool_name: str | None = None, event_type: str | None = None, native_tool_call_ref: str | None = None,
            execution: dict | None = None):
        """Persist operational summaries, never model text, credentials or tool arguments."""
        if level not in {"INFO", "WARN", "ERROR"}:
            raise ValueError("Unsupported log level")
        self.get_run(run_id)
        payload = dict(run_id=run_id, occurred_at=now(), level=level, origin=origin, message=message,
                       tool_name=tool_name, event_type=event_type, native_tool_call_ref=native_tool_call_ref)
        if execution is not None:
            payload['execution'] = execution
        with self._event_lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._backfill_stream(db, run_id)
            self._insert_stream(db, run_id, "info", payload)

    def stream_after(self, run_id: str, sequence: int = 0, limit: int = 200) -> list[dict]:
        self.get_run(run_id)
        with self._event_lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._backfill_stream(db, run_id)
            rows = db.execute("SELECT sequence, kind, payload FROM run_stream WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                              (run_id, sequence, limit)).fetchall()
        return [dict(sequence=row["sequence"], kind=row["kind"], data=json.loads(row["payload"])) for row in rows]

    def stream_finished(self, run_id: str, sequence: int) -> bool:
        # A terminal state may commit before its event; close only after its durable snapshot has been delivered.
        with self._db() as db:
            db.execute("BEGIN")
            last = db.execute("SELECT MAX(sequence) FROM run_stream WHERE run_id=?", (run_id,)).fetchone()[0]
            snapshot = db.execute("SELECT payload FROM run_stream WHERE run_id=? AND kind='run' ORDER BY sequence DESC LIMIT 1", (run_id,)).fetchone()
            if last is None or last > sequence or not snapshot:
                return False
            status = json.loads(snapshot['payload'])['status']
            if status not in TERMINAL:
                return False
            submitted = self._run_directory(run_id) / 'submitted-plan.json'
            if status == 'COMPLETED' and submitted.is_file():
                # Submitted plans are simulated and verified by separate tool calls. A result file
                # alone is insufficient: its verification event must commit and reach this cursor.
                candidate_id = self._read_json(submitted)['candidate_id']
                events = db.execute("SELECT payload FROM events WHERE run_id=?", (run_id,)).fetchall()
                return any(event['type'] == 'verification.completed' and event.get('candidate_id') == candidate_id
                           for event in (json.loads(row['payload']) for row in events))
            return True

    def events_after(self, run_id: str, sequence: int = 0) -> list[dict]:
        self.get_run(run_id)
        with self._db() as db:
            rows = db.execute("SELECT payload FROM events WHERE run_id=? AND sequence>? ORDER BY sequence", (run_id, sequence)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def list_candidates(self, run_id: str) -> list[dict]:
        self.get_run(run_id)
        directory = self._run_directory(run_id) / "candidates"
        if not directory.is_dir():
            return []
        candidates = []
        for path in sorted(directory.glob("*/plan.json")):
            plan = self._read_json(path)
            validate("ActionPlan", plan)
            candidates.append(plan)
        return candidates

    def get_evidence(self, run_id: str) -> dict:
        self.get_run(run_id)
        path = self._run_directory(run_id) / "evidence-manifest.json"
        if not path.is_file():
            raise NotFoundError("Evidence package has not been built")
        evidence = self._read_json(path)
        validate("EvidenceManifest", evidence)
        if evidence["run_id"] != run_id:
            raise ValueError("Evidence package run binding mismatch")
        return evidence

    def get_series(self, simulation_id: str, node_id: str | None = None, start_s: int | None = None, end_s: int | None = None) -> dict:
        if not re.fullmatch(r"(?:sim|simulation)-[A-Za-z0-9_-]+", simulation_id):
            raise ValueError("Invalid simulation identifier")
        if start_s is not None and end_s is not None and start_s >= end_s:
            raise ValueError("Invalid series range")
        matches = list(self.artifacts.glob(f"run-*/simulations/{simulation_id}/series.json"))
        if len(matches) != 1:
            raise NotFoundError("Simulation series is unavailable")
        series = self._read_json(matches[0])
        if series.get("simulation_id") != simulation_id:
            raise ValueError("Series simulation binding mismatch")
        frames = series.get("frames", [])
        if node_id is not None:
            known = {sample["node_id"] for frame in frames for sample in frame.get("nodes", [])}
            if node_id not in known:
                raise NotFoundError("Unknown node in simulation series")
        filtered = []
        for frame in frames:
            if (start_s is not None and frame["time_s"] < start_s) or (end_s is not None and frame["time_s"] >= end_s):
                continue
            item = dict(frame)
            if node_id is not None:
                item["nodes"] = [node for node in frame["nodes"] if node["node_id"] == node_id]
            filtered.append(item)
        series["frames"] = filtered
        return series

    def reserve_simulation(self, run_id: str, request_key: str) -> dict:
        """Atomically reserve a simulation attempt before a worker is started."""
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_key):
            raise ValueError("Invalid simulation idempotency key")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            run_row = db.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run_row:
                raise NotFoundError("Unknown run")
            run = json.loads(run_row["payload"])
            if run["status"] in TERMINAL:
                raise ConflictError("Cannot reserve a simulation for a terminal run")
            existing = db.execute("SELECT reservation_id, state FROM simulation_reservations WHERE run_id=? AND request_key=?", (run_id, request_key)).fetchone()
            if existing:
                return {"reservation_id": existing["reservation_id"], "state": existing["state"], "idempotent": True}
            task_row = db.execute("SELECT payload FROM tasks WHERE task_id=? AND revision=?", (run["task_ref"]["task_id"], run["task_ref"]["revision"])).fetchone()
            task = json.loads(task_row["payload"])
            used = db.execute("SELECT COUNT(*) AS count FROM simulation_reservations WHERE run_id=? AND state IN ('RESERVED','SPENT')", (run_id,)).fetchone()["count"]
            active = db.execute("SELECT COUNT(*) AS count FROM simulation_reservations WHERE run_id=? AND state='RESERVED'", (run_id,)).fetchone()["count"]
            if used >= task["budget"]["max_simulations"]:
                raise ConflictError("BUDGET_EXHAUSTED: simulation budget reached")
            if active >= task["budget"]["max_parallel"]:
                raise ConflictError("BUDGET_EXHAUSTED: simulation concurrency slots reached")
            reservation = "res-" + uuid.uuid4().hex
            db.execute("INSERT INTO simulation_reservations VALUES (?, ?, ?, 'RESERVED', ?)", (reservation, run_id, request_key, now()))
        return {"reservation_id": reservation, "state": "RESERVED", "idempotent": False}

    def settle_simulation(self, reservation_id: str) -> None:
        """A dispatched attempt stays charged whether its engine succeeded or failed."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT run_id, state FROM simulation_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if not row:
                raise NotFoundError("Unknown simulation reservation")
            if row["state"] == "SPENT":
                return
            if row["state"] != "RESERVED":
                raise ConflictError("Reservation is not active")
            db.execute("UPDATE simulation_reservations SET state='SPENT' WHERE reservation_id=?", (reservation_id,))
            count = db.execute("SELECT COUNT(*) AS count FROM simulation_reservations WHERE run_id=? AND state='SPENT'", (row["run_id"],)).fetchone()["count"]
            run_row = db.execute("SELECT payload FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
            run = json.loads(run_row["payload"])
            run["budget_usage"]["simulations"] = count
            db.execute("UPDATE runs SET payload=?, updated_at=? WHERE run_id=?", (canonical(run), now(), row["run_id"]))

    def create_simulation_job(self, run_id: str, job_id: str, candidate_id: str, reservation_id: str | None) -> dict:
        """Persist a gateway job intent before any child process is spawned."""
        require(re.fullmatch(r"job-[A-Za-z0-9_-]+", job_id) is not None, "Invalid job identifier")
        require(isinstance(candidate_id, str) and candidate_id, "Candidate identifier is required")
        with self._db() as db:
            run = db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise NotFoundError("Unknown run")
            if reservation_id is not None:
                reservation = db.execute("SELECT run_id FROM simulation_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if not reservation or reservation["run_id"] != run_id:
                    raise ConflictError("Simulation reservation does not belong to run")
            existing = db.execute("SELECT payload FROM simulation_jobs WHERE job_id=?", (job_id,)).fetchone()
            if existing:
                return json.loads(existing["payload"])
            record = dict(job_id=job_id, run_id=run_id, candidate_id=candidate_id, reservation_id=reservation_id,
                          status="QUEUED", result_ref=None, error=None, created_at=now(), updated_at=now())
            db.execute("INSERT INTO simulation_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (job_id, run_id, candidate_id, reservation_id, record["status"], canonical(record), record["created_at"], record["updated_at"]))
        self._write_json(f"{run_id}/jobs/{job_id}.json", record)
        return record

    def get_simulation_job(self, job_id: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT payload FROM simulation_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise NotFoundError("Unknown simulation job")
        return json.loads(row["payload"])

    def update_simulation_job(self, job_id: str, status: str, *, result: dict | None = None, error: dict | None = None) -> dict:
        require(status in {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "ERROR"}, "Invalid simulation job status")
        with self._db() as db:
            row = db.execute("SELECT payload FROM simulation_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise NotFoundError("Unknown simulation job")
            record = json.loads(row["payload"])
            if record["status"] in {"SUCCEEDED", "FAILED", "CANCELLED", "ERROR"} and status != record["status"]:
                return record
            record["status"], record["updated_at"] = status, now()
            if result is not None:
                result_ref = self._write_json(f"{record['run_id']}/jobs/{job_id}-result.json", result)
                record["result_ref"] = result_ref
            if error is not None:
                record["error"] = error
            db.execute("UPDATE simulation_jobs SET status=?, payload=?, updated_at=? WHERE job_id=?",
                       (status, canonical(record), record["updated_at"], job_id))
        self._write_json(f"{record['run_id']}/jobs/{job_id}.json", record)
        return record

    def recover_simulation_jobs(self) -> list[dict]:
        """Mark child jobs from a previous gateway process as unrecoverable."""
        with self._db() as db:
            rows = db.execute("SELECT payload FROM simulation_jobs WHERE status IN ('QUEUED','RUNNING')").fetchall()
        recovered = []
        for row in rows:
            record = json.loads(row["payload"])
            recovered.append(self.update_simulation_job(record["job_id"], "ERROR", error={"code": "GATEWAY_RESTARTED", "message": "Gateway process restarted; child simulation cannot be resumed."}))
            if record.get("reservation_id"):
                self.settle_simulation(record["reservation_id"])
        return recovered
