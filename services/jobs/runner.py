"""Small process-backed job manager with explicit cancellation semantics."""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))


def _simulate_worker(queue, task: dict, plan: dict, mode: str, output_root: str) -> None:
    """Child process boundary: no AGH/model code is imported here."""
    try:
        if mode == "fixture":
            from waterguard_contracts.core import FixtureAdapter
            result = FixtureAdapter().simulate(task, plan)
        elif mode == "benchmark":
            # Physical 0.3/0.4 tasks use the same sealed worker boundary as
            # the web benchmark.  Keep the legacy adapter only for its
            # explicitly versioned 0.2 fixture catalogue.
            if task.get("schema_version") in {"0.3.0", "0.4.0"}:
                from services.hydraulics.runtime import PhysicalAdapter
                result = PhysicalAdapter(Path(output_root)).simulate(task, plan)
            else:
                from services.hydraulics.adapter import EpanetAdapter
                result = EpanetAdapter(Path(output_root)).simulate(task, plan)
        else:
            raise ValueError("Simulation jobs only support fixture or benchmark mode")
        queue.put({"status": "SUCCEEDED", "result": result})
    except Exception as error:
        queue.put({"status": "FAILED", "code": "SIMULATION_FAILED", "message": f"{type(error).__name__}: {error}"})


@dataclass
class Job:
    job_id: str
    process: mp.Process
    queue: object
    submitted_at: float
    status: str = "QUEUED"
    payload: dict | None = None


class SimulationJobManager:
    """In-memory dispatcher; completed result must be persisted by its caller."""

    def __init__(self, output_root: Path, *, max_concurrent: int = 2):
        self.output_root = Path(output_root)
        self.max_concurrent = max_concurrent
        self.jobs: dict[str, Job] = {}
        self.context = mp.get_context("spawn")

    def submit(self, task: dict, plan: dict, mode: str) -> dict:
        active = sum(job.status in {"QUEUED", "RUNNING"} for job in self.jobs.values())
        if active >= self.max_concurrent:
            return {"status": "rejected", "code": "BUDGET_EXHAUSTED", "summary": "Simulation concurrency slots are exhausted.", "retryable": True}
        queue = self.context.Queue()
        process = self.context.Process(target=_simulate_worker, args=(queue, task, plan, mode, str(self.output_root)))
        job_id = "job-" + uuid.uuid4().hex
        job = Job(job_id, process, queue, time.monotonic())
        self.jobs[job_id] = job
        process.start()
        job.status = "RUNNING"
        return {"status": "accepted", "code": "SIMULATION_QUEUED", "job_id": job_id, "retryable": False}

    def get(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise ValueError("Unknown simulation job")
        if job.status == "RUNNING":
            try:
                job.payload = job.queue.get_nowait()
                job.status = job.payload["status"]
                job.process.join(timeout=0.5)
            except Exception:
                if not job.process.is_alive():
                    job.status = "FAILED"
                    job.payload = {"status": "FAILED", "code": "SIMULATION_FAILED", "message": "Worker exited without a structured result"}
        return {"job_id": job_id, "status": job.status, "elapsed_s": round(time.monotonic() - job.submitted_at, 3), **(job.payload or {})}

    def cancel(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise ValueError("Unknown simulation job")
        if job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return self.get(job_id)
        if job.process.is_alive():
            job.process.terminate()
            job.process.join(timeout=2)
        job.status, job.payload = "CANCELLED", {"code": "CANCELLED", "message": "Worker process terminated by run cancellation."}
        return self.get(job_id)
