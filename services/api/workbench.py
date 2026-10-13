"""Deterministic web benchmark execution using the verified physical adapter."""
from __future__ import annotations

import threading
import time
import uuid
import copy
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from .runtime import ConflictError, NotFoundError, RunStore, TERMINAL
from waterguard_contracts.core import FIXTURES, FixtureAdapter, load, check_task, plan_hash, task_hash, task_ref, validate
from services.hydraulics.configuration import defaults, demand_domain


class TaskProposal(BaseModel):
    network_id: str = Field(default_factory=lambda: defaults()['proposal']['network_id'])
    fault_kind: Literal["normal", "closure", "leak", "rupture", "isolation"] = Field(default_factory=lambda: defaults()['proposal']['fault_kind'])
    link_id: str | None = None
    threshold_mode: Literal['adaptive', 'manual'] | None = None
    min_pressure_m: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    min_supply_ratio: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    demand_increase_pct: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    baseline_retention: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    fault_parameters: dict | None = None
    time_window: dict | None = None
    hydraulic_options: dict | None = None
    physical_parameters: dict | None = None
    action_limits: dict | None = None
    control_overrides: list[dict] | None = None
    response_latency: dict | None = None
    emergency_floor: dict | None = None
    service_priorities: dict | None = None
    candidate_limit: int = Field(default_factory=lambda: defaults()['proposal']['candidate_limit'], ge=1, le=100)
    max_actions: int = Field(default_factory=lambda: defaults()['proposal']['max_actions'], ge=1, le=100)
    max_wall_time_s: int = Field(default_factory=lambda: defaults()['proposal']['max_wall_time_s'], ge=60, le=7200)


def network_details(network_id: str, include_thresholds: bool = False) -> dict:
    from services.hydraulics.compiler import assets
    from services.hydraulics.datasets import NETWORKS
    if network_id not in NETWORKS:
        raise NotFoundError("Unknown public network")
    wn, source, scene, catalog = assets(network_id)
    fault_links = [f["link_id"] for f in catalog["facilities"]
                   if f["kind"] == "pipe" and f["initial_status"] == "open"
                   and not f["requires_explicit_override"]]
    details = dict(manifest=source, scene=scene, catalog=catalog,
                   fault_link_ids=fault_links, junction_ids=wn.junction_name_list)
    if include_thresholds:
        from services.hydraulics.thresholds import recommend
        try:
            details['threshold_recommendation'] = dict(available=True, **recommend(network_id))
        except ValueError as error:
            details['threshold_recommendation'] = dict(available=False, message=str(error))
    return details


def propose_task(proposal: TaskProposal) -> dict:
    from services.hydraulics.scenarios import physical_case
    details = network_details(proposal.network_id)
    task, _ = physical_case(proposal.network_id, proposal.fault_kind)
    task["task_id"] = "web-" + uuid.uuid4().hex
    if proposal.time_window is not None:
        task['time_window'] = copy.deepcopy(proposal.time_window)
    if proposal.hydraulic_options is not None:
        task['hydraulic_options'].update(proposal.hydraulic_options)
    if proposal.physical_parameters is not None:
        task['physical_parameters'].update(copy.deepcopy(proposal.physical_parameters))
    if task["fault"]:
        if proposal.fault_kind == "isolation" and proposal.link_id is not None:
            raise ValueError("Isolation uses the declared topology cut; custom link_id is not supported")
        link = proposal.link_id or task["fault"]["link_id"]
        if link not in details["fault_link_ids"]:
            raise ValueError("Fault must target an initially open pipe without a native control conflict")
        task["fault"]["link_id"] = link
        if proposal.fault_parameters is not None:
            if set(proposal.fault_parameters) - {'start_s', 'end_s', 'location_fraction', 'discharge_coefficient', 'leak_area_m2'}:
                raise ValueError('Unsupported fault parameter')
            task['fault'].update(proposal.fault_parameters)
    elif proposal.fault_parameters:
        raise ValueError('Normal scenario does not accept fault parameters')
    from services.hydraulics.thresholds import apply, recommend
    mode = proposal.threshold_mode or ('manual' if proposal.min_pressure_m is not None or proposal.min_supply_ratio is not None else 'adaptive')
    if mode == 'adaptive':
        if proposal.min_pressure_m is not None or proposal.min_supply_ratio is not None:
            raise ValueError('Adaptive thresholds are computed by the server; select manual mode to enter values')
        if proposal.time_window is not None or proposal.hydraulic_options is not None or proposal.physical_parameters is not None:
            raise ValueError('Custom window or hydraulic options require manual thresholds from a matching reference')
        policy = recommend(proposal.network_id, retention=proposal.baseline_retention)
    else:
        if proposal.min_pressure_m is None or proposal.min_supply_ratio is None:
            raise ValueError('Manual mode requires both pressure and supply thresholds')
        policy = dict(mode='manual', min_pressure_m=proposal.min_pressure_m,
                      min_supply_ratio=proposal.min_supply_ratio,
                      interpretation='User-entered workbench acceptance thresholds.')
    apply(task, policy)
    task['hard_constraints']['node_ids'] = details['junction_ids']
    task['perturbation_domain'] = demand_domain(proposal.demand_increase_pct if proposal.demand_increase_pct is not None
        else defaults()['demand_increase_pct'], task['hydraulic_options']['demand_multiplier'])
    task["budget"]["max_simulations"] = proposal.candidate_limit
    task['budget']['max_wall_time_s'] = proposal.max_wall_time_s
    # Staged controls use only user-frozen, manually operable facilities.
    task["action_limits"].update(max_actions=proposal.max_actions,
        allowed_link_ids=[f["link_id"] for f in details["catalog"]["facilities"]
                          if not f["requires_explicit_override"]])
    if proposal.action_limits is not None:
        task['action_limits'].update(copy.deepcopy(proposal.action_limits))
    if proposal.control_overrides is not None:
        task['control_policy']['overrides'] = copy.deepcopy(proposal.control_overrides)
    if proposal.response_latency is not None:
        # Real-time dispatch: the action window cannot open before the fault is
        # detected, the model decides and the control actuates. The server derives
        # and freezes an honest no-backdating floor; the model cannot lower it.
        if task['fault'] is None:
            raise ValueError('Response latency requires an actual fault scenario')
        from services.hydraulics.configuration import response_latency
        spec = proposal.response_latency
        if 'decision_wall_clock_s' not in spec:
            raise ValueError('response_latency requires a measured or declared decision_wall_clock_s')
        latency = response_latency(task['fault']['start_s'], task['time_window']['step_s'],
            spec['decision_wall_clock_s'], detection_s=spec.get('detection_s'),
            actuation_s=spec.get('actuation_s'), measurement=spec.get('measurement', 'measured'),
            source=spec.get('source', 'AGH native session wall-clock'))
        task['time_window']['evaluation_start_s'] = task['fault']['start_s']
        floor = latency['earliest_effective_s']
        if floor > task['action_limits']['latest_s']:
            raise ValueError('Real response latency leaves no action slot before latest_s')
        task['action_limits']['earliest_s'] = max(task['action_limits']['earliest_s'], floor)
        task['response_latency'] = latency
    emergency = proposal.emergency_floor
    config_floor = defaults().get('emergency_floor', {})
    if emergency is None and 'emergency_floor' not in proposal.model_fields_set and config_floor:
        # Enable the confirmed additional tier for compatible new tasks. A lower
        # custom/adaptive gate cannot acquire a stricter "degraded" service tier.
        if (config_floor['min_pressure_m'] <= task['hard_constraints']['min_pressure_m']
                and config_floor['min_supply_ratio'] <= task['hard_constraints']['min_supply_ratio']):
            emergency = {}
    if emergency is not None:
        # Best-effort livelihood floor: records partial-rescue service, never relaxes
        # the recommendation gate. Falls back to the configured default when the
        # proposal passes an empty object.
        spec = emergency
        floor_spec = dict(min_pressure_m=spec.get('min_pressure_m', config_floor.get('min_pressure_m')),
                          min_supply_ratio=spec.get('min_supply_ratio', config_floor.get('min_supply_ratio')))
        if floor_spec['min_pressure_m'] is None or floor_spec['min_supply_ratio'] is None:
            raise ValueError('Emergency floor requires both a pressure and a supply ratio (config or proposal)')
        floor_spec['source'] = spec.get('source', config_floor.get('source', 'Explicit best-effort livelihood service floor; not a recommendation gate.'))
        task['emergency_floor'] = floor_spec
    if proposal.service_priorities is not None:
        task['service_priorities'] = copy.deepcopy(proposal.service_priorities)
    from services.hydraulics.configuration import livelihood_objectives
    task['objectives'] = livelihood_objectives(task['objectives'], service_tier='emergency_floor' in task,
                                               priorities='service_priorities' in task)
    task["constraint_hash"] = task_hash(task)
    check_task(task, details["catalog"], details["scene"])
    from services.hydraulics.compiler import compile_task
    empty = dict(schema_version=task['schema_version'], candidate_id='proposal-preflight', task_ref=task_ref(task),
                 parent_candidate_id=None, actions=[], source='rule', reason='Task physical preflight only')
    empty['plan_hash'] = plan_hash(empty)
    compile_task(task, empty)
    return task


def benchmark_plans(task: dict, catalog: dict) -> list[dict]:
    from services.hydraulics.compiler import compile_task
    def make(actions: list[dict], index: int) -> dict:
        plan = dict(schema_version=task["schema_version"], candidate_id=f"web-candidate-{index}",
                    task_ref=task_ref(task), parent_candidate_id=None, actions=actions,
                    source="enumeration", reason="Finite topology-ranked controls; AGH selects from actual feedback.")
        plan["plan_hash"] = plan_hash(plan)
        compile_task(task, plan)
        return plan
    plans = [make([], 0)]
    limit = task["budget"]["max_simulations"]
    if limit == 1 or task["action_limits"]["max_actions"] == 0:
        return plans
    fault = task["fault"] or {}
    excluded = {fault.get("link_id"), *fault.get("boundary_link_ids", [])}
    acknowledged = {o["link_id"] for o in task.get("control_policy", {}).get("overrides", [])}
    from services.hydraulics.compiler import assets
    wn = assets(task['network_ref']['network_id'])[0]
    distance = {}
    if fault.get('link_id'):
        target = wn.get_link(fault['link_id'])
        distance = {target.start_node_name: 0, target.end_node_name: 0}
        pending = list(distance)
        adjacency = {node: [] for node in wn.node_name_list}
        for _, link in wn.links():
            adjacency[link.start_node_name].append(link.end_node_name)
            adjacency[link.end_node_name].append(link.start_node_name)
        for node in pending:
            for neighbor in adjacency[node]:
                if neighbor not in distance:
                    distance[neighbor] = distance[node] + 1
                    pending.append(neighbor)
    def priority(facility):
        link = wn.get_link(facility['link_id'])
        # An available alternate supply is considered before isolating pipes.
        return (0 if facility['kind'] == 'pump' and facility['initial_status'] == 'closed' else 1,
                min(distance.get(link.start_node_name, 0), distance.get(link.end_node_name, 0)), facility['link_id'])
    operations = []
    for facility in sorted(catalog["facilities"], key=priority):
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
        action = dict(kind=kind, link_id=link, simulation_time_s=task['action_limits']['earliest_s'])
        operations.append((facility, action))
    # Round-robin singles, restoration sequences and combination sizes so a small
    # budget reaches multi-facility isolation plus available backup supply.
    from itertools import combinations
    max_actions = task['action_limits']['max_actions']
    def restorations():
        restore_s = fault.get('end_s', task['action_limits']['latest_s'])
        for facility, action in operations:
            inverse = {'link_close': 'link_open', 'link_open': 'link_close', 'pump_on': 'pump_off', 'pump_off': 'pump_on'}[action['kind']]
            if (max_actions >= 2 and inverse in facility['allowed_actions']
                    and inverse in task['action_limits']['allowed_kinds']
                    and action['simulation_time_s'] + task['action_limits']['min_switch_interval_s'] <= restore_s <= task['action_limits']['latest_s']):
                yield [action, dict(kind=inverse, link_id=action['link_id'], simulation_time_s=restore_s)]
    actions = [a for _, a in operations]
    streams = [iter(([a] for a in actions)), iter(restorations())]
    streams.extend(iter(combinations(actions, size)) for size in range(2, min(max_actions, len(actions)) + 1))
    while streams and len(plans) < limit:
        for stream in list(streams):
            controls = next(stream, None)
            if controls is None:
                streams.remove(stream)
                continue
            plans.append(make(list(controls), len(plans)))
            if len(plans) >= limit:
                break
    return plans


class BenchmarkExecutor:
    def __init__(self, store: RunStore):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="waterguard-benchmark")
        self.lock = threading.Lock()
        self.evidence_lock = threading.RLock()
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
            else:
                self.package(run_id)
            return result

    def package(self, run_id: str) -> None:
        from services.evidence.service import build_evidence
        with self.evidence_lock:
            run = self.store.get_run(run_id)
            self.store._write_json(f"{run_id}/run.json", run)
            build_evidence(self.store.artifacts, run)

    @staticmethod
    def _controlled_test_delay(cancel: threading.Event) -> None:
        """Test-only delay used to verify Live cancellation propagation.

        It is disabled unless both environment switches are present, so a
        production/user run cannot request artificial delay through the API.
        """
        if os.environ.get('WG_TEST_MODE') != '1':
            return
        try:
            seconds = float(os.environ.get('WG_TEST_SIMULATION_DELAY_S', '0'))
        except ValueError as error:
            raise ValueError('WG_TEST_SIMULATION_DELAY_S must be numeric') from error
        if not 0 <= seconds <= 30:
            raise ValueError('WG_TEST_SIMULATION_DELAY_S must be within 0..30')
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if cancel.wait(min(.05, until - time.monotonic())):
                return

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
            if run["mode"] != "fixture" and task["schema_version"] != "0.4.0":
                raise ValueError("Web benchmarks require a physical TaskSpec 0.4.0")
            source_root = None
            if run["mode"] == "fixture":
                scene, catalog = load(FIXTURES / "scene.json"), load(FIXTURES / "action-catalog.json")
                plans = [load(FIXTURES / f"candidate-{name}.json") for name in ("a", "b")]
            else:
                _, _, scene, catalog = assets(task["network_ref"]["network_id"])
                plans = benchmark_plans(task, catalog)
                if run["mode"] == "replay":
                    from services.hydraulics.replay import verify_evidence
                    from services.hydraulics.runtime import engine_identity
                    source_id = load(self.store._run_directory(run_id) / "replay-source.json")["source_run_id"]
                    source_root = self.store._run_directory(source_id)
                    plans = self.store.list_candidates(source_id)
                    if not plans:
                        raise ValueError("Replay source contains no candidates")
                    for source_plan in plans:
                        original = load(source_root / "candidates" / source_plan["candidate_id"] / "result.json")
                        previous = verify_evidence(source_root / "simulations" / original["artifact"]["simulation_id"])
                        if previous["artifact"]["engine_version"] != engine_identity(task):
                            raise ValueError("Replay engine changed; create a new benchmark")
            root = self.store._run_directory(run_id)
            submitted = root / "submitted-plan.json"
            perturbation_file = root / "perturbation.json"
            perturbation = load(perturbation_file) if perturbation_file.is_file() else None
            if submitted.is_file():
                plans = [load(submitted)]
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
                    if run["mode"] == "fixture":
                        result = copy.deepcopy(FixtureAdapter().simulate(task, plan))
                        # Each API run owns its stored fixture copy; no shared simulation IDs.
                        sid = "sim-" + uuid.uuid4().hex
                        for item in (result["artifact"], result["series"], result["diagnostics"]):
                            item["simulation_id"] = sid
                        for filename, value in (("series.json", result["series"]), ("diagnostics.json", result["diagnostics"]), ("result.json", result)):
                            self.store._write_json(f"{run_id}/simulations/{sid}/{filename}", value)
                    else:
                        self._controlled_test_delay(cancel)
                        if cancel.is_set():
                            return
                        result = adapter.simulate(task, plan, perturbation=perturbation, timeout_s=remaining, cancel_event=cancel)
                finally:
                    self.store.settle_simulation(reservation["reservation_id"])
                sid = result["artifact"]["simulation_id"]
                ref = self.store._write_json(f"{run_id}/candidates/{cid}/result.json", result)
                self.store.emit(run, "worker", "simulation.completed", ref, candidate_id=cid, simulation_id=sid)
                if cancel.is_set():
                    return
                if result["diagnostics"].get("error_code") == "TOOL_TIMEOUT":
                    self.store._write_json(f"{run_id}/error.json", dict(code="BUDGET_EXHAUSTED", message="Simulation exceeded the remaining wall-time budget"))
                    with self.lock:
                        if not cancel.is_set():
                            self.store.transition(run_id, "BUDGET_EXHAUSTED", wall_time_s=time.monotonic()-started)
                    return
                if result["artifact"]["status"] != "SUCCEEDED":
                    raise ValueError(str(result["diagnostics"].get("errors", ["Simulation failed"])))
                if submitted.is_file():
                    # AGH receives the simulation first, then chooses to call independent verification.
                    continue
                with self.lock:
                    if cancel.is_set():
                        return
                    self.store.transition(run_id, "RUNNING", "VERIFYING")
                report = verify(task, plan, result, catalog, scene, perturbation,
                                artifact_dir=root / 'simulations' / sid if run['mode'] != 'fixture' else None)
                ref = self.store._write_json(f"{run_id}/candidates/{cid}/verification.json", report)
                self.store.emit(run, "verifier", "verification.completed", ref, candidate_id=cid, simulation_id=sid)
                if source_root is not None:
                    from services.hydraulics.replay import compare_frames
                    original = load(source_root / "candidates" / cid / "result.json")
                    comparison = dict(source_simulation_id=original["artifact"]["simulation_id"], simulation_id=sid,
                                      recomputed=True, input_hash_identical=result["artifact"]["input_hash"] == original["artifact"]["input_hash"],
                                      **compare_frames(result["series"]["frames"], original["series"]["frames"]))
                    self.store._write_json(f"{run_id}/candidates/{cid}/replay-comparison.json", comparison)
                    if not comparison["input_hash_identical"] or not comparison["numerically_equivalent"]:
                        raise ValueError("Replay differs from the frozen source")
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
            self.store.settle_live_child(run_id)
            with self.lock:
                if self.store.get_run(run_id)["status"] == "CANCELLED":
                    self.store.transition(run_id, "CANCELLED", wall_time_s=time.monotonic()-started)
            try:
                self.package(run_id)
            finally:
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
    results = [row for child in store.live_children(run_id)
               if not (store._run_directory(child['run_id']) / 'perturbation.json').is_file()
               for row in run_results(store, child['run_id'])]
    for plan in store.list_candidates(run_id):
        folder = root / "candidates" / plan["candidate_id"]
        result = store._read_json(folder / "result.json") if (folder / "result.json").is_file() else None
        report = store._read_json(folder / "verification.json") if (folder / "verification.json").is_file() else None
        if result:
            validate("SimulationResult", result)
        if report:
            validate("VerificationReport", report)
        results.append(dict(plan=plan, artifact=result["artifact"] if result else None,
                            diagnostics=result["diagnostics"] if result else None, verification=report,
                            replay_comparison=store._read_json(folder / "replay-comparison.json") if (folder / "replay-comparison.json").is_file() else None))
    return list({row['plan']['candidate_id']: row for row in results}.values())
