"""FastAPI application for WaterGuard's user-facing, non-agent API boundary."""

from __future__ import annotations

import json
import asyncio
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Response, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from starlette.responses import FileResponse, StreamingResponse, JSONResponse, RedirectResponse

from .runtime import ConflictError, NotFoundError, ROOT, RunStore
from .workbench import BenchmarkExecutor, TaskProposal, network_details, propose_task, run_results
from waterguard_contracts.core import FIXTURES, load, task_ref


class RunRequest(BaseModel):
    mode: Literal["live", "fixture", "benchmark", "replay"] = "live"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    source_run_id: str | None = None


class ModelConfigRequest(BaseModel):
    providerId: Literal['agnes-ai', 'deepseek', 'zai']
    baseUrl: str | None = Field(default=None, max_length=2048)
    apiKey: str | None = Field(default=None, max_length=65536)
    model: str | None = Field(default=None, max_length=256)
    accountId: str | None = Field(default=None, pattern=r'^[a-z0-9][a-z0-9-]{0,47}$', max_length=48)


def app_store(artifacts: Path | None = None, database: Path | None = None) -> RunStore:
    artifacts = artifacts or Path(os.environ.get("WG_ARTIFACT_ROOT", ROOT / "artifacts"))
    database = database or Path(os.environ.get("WG_DATABASE", artifacts / "waterguard.sqlite3"))
    return RunStore(database, artifacts)


class JevConfigRequest(BaseModel):
    apiKey: str = Field(min_length=1, max_length=4096)


def create_app(store: RunStore | None = None) -> FastAPI:
    store = store or app_store()
    executor = BenchmarkExecutor(store)
    from .agh import LiveExecutor
    live = LiveExecutor(store, executor)

    @asynccontextmanager
    async def lifespan(_app):
        executor.recover()
        live.recover()
        yield
        live.close()
        executor.close()

    app = FastAPI(title="WaterGuard API", version="0.1.0", lifespan=lifespan)
    app.state.benchmark_executor = executor

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Model settings may contain credentials even when another field is invalid.
        fields = [{key: item[key] for key in ('loc', 'msg', 'type')} for item in error.errors()]
        return JSONResponse(status_code=422, content={'detail': fields})

    @app.get('/api/agh/status')
    def agh_status():
        from .agh import bridge
        try:
            return bridge({'action': 'status'})
        except Exception:
            return {'available': False, 'code': 'AGH_UNAVAILABLE', 'message': '启动本项目 AGH daemon 后刷新连接。'}

    @app.get('/api/jev/status')
    def jev_status():
        from .jev import status
        return status()

    @app.post('/api/jev/config')
    def jev_config(request: JevConfigRequest):
        from .jev import configure
        try:
            return configure(request.apiKey)
        except Exception as error:
            fail(error)

    @app.get('/api/runs/{run_id}/jev')
    def jev_decisions(run_id: str):
        from .jev import decisions, status
        try:
            return {**status(), 'decisions': decisions(store, run_id)}
        except Exception as error:
            fail(error)

    @app.post('/api/tasks/{task_id}/jev-decision')
    def jev_decide(task_id: str, x_wg_agh_session: str | None = Header(default=None)):
        from .jev import decide
        try:
            parent = store.live_parent(x_wg_agh_session, task_id)
            if not parent:
                raise ConflictError('Jev 决策需要已绑定的 Agnes 运行会话。')
            return decide(store, parent)
        except Exception as error:
            fail(error)

    @app.post('/api/agh/models/{action}')
    def configure_model(action: Literal['test', 'save'], request: ModelConfigRequest):
        from .agh import bridge
        try:
            return bridge({'action': action, 'config': request.model_dump(exclude_none=True)})
        except Exception as error:
            raise HTTPException(422, {'code': 'AGH_MODEL_CONFIG_FAILED', 'message': str(error)})

    @app.get("/", include_in_schema=False)
    def workbench(request: Request):
        frontend = os.environ.get("WG_FRONTEND_URL", "http://127.0.0.1:5173/").rstrip("/") + "/"
        if request.url.query:
            frontend += "?" + request.url.query
        return RedirectResponse(frontend, status_code=307, headers={"Cache-Control": "no-store"})

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

    @app.get("/api/fixtures")
    def fixture_inputs():
        return {"task": load(FIXTURES / "task.json"),
                "candidates": [load(FIXTURES / f"candidate-{name}.json") for name in ("a", "b")]}

    @app.get("/api/networks/{network_id}")
    def network(network_id: str):
        try:
            return network_details(network_id, include_thresholds=True)
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
    def get_task(task_id: str, revision: int | None = None, x_wg_agh_session: str | None = Header(default=None)):
        try:
            parent = store.live_parent(x_wg_agh_session, task_id)
            if parent:
                revision = parent['task_ref']['revision']
            return store.get_task(task_id, revision)
        except Exception as error:
            fail(error)

    @app.get("/api/tasks/{task_id}/candidate-plans")
    def candidate_plans(task_id: str, x_wg_agh_session: str | None = Header(default=None)):
        try:
            from .workbench import benchmark_plans
            parent = store.live_parent(x_wg_agh_session, task_id)
            task = store.get_task(task_id, parent['task_ref']['revision'] if parent else None)
            return benchmark_plans(task, network_details(task["network_ref"]["network_id"])["catalog"])
        except Exception as error:
            fail(error)

    @app.post("/api/tasks/{task_id}/candidate-runs", status_code=201)
    def candidate_run(task_id: str, plan: dict[str, Any], x_wg_agh_session: str | None = Header(default=None)):
        """Preflight an Agent-selected plan against a task already frozen by the user."""
        try:
            from services.hydraulics.compiler import compile_task
            from waterguard_contracts.core import plan_hash, validate
            parent = store.live_parent(x_wg_agh_session, task_id)
            task = store.get_task(task_id, parent['task_ref']['revision'] if parent else None)
            validate("ActionPlan", plan)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", plan["candidate_id"]):
                raise ValueError("Invalid candidate identifier")
            if plan_hash(plan) != plan["plan_hash"]:
                raise ValueError("Candidate hash mismatch")
            compile_task(task, plan)
            run = store.create_run(task, "benchmark")
            if parent:
                store.link_live_child(parent['run_id'], run['run_id'])
            store._write_json(f"{run['run_id']}/submitted-plan.json", plan)
            store._write_json(f"{run['run_id']}/task.json", task)
            ref = store._write_json(f"{run['run_id']}/candidates/{plan['candidate_id']}/plan.json", plan)
            store.emit(run, "gateway", "candidate.submitted", ref, candidate_id=plan["candidate_id"])
            return run
        except Exception as error:
            fail(error)

    @app.post("/api/runs/{run_id}/simulate", status_code=202)
    def simulate_submitted(run_id: str):
        try:
            run = store.get_run(run_id)
            if not (store._run_directory(run_id) / "submitted-plan.json").is_file():
                raise ConflictError("Run has no explicitly submitted candidate")
            if run["status"] == "CANCELLED":
                raise ConflictError("Cancelled run cannot be dispatched")
            if run['status'] == 'QUEUED':
                store.reserve_live_child(run_id)
            executor.dispatch(run)
            return store.get_run(run_id)
        except Exception as error:
            fail(error)

    @app.post("/api/runs/{run_id}/verify")
    def verify_submitted(run_id: str):
        try:
            from services.verifier.service import verify
            run = store.get_run(run_id)
            if run["status"] != "COMPLETED" or not (store._run_directory(run_id) / "submitted-plan.json").is_file():
                raise ConflictError("Verification requires a completed submitted-candidate simulation")
            task = store.get_task(run["task_ref"]["task_id"], run["task_ref"]["revision"])
            details = network_details(task["network_ref"]["network_id"])
            # Serialize report/event changes with background packaging and ZIP export.
            with executor.evidence_lock:
                reports = []
                for plan in store.list_candidates(run_id):
                    root = store._run_directory(run_id) / "candidates" / plan["candidate_id"]
                    report = verify(task, plan, store._read_json(root / "result.json"), details["catalog"], details["scene"])
                    ref = store._write_json(f"{run_id}/candidates/{plan['candidate_id']}/verification.json", report)
                    store.emit(run, "verifier", "verification.completed", ref, candidate_id=plan["candidate_id"], simulation_id=report["simulation_id"])
                    reports.append(report)
                executor.package(run_id)
            return reports
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
        try:
            task = store.get_task(task_id, revision)
            if request.mode == 'live':
                from .agh import bridge
                try:
                    status = bridge({'action': 'status'})
                except Exception:
                    raise HTTPException(503, {'code': 'AGH_UNAVAILABLE', 'message': 'AGH daemon is unavailable'})
                model = status['snapshot'].get('provider')
                if not model or not model['credentialConfigured']:
                    raise HTTPException(503, {'code': 'MODEL_NOT_CONFIGURED', 'message': '请在工作台配置并验证 Agnes、DeepSeek 或 GLM 账号。'})
                if model['id'] not in {'agnes-ai', 'deepseek', 'zai'}:
                    raise HTTPException(422, {'code': 'MODEL_NOT_ALLOWED', 'message': '请选择 Agnes、DeepSeek 或 GLM 账号。'})
                if request.source_run_id:
                    raise ValueError('source_run_id is only valid in replay mode')
                run = store.create_run(task, 'live', request.idempotency_key)
                live.dispatch(run, model)
                return run
            if request.mode == "benchmark" and task["schema_version"] != "0.4.0":
                raise ValueError("Web benchmarks require a physical TaskSpec 0.4.0")
            if request.mode == "fixture" and task != load(FIXTURES / "task.json"):
                raise ValueError("Fixture mode accepts only the exact frozen /api/fixtures task")
            if request.mode == "replay":
                if not request.source_run_id:
                    raise ValueError("Replay requires source_run_id")
                source = store.get_run(request.source_run_id)
                if source["status"] != "COMPLETED" or source["mode"] != "benchmark" or source["task_ref"] != task_ref(task):
                    raise ConflictError("Replay requires a completed benchmark with the same frozen task")
                store.get_evidence(request.source_run_id)
            elif request.source_run_id is not None:
                raise ValueError("source_run_id is only valid in replay mode")
            run = store.create_run(task, request.mode, request.idempotency_key, request.source_run_id)
            executor.dispatch(run)
            return run
        except HTTPException:
            raise
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

    @app.get('/api/runs/{run_id}/workspace')
    def run_workspace(run_id: str):
        """Expose only verified candidates bound to this immutable task."""
        try:
            run = store.get_run(run_id)
            task = store.get_task(run['task_ref']['task_id'], run['task_ref']['revision'])
            details = network_details(task['network_ref']['network_id'])
            root = store._run_directory(run_id)
            rows = run_results(store, run_id)
            candidates = []
            for row in rows:
                if row['artifact'] is None or row['verification'] is None:
                    continue
                if row['plan']['task_ref'] != run['task_ref']:
                    raise ConflictError('Candidate belongs to a different frozen task')
                sid = row['artifact']['simulation_id']
                candidate_root = root
                if not (root / 'candidates' / row['plan']['candidate_id'] / 'result.json').is_file():
                    candidate_root = next((store._run_directory(child['run_id']) for child in store.live_children(run_id)
                        if (store._run_directory(child['run_id']) / 'candidates' / row['plan']['candidate_id'] / 'result.json').is_file()), root)
                result = store._read_json(candidate_root / 'candidates' / row['plan']['candidate_id'] / 'result.json')
                simulation_root = next((store._run_directory(child['run_id']) for child in store.live_children(run_id)
                    if (store._run_directory(child['run_id']) / 'simulations' / sid / 'physical-checks.json').is_file()), root)
                if result['artifact']['task_ref'] != run['task_ref'] or row['verification']['task_ref'] != run['task_ref'] or row['verification']['simulation_id'] != sid:
                    raise ConflictError('Simulation or verification belongs to a different frozen task')
                candidates.append(dict(id=row['plan']['candidate_id'],
                    name='基线' if not row['plan']['actions'] else row['plan']['candidate_id'],
                    simulationId=sid, task=task, plan=row['plan'], verification=row['verification'],
                    series=store.get_series(sid), result=result,
                    evidence=store.get_evidence(run_id) if (root / 'evidence-manifest.json').is_file() else store.get_evidence(simulation_root.name),
                    physics=store._read_json(simulation_root / 'simulations' / sid / 'physical-checks.json'),
                    mapping=store._read_json(simulation_root / 'simulations' / sid / 'entity-map.json')))
            if not candidates:
                raise ConflictError('Run has no verified results yet')
            return dict(source='runtime', mode=run['mode'], base='/wg', run=run,
                        scene=details['scene'], manifest=details['manifest'], candidates=candidates, index=None)
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

    @app.get('/api/runs/{run_id}/execution')
    def execution(run_id: str):
        from .execution import execution_trace
        try:
            return execution_trace(store, run_id)
        except Exception as error:
            fail(error)

    @app.get("/api/runs/{run_id}/evidence")
    def evidence(run_id: str):
        try:
            return store.get_evidence(run_id)
        except Exception as error:
            fail(error)

    @app.get("/api/runs/{run_id}/evidence/download")
    def download_evidence(run_id: str):
        try:
            from services.evidence.service import export_evidence
            run = store.get_run(run_id)
            if run["status"] not in {"COMPLETED", "ERROR", "CANCELLED", "BUDGET_EXHAUSTED"}:
                raise ConflictError("Evidence download requires a terminal run")
            with executor.evidence_lock:
                store.get_evidence(run_id)
                archive = export_evidence(store.artifacts, run_id)
            return FileResponse(archive, media_type="application/zip", filename=f"{run_id}-evidence.zip")
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
            return live.cancel(run_id) if run['mode'] == 'live' else executor.cancel(run_id)
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

    @app.get("/api/runs/{run_id}/stream")
    def run_stream(run_id: str, request: Request, follow: bool = True, last_event_id: str | None = Header(default=None)):
        try:
            after = int(last_event_id or "0")
            if not 0 <= after <= 9223372036854775807:
                raise ValueError("Last-Event-ID is outside the supported cursor range")
            store.get_run(run_id)
        except Exception as error:
            fail(error)

        async def stream():
            cursor, idle = after, 0
            # The snapshot has no cursor: reconnects must retain the durable stream cursor.
            yield f"event: run\ndata: {json.dumps(store.get_run(run_id), ensure_ascii=False)}\n\n"
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(store.stream_after, run_id, cursor)
                for row in rows:
                    cursor = row['sequence']
                    yield f"id: {cursor}\nevent: {row['kind']}\ndata: {json.dumps(row['data'], ensure_ascii=False)}\n\n"
                if rows:
                    idle = 0
                    continue
                current = await asyncio.to_thread(store.get_run, run_id)
                if not follow or (current['status'] in {'COMPLETED', 'ERROR', 'CANCELLED', 'BUDGET_EXHAUSTED'}
                                  and await asyncio.to_thread(store.stream_finished, run_id, cursor)):
                    yield f"event: stream.complete\ndata: {json.dumps(current, ensure_ascii=False)}\n\n"
                    return
                idle += 1
                if idle >= 40:
                    yield ': heartbeat\n\n'
                    idle = 0
                await asyncio.sleep(.25)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
