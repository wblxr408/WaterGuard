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
            CREATE TABLE IF NOT EXISTS idempotency (
              scope TEXT NOT NULL, key TEXT NOT NULL, response TEXT NOT NULL,
              PRIMARY KEY (scope, key)
            );
            CREATE TABLE IF NOT EXISTS simulation_reservations (
              reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, request_key TEXT NOT NULL,
              state TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id, request_key),
              FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            """)

    def _write_json(self, relative: str, value: Any) -> str:
        from services.hydraulics.storage import write
        target = self.artifacts / relative
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
        require(task_hash(task) == task["constraint_hash"], "Task hash mismatch")
        with self._db() as db:
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
            rows = db.execute("SELECT payload FROM runs WHERE mode='benchmark' AND status IN ('QUEUED','RUNNING')").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def create_run(self, task: dict, mode: str, idempotency_key: str | None = None) -> dict:
        if mode not in {"fixture", "benchmark", "replay"}:
            raise ConflictError("Live runs require the AGH host and cannot be created through this development store")
        scope = f"run:{task['task_id']}:{task['revision']}"
        with self._db() as db:
            if idempotency_key:
                found = db.execute("SELECT response FROM idempotency WHERE scope=? AND key=?", (scope, idempotency_key)).fetchone()
                if found:
                    return json.loads(found["response"])
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
                db.execute("INSERT INTO idempotency VALUES (?, ?, ?)", (scope, idempotency_key, canonical(run)))
        ref = self._write_json(f"{run_id}/run.json", run)
        self.emit(run, "user", "run.started", ref)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise NotFoundError("Unknown run")
        return json.loads(row["payload"])

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

    def transition(self, run_id: str, status: str, phase: str | None = None, *, wall_time_s: float | None = None) -> dict:
        if status not in RUN_STATUS or (phase is not None and phase not in RUN_PHASE):
            raise ValueError("Invalid status or phase")
        with self._db() as db:
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
            validate("RunRecord", run)
            db.execute("UPDATE runs SET status=?, phase=?, payload=?, updated_at=? WHERE run_id=?", (run["status"], run["phase"], canonical(run), now(), run_id))
        ref = self._write_json(f"{run_id}/run.json", run)
        self.emit(run, "user", "run.completed" if status in TERMINAL else "run.started", ref)
        return run

    def emit(self, run: dict, origin: str, event_type: str, payload_ref: str, *, candidate_id: str | None = None, simulation_id: str | None = None, correlation_id: str | None = None) -> dict:
        with self._event_lock:
            return self._emit_serial(run, origin, event_type, payload_ref, candidate_id=candidate_id,
                                     simulation_id=simulation_id, correlation_id=correlation_id)

    def _emit_serial(self, run: dict, origin: str, event_type: str, payload_ref: str, *, candidate_id: str | None = None, simulation_id: str | None = None, correlation_id: str | None = None) -> dict:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            sequence = db.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM events WHERE run_id=?", (run["run_id"],)).fetchone()["sequence"]
            event = dict(schema_version=run["schema_version"], event_id="evt-" + uuid.uuid4().hex, sequence=sequence,
                         run_id=run["run_id"], task_revision=run["task_ref"]["revision"], constraint_hash=run["task_ref"]["constraint_hash"],
                         mode=run["mode"], agh_session_ref=run["agh_session_ref"], native_tool_call_ref=None,
                         native_reference_missing_reason="AGH host not configured" if run["agh_session_ref"] is None else None,
                         correlation_id=correlation_id or "corr-" + uuid.uuid4().hex, origin=origin, type=event_type,
                         candidate_id=candidate_id, simulation_id=simulation_id, occurred_at=now(), simulation_time_s=None,
                         payload_ref=payload_ref)
            validate("RunEvent", event)
            db.execute("INSERT INTO events VALUES (?, ?, ?)", (run["run_id"], sequence, canonical(event)))
        self._append_event(run["run_id"], event)
        return event

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
