"""Deterministic web benchmark execution using the verified physical adapter."""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from .runtime import ConflictError, NotFoundError, RunStore, TERMINAL
from waterguard_contracts.core import check_task, plan_hash, task_hash, task_ref, validate


class TaskProposal(BaseModel):
    network_id: Literal["epa-net1", "epa-net3"] = "epa-net1"
    fault_kind: Literal["normal", "closure", "leak", "rupture"] = "closure"
    link_id: str | None = None
    min_pressure_m: float = Field(default=20, ge=0, le=100, allow_inf_nan=False)
    min_supply_ratio: float = Field(default=.95, ge=0, le=1, allow_inf_nan=False)
    candidate_limit: int = Field(default=3, ge=1, le=6)


def network_details(network_id: str) -> dict:
    from services.hydraulics.compiler import assets
    from services.hydraulics.datasets import NETWORKS
    if network_id not in NETWORKS:
        raise NotFoundError("Unknown public network")
    wn, source, scene, catalog = assets(network_id)
    fault_links = [f["link_id"] for f in catalog["facilities"]
                   if f["kind"] == "pipe" and f["initial_status"] == "open"
                   and not f["requires_explicit_override"]]
    return dict(manifest=source, scene=scene, catalog=catalog,
                fault_link_ids=fault_links, junction_ids=wn.junction_name_list)


def propose_task(proposal: TaskProposal) -> dict:
    from services.hydraulics.scenarios import physical_case
    details = network_details(proposal.network_id)
    task, _ = physical_case(proposal.network_id, proposal.fault_kind)
    task["task_id"] = "web-" + uuid.uuid4().hex
    if task["fault"]:
        link = proposal.link_id or task["fault"]["link_id"]
        if link not in details["fault_link_ids"]:
            raise ValueError("Fault must target an initially open pipe without a native control conflict")
        task["fault"]["link_id"] = link
    task["hard_constraints"].update(
        min_pressure_m=proposal.min_pressure_m, min_supply_ratio=proposal.min_supply_ratio,
        node_ids=details["junction_ids"],
        source="User-entered workbench scenario thresholds; not field calibration.")
    task["budget"]["max_simulations"] = proposal.candidate_limit
    # Restrict the comparison to manually operable facilities and one action.
    task["action_limits"].update(max_actions=1, latest_s=3600,
        allowed_link_ids=[f["link_id"] for f in details["catalog"]["facilities"]
                          if not f["requires_explicit_override"]])
    task["constraint_hash"] = task_hash(task)
    check_task(task, details["catalog"], details["scene"])
    return task


def benchmark_plans(task: dict, catalog: dict) -> list[dict]:
    from services.hydraulics.compiler import compile_task
    def make(actions: list[dict], index: int) -> dict:
        plan = dict(schema_version=task["schema_version"], candidate_id=f"web-candidate-{index}",
                    task_ref=task_ref(task), parent_candidate_id=None, actions=actions,
                    source="enumeration", reason="Fixed no-action / one-action benchmark; no Agnes decision.")
        plan["plan_hash"] = plan_hash(plan)
        compile_task(task, plan)
        return plan
    plans = [make([], 0)]
    limit = min(task["budget"]["max_simulations"], 6)
    if limit == 1 or task["action_limits"]["max_actions"] == 0:
        return plans
    fault = task["fault"] or {}
    excluded = {fault.get("link_id"), *fault.get("boundary_link_ids", [])}
    acknowledged = {o["link_id"] for o in task.get("control_policy", {}).get("overrides", [])}
    for facility in sorted(catalog["facilities"], key=lambda f: f["link_id"]):
        link = facility["link_id"]
        if link in excluded or link not in task["action_limits"]["allowed_link_ids"]:
            continue
        if facility.get("requires_explicit_override") and link not in acknowledged:
            continue
        if facility["kind"] == "pump":
            kind = "pump_on" if facility["initial_status"] == "closed" else "pump_off"
        else:
            kind = "link_open" if facility["initial_status"] == "closed" else "link_close"
        if kind not in task["action_limits"]["allowed_kinds"] or kind not in facility["allowed_actions"]:
            continue
        plans.append(make([dict(kind=kind, link_id=link,
                                simulation_time_s=task["action_limits"]["earliest_s"])], len(plans)))
        if len(plans) >= limit:
            break
    return plans


class BenchmarkExecutor:
    def __init__(self, store: RunStore):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="waterguard-benchmark")
        self.lock = threading.Lock()
        self.cancellations: dict[str, threading.Event] = {}
        self.closed = False

    def recover(self) -> None:
        # A killed API supervisor cannot safely resume its previous subprocess.
        for run in self.store.active_benchmarks():
            ref = self.store._write_json(f"{run['run_id']}/error.json",
                                         dict(code="SERVER_RESTARTED", message="Benchmark interrupted by server restart; create a new run."))
            self.store.transition(run["run_id"], "ERROR")
            self.store.emit(self.store.get_run(run["run_id"]), "user", "run.completed", ref)
            self.package(run["run_id"])

    def dispatch(self, run: dict) -> None:
        if run["status"] in TERMINAL:
            return
        with self.lock:
            if self.closed:
                raise ConflictError("Benchmark executor is shutting down")
            if run["run_id"] in self.cancellations:
                return
            cancel = self.cancellations[run["run_id"]] = threading.Event()
            self.pool.submit(self.execute, run["run_id"], cancel)

    def cancel(self, run_id: str) -> dict:
        with self.lock:
            run = self.store.get_run(run_id)
            if run["status"] == "CANCELLED":
                return run
            if run["status"] in TERMINAL:
                raise ConflictError("Terminal run cannot be cancelled")
            result = self.store.transition(run_id, "CANCELLED")
            if run_id in self.cancellations:
                self.cancellations[run_id].set()
            return result

    def package(self, run_id: str) -> None:
        from services.evidence.service import build_evidence
        run = self.store.get_run(run_id)
        self.store._write_json(f"{run_id}/run.json", run)
        build_evidence(self.store.artifacts, run)

    def execute(self, run_id: str, cancel: threading.Event) -> None:
        started = time.monotonic()
        try:
            from services.hydraulics.runtime import PhysicalAdapter
            from services.hydraulics.compiler import assets
            from services.verifier.service import verify
            with self.lock:
                if cancel.is_set():
                    return
                run = self.store.transition(run_id, "RUNNING", "BASELINE")
            task = self.store.get_task(run["task_ref"]["task_id"], run["task_ref"]["revision"])
            if task["schema_version"] != "0.4.0":
                raise ValueError("Web benchmarks require a physical TaskSpec 0.4.0")
            _, _, scene, catalog = assets(task["network_ref"]["network_id"])
            plans = benchmark_plans(task, catalog)
            root = self.store._run_directory(run_id)
            adapter = PhysicalAdapter(root / "simulations")
            self.store._write_json(f"{run_id}/task.json", task)
            for plan in plans:
                if cancel.is_set():
                    return
                remaining = task["budget"]["max_wall_time_s"] - (time.monotonic() - started)
                if remaining <= 0:
                    with self.lock:
                        if not cancel.is_set():
                            self.store.transition(run_id, "BUDGET_EXHAUSTED", wall_time_s=time.monotonic()-started)
                    return
                cid = plan["candidate_id"]
                reservation = self.store.reserve_simulation(run_id, cid)
                ref = self.store._write_json(f"{run_id}/candidates/{cid}/plan.json", plan)
                self.store.emit(run, "gateway", "candidate.submitted", ref, candidate_id=cid)
                with self.lock:
                    if cancel.is_set():
                        self.store.settle_simulation(reservation["reservation_id"])
                        return
                    self.store.transition(run_id, "RUNNING", "SIMULATING")
                try:
                    result = adapter.simulate(task, plan, timeout_s=remaining, cancel_event=cancel)
                finally:
                    self.store.settle_simulation(reservation["reservation_id"])
                sid = result["artifact"]["simulation_id"]
                ref = self.store._write_json(f"{run_id}/candidates/{cid}/result.json", result)
                self.store.emit(run, "worker", "simulation.completed", ref, candidate_id=cid, simulation_id=sid)
                if cancel.is_set():
                    return
                if result["artifact"]["status"] != "SUCCEEDED":
                    raise ValueError(str(result["diagnostics"].get("errors", ["Simulation failed"])))
                with self.lock:
                    if cancel.is_set():
                        return
                    self.store.transition(run_id, "RUNNING", "VERIFYING")
                report = verify(task, plan, result, catalog, scene)
                ref = self.store._write_json(f"{run_id}/candidates/{cid}/verification.json", report)
                self.store.emit(run, "verifier", "verification.completed", ref, candidate_id=cid, simulation_id=sid)
            with self.lock:
                if not cancel.is_set():
                    # Nominal comparisons cannot certify pending stress tests / AGH provenance.
                    self.store.transition(run_id, "COMPLETED", "PACKAGING", wall_time_s=time.monotonic()-started)
        except Exception as error:
            ref = self.store._write_json(f"{run_id}/error.json", dict(code="BENCHMARK_FAILED", message=f"{type(error).__name__}: {error}"))
            with self.lock:
                current = self.store.get_run(run_id)
                if current["status"] not in TERMINAL:
                    current = self.store.transition(run_id, "ERROR", wall_time_s=time.monotonic()-started)
                self.store.emit(current, "user", "run.completed", ref)
        finally:
            with self.lock:
                if self.store.get_run(run_id)["status"] == "CANCELLED":
                    self.store.transition(run_id, "CANCELLED", wall_time_s=time.monotonic()-started)
            self.package(run_id)
            with self.lock:
                self.cancellations.pop(run_id, None)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for run_id, cancel in self.cancellations.items():
                if self.store.get_run(run_id)["status"] not in TERMINAL:
                    self.store.transition(run_id, "CANCELLED")
                cancel.set()
        self.pool.shutdown(wait=True)


def run_results(store: RunStore, run_id: str) -> list[dict]:
    root = store._run_directory(run_id)
    results = []
    for plan in store.list_candidates(run_id):
        folder = root / "candidates" / plan["candidate_id"]
        result = store._read_json(folder / "result.json") if (folder / "result.json").is_file() else None
        report = store._read_json(folder / "verification.json") if (folder / "verification.json").is_file() else None
        if result:
            validate("SimulationResult", result)
        if report:
            validate("VerificationReport", report)
        results.append(dict(plan=plan, artifact=result["artifact"] if result else None,
                            diagnostics=result["diagnostics"] if result else None, verification=report))
    return results
