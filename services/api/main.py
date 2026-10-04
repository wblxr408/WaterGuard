"""FastAPI application for WaterGuard's user-facing, non-agent API boundary."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field
from starlette.responses import FileResponse, StreamingResponse
from starlette.staticfiles import StaticFiles

from .runtime import ConflictError, NotFoundError, ROOT, RunStore
from .workbench import BenchmarkExecutor, TaskProposal, network_details, propose_task, run_results


class RunRequest(BaseModel):
    mode: Literal["live", "fixture", "benchmark", "replay"] = "live"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


def app_store(artifacts: Path | None = None, database: Path | None = None) -> RunStore:
    artifacts = artifacts or Path(os.environ.get("WG_ARTIFACT_ROOT", ROOT / "artifacts"))
    database = database or Path(os.environ.get("WG_DATABASE", artifacts / "waterguard.sqlite3"))
    return RunStore(database, artifacts)


def create_app(store: RunStore | None = None) -> FastAPI:
    store = store or app_store()
    executor = BenchmarkExecutor(store)

    @asynccontextmanager
    async def lifespan(_app):
        executor.recover()
        yield
        executor.close()

    app = FastAPI(title="WaterGuard API", version="0.1.0", lifespan=lifespan)
    app.state.benchmark_executor = executor
    app.mount("/assets", StaticFiles(directory=ROOT / "design"), name="assets")

    @app.get("/", include_in_schema=False)
    def workbench():
        return FileResponse(ROOT / "design" / "waterguard-blueprint.html", headers={"Cache-Control": "no-cache"})

    def fail(error: Exception):
        if isinstance(error, NotFoundError):
            raise HTTPException(404, {"code": "NOT_FOUND", "message": str(error)})
        if isinstance(error, ConflictError):
            raise HTTPException(409, {"code": "CONFLICT", "message": str(error)})
        raise HTTPException(422, {"code": "INPUT_INVALID", "message": str(error)})

    @app.get("/api/networks")
    def networks():
        manifests = []
        for path in sorted((ROOT / "data" / "manifests").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            manifests.append({key: payload[key] for key in ("network_id", "revision", "sha256", "source_url", "license", "description")})
        return manifests

    @app.get("/api/networks/{network_id}")
    def network(network_id: str):
        try:
            return network_details(network_id)
        except Exception as error:
            fail(error)

    @app.post("/api/task-proposals")
    def proposal(request: TaskProposal):
        try:
            return propose_task(request)
        except Exception as error:
            fail(error)

    @app.post("/api/tasks/from-proposal", status_code=201)
    def create_from_proposal(request: TaskProposal):
        try:
            # Freeze and hash in Python, avoiding JS reserialization of 20.0 as 20.
            return store.save_task(propose_task(request))
        except Exception as error:
            fail(error)

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str, revision: int | None = None):
        try:
            return store.get_task(task_id, revision)
        except Exception as error:
            fail(error)

    @app.get("/api/runs")
    def recent_runs():
        return store.recent_runs()

    @app.post("/api/tasks", status_code=201)
    def create_task(task: dict[str, Any]):
        try:
            return store.save_task(task)
        except Exception as error:
            fail(error)

    @app.post("/api/tasks/{task_id}/revisions", status_code=201)
    def revise_task(task_id: str, task: dict[str, Any]):
        if task.get("task_id") != task_id:
            raise HTTPException(422, {"code": "INPUT_INVALID", "message": "task_id does not match path"})
        try:
            return store.save_task(task)
        except Exception as error:
            fail(error)

    @app.post("/api/tasks/{task_id}/runs", status_code=202)
    def start_run(task_id: str, request: RunRequest, revision: int | None = None):
        if request.mode == "live":
            raise HTTPException(503, {"code": "AGH_UNAVAILABLE", "message": "AGH host is not configured; live runs cannot fall back to a hidden agent loop."})
        try:
            task = store.get_task(task_id, revision)
            if request.mode == "benchmark" and task["schema_version"] != "0.4.0":
                raise ValueError("Web benchmarks require a physical TaskSpec 0.4.0")
            run = store.create_run(task, request.mode, request.idempotency_key)
            if request.mode == "benchmark":
                executor.dispatch(store.get_run(run["run_id"]))
            return run
        except Exception as error:
            fail(error)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        try:
            return store.get_run(run_id)
        except Exception as error:
            fail(error)

    @app.get("/api/runs/{run_id}/candidates")
    def candidates(run_id: str):
        try:
            return store.list_candidates(run_id)
        except Exception as error:
            fail(error)

    @app.get("/api/runs/{run_id}/results")
    def results(run_id: str):
        try:
            return run_results(store, run_id)
        except Exception as error:
            fail(error)

    @app.get("/api/runs/{run_id}/error")
    def run_error(run_id: str):
        try:
            store.get_run(run_id)
            path = store._run_directory(run_id) / "error.json"
            if not path.is_file():
                raise NotFoundError("Run has no error record")
            return store._read_json(path)
        except Exception as error:
            fail(error)

    @app.get("/api/simulations/{simulation_id}/series")
    def series(simulation_id: str, node_id: str | None = None, start_s: int | None = None, end_s: int | None = None):
        try:
            return store.get_series(simulation_id, node_id, start_s, end_s)
        except Exception as error:
            fail(error)

    @app.get("/api/runs/{run_id}/evidence")
    def evidence(run_id: str):
        try:
            return store.get_evidence(run_id)
        except Exception as error:
            fail(error)

    @app.post("/api/runs/{run_id}/answers")
    def answer(run_id: str, _answer: dict[str, Any]):
        try:
            run = store.get_run(run_id)
            if run["status"] != "NEEDS_INPUT":
                raise ConflictError("Run is not awaiting a clarification answer")
            raise HTTPException(503, {"code": "AGH_UNAVAILABLE", "message": "AGH resume adapter is not configured."})
        except HTTPException:
            raise
        except Exception as error:
            fail(error)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        try:
            run = store.get_run(run_id)
            if run["status"] == "CANCELLED":
                return run
            if run["status"] in {"COMPLETED", "BUDGET_EXHAUSTED", "ERROR"}:
                raise ConflictError("Terminal run cannot be cancelled")
            return executor.cancel(run_id)
        except Exception as error:
            fail(error)

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, last_event_id: str | None = Header(default=None)):
        try:
            after = int(last_event_id or "0")
            if after < 0:
                raise ValueError("Last-Event-ID must be non-negative")
            pending = store.events_after(run_id, after)
        except Exception as error:
            fail(error)
        def stream():
            for event in pending:
                yield f"id: {event['sequence']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
