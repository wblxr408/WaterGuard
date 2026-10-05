"""WaterGuard MCP gateway for bounded engineering tools.

It exposes deterministic preflight, candidate enumeration and computation
handles.  Agent decision making remains in AGH; the gateway never calls a
model or silently turns a development run into a live AGH run.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from jsonschema.exceptions import ValidationError
from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "data" / "manifests"
CATALOGS = ROOT / "data" / "catalogs"
NETWORK_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# The gateway is launched by both AGH and developer tools, whose working
# directories are not guaranteed to be the repository root.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONTRACTS = ROOT / "packages" / "contracts"
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))

from waterguard_contracts.core import FIXTURES, check_plan, digest, file_hash, load, require, task_hash, validate
from services.candidate_generation.generator import generate_candidates
from services.evidence.service import build_evidence
from services.jobs.runner import SimulationJobManager
from services.api.runtime import RunStore
from services.policy.authorization import ExecutionIdentity, authorize
from services.verifier.service import verify

mcp = FastMCP("WaterGuard")
JOBS = SimulationJobManager(ROOT / "artifacts" / "gateway-jobs")
JOB_CONTEXT: dict[str, dict] = {}


def _strict_gateway() -> bool:
    """Whether this process is being hosted as an AGH production gateway.

    Local fixture use deliberately stays available for deterministic unit tests.
    A real AGH launch sets this once in the server environment; a model cannot
    alter it through an MCP argument.
    """
    return os.environ.get("WG_GATEWAY_STRICT") == "1"


def _bound_run(run_id: str, tool_name: str) -> tuple[RunStore, dict, dict]:
    """Load an immutable task only after host-side identity authorization."""
    if not _strict_gateway():
        raise ValueError("Bound run access requires WG_GATEWAY_STRICT=1")
    if not isinstance(run_id, str) or not re.fullmatch(r"run-[A-Za-z0-9_-]+", run_id):
        raise ValueError("A valid server-issued run_id is required")
    identity = ExecutionIdentity(
        agh_session_ref=os.environ.get("WG_AGH_SESSION_REF", ""),
        role=os.environ.get("WG_AGH_ROLE", ""),
        host_verified=os.environ.get("WG_AGH_HOST_VERIFIED") == "1",
    )
    authorize(identity, tool_name)
    artifacts = Path(os.environ.get("WG_ARTIFACT_ROOT", ROOT / "artifacts"))
    database = Path(os.environ.get("WG_DATABASE", artifacts / "waterguard.sqlite3"))
    store = RunStore(database, artifacts)
    run = store.get_run(run_id)
    if run.get("agh_session_ref") != identity.agh_session_ref:
        raise ValueError("Run is not bound to this host-verified AGH session")
    task = store.get_task(run["task_ref"]["task_id"], run["task_ref"]["revision"])
    return store, run, task


def _task_for_call(run_id: str | None, task_json: str | None, tool_name: str) -> tuple[dict, RunStore | None, dict | None]:
    """Use the persisted task in strict mode; legacy JSON is development-only."""
    if _strict_gateway():
        if task_json is not None:
            raise ValueError("Strict gateway rejects caller-supplied task_json; use the frozen run_id")
        store, run, task = _bound_run(run_id or "", tool_name)
        return task, store, run
    if task_json is None:
        raise ValueError("task_json is required outside a strict AGH gateway")
    task, _ = _objects(task_json)
    return task, None, None


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def _catalog_for(network_id: str, schema_version: str | None = None) -> tuple[dict, dict]:
    """Load a server-owned catalogue for the task contract generation.

    The original gateway only understood the 0.2 JSON fixture catalogue.  A
    physical 0.3/0.4 task instead has a compiler-derived v3 catalogue, whose
    hash is part of the frozen task.  Selecting it here prevents a gateway
    request from silently downgrading a physical task to legacy semantics.
    """
    if not NETWORK_ID.fullmatch(network_id):
        raise ValueError("network_id must contain lowercase letters, digits, and hyphens only")

    if schema_version in {"0.3.0", "0.4.0"}:
        from services.hydraulics.compiler import assets
        _wn, manifest, _scene, catalog = assets(network_id)
        return manifest, catalog

    manifest_path = (MANIFESTS / f"{network_id}.json").resolve()
    catalog_path = (CATALOGS / f"{network_id}.json").resolve()
    if manifest_path.parent != MANIFESTS.resolve() or catalog_path.parent != CATALOGS.resolve():
        raise ValueError("network_id resolves outside the approved catalog")
    if not manifest_path.is_file() or not catalog_path.is_file():
        raise ValueError(f"Unknown approved network: {network_id}")

    manifest = _load_json(manifest_path)
    catalog = _load_json(catalog_path)
    if manifest["network_id"] != network_id or catalog["network_id"] != network_id:
        raise ValueError("Catalog metadata is inconsistent")
    return manifest, catalog


def _preflight_action_plan(task: dict, plan: dict, catalog: dict) -> dict:
    """Validate a candidate against the frozen task and action catalog.

    This is deliberately a preflight, not a simulation or recommendation.
    It catches invalid candidate actions before a later tool dispatches a
    WNTR/EPANET job owned by the hydraulics service.
    """

    validate("TaskSpec", task)
    validate("ActionCatalog", catalog)
    require(task_hash(task) == task["constraint_hash"], "Task hash mismatch")
    require(task["network_ref"]["network_id"] == catalog["network_id"], "Network ID mismatch")
    check_plan(task, plan, catalog)

    facilities = {facility["link_id"]: facility for facility in catalog["facilities"]}
    return {
        "candidate_id": plan["candidate_id"],
        "task_ref": plan["task_ref"],
        "action_count": len(plan["actions"]),
        "action_cost": sum(facilities[action["link_id"]]["action_cost"] for action in plan["actions"]),
    }


def _response(status: str, code: str, summary: str, *, retryable: bool = False, **extra) -> dict:
    return {"status": status, "code": code, "data_ref": None, "summary": summary,
            "violations": [], "retryable": retryable, "remaining_budget": None,
            "correlation_id": None, **extra}


def _objects(task_json: str, plan_json: str | None = None) -> tuple[dict, dict | None]:
    task = json.loads(task_json)
    plan = json.loads(plan_json) if plan_json is not None else None
    if not isinstance(task, dict) or (plan is not None and not isinstance(plan, dict)):
        raise ValueError("JSON arguments must decode to objects")
    return task, plan


def _fixture_scene(task: dict) -> dict:
    if task["network_ref"]["network_id"] != "wg-fixture-loop-v1":
        raise ValueError("Network scene lookup for benchmark verification requires the hydraulics adapter")
    return load(FIXTURES / "scene.json")


@mcp.tool()
def wg_inspect_network(network_id: str) -> dict:
    """Return the provenance and server-approved action catalog for one network.

    The response is reference data only.  It is not a task snapshot, a
    simulation result, or an authorization to operate a facility.
    """

    manifest, catalog = _catalog_for(network_id)
    return {
        "status": "ok",
        "code": "NETWORK_INSPECTED",
        "data_ref": f"data/manifests/{network_id}.json",
        "summary": "Approved public network metadata and action catalog loaded.",
        "violations": [],
        "retryable": False,
        "remaining_budget": None,
        "correlation_id": None,
        "network_manifest": manifest,
        "allowed_action_catalog": catalog,
    }


@mcp.tool()
def wg_propose_task(task_json: str) -> dict:
    """Validate a proposed immutable TaskSpec before it is frozen by the host."""
    try:
        task, _ = _objects(task_json)
        validate("TaskSpec", task)
        require(task_hash(task) == task["constraint_hash"], "Task hash mismatch")
        network_id = task["network_ref"]["network_id"]
        _, catalog = _catalog_for(network_id, task["schema_version"])
        suffix = "-v3" if task["schema_version"] in {"0.3.0", "0.4.0"} else ""
        catalog_path = CATALOGS / f"{network_id}{suffix}.json"
        require(task["action_catalog_ref"] == f"data/catalogs/{network_id}{suffix}.json", "Unapproved action catalog reference")
        require(task["action_catalog_hash"] == file_hash(catalog_path), "Action catalog hash mismatch")
        return _response("ok", "TASK_PROPOSAL_VALID", "Task proposal is structurally valid and bound to the approved catalog.", task=task)
    except Exception as exc:
        return _response("rejected", "INPUT_INVALID", "Task proposal cannot be frozen.", retryable=True, violations=[{"message": str(exc)}])


@mcp.tool()
def wg_generate_candidates(candidate_limit: int = 16, run_id: str | None = None, task_json: str | None = None) -> dict:
    """Enumerate bounded candidates from a frozen run in AGH strict mode.

    ``task_json`` exists only for local fixture development.  A production AGH
    host must supply the server-issued ``run_id`` and never trusts a task sent
    by the model.
    """
    try:
        # Compatibility for the former positional (task_json, limit) helper.
        if isinstance(candidate_limit, str):
            task_json, candidate_limit, run_id = candidate_limit, (run_id if isinstance(run_id, int) else 16), None
        task, _store, _run = _task_for_call(run_id, task_json, "wg_generate_candidates")
        _, catalog = _catalog_for(task["network_ref"]["network_id"], task["schema_version"])
        candidates = generate_candidates(task, catalog, limit=candidate_limit)
        return _response("ok", "CANDIDATES_GENERATED", "Finite-domain candidates generated; AGH must choose what to simulate.", candidates=candidates)
    except Exception as exc:
        return _response("rejected", "INPUT_INVALID", "Candidate generation request is invalid.", retryable=True, violations=[{"message": str(exc)}])


@mcp.tool()
def wg_submit_plan(plan_json: str, run_id: str | None = None, task_json: str | None = None) -> dict:
    """Accept an action plan only after deterministic catalog and hash preflight."""
    try:
        # Compatibility for the former positional (task_json, plan_json) helper.
        if task_json is None and isinstance(run_id, str) and run_id.lstrip().startswith("{"):
            task_json, plan_json, run_id = plan_json, run_id, None
        task, _store, _run = _task_for_call(run_id, task_json, "wg_submit_plan")
        _unused, plan = _objects("{}", plan_json)
        _, catalog = _catalog_for(task["network_ref"]["network_id"], task["schema_version"])
        summary = _preflight_action_plan(task, plan, catalog)
        return _response("ok", "CANDIDATE_ACCEPTED", "Candidate is policy-valid only; simulation is still required.", **summary)
    except Exception as exc:
        return _response("rejected", "INVALID_ACTION", "Candidate failed deterministic action validation.", retryable=True, violations=[{"message": str(exc)}])


@mcp.tool()
def wg_simulate_candidate(plan_json: str, mode: str = "benchmark", run_id: str | None = None, task_json: str | None = None) -> dict:
    """Dispatch an isolated hydraulic calculation and return a job handle."""
    try:
        # Compatibility for the former positional (task_json, plan_json, mode) helper.
        if task_json is None and isinstance(mode, str) and mode.lstrip().startswith("{"):
            task_json, plan_json, mode, run_id = plan_json, mode, (run_id if isinstance(run_id, str) else "benchmark"), None
        task, store, run = _task_for_call(run_id, task_json, "wg_simulate_candidate")
        _unused, plan = _objects("{}", plan_json)
        _, catalog = _catalog_for(task["network_ref"]["network_id"], task["schema_version"])
        _preflight_action_plan(task, plan, catalog)
        reservation = None
        if store is not None and run is not None:
            reservation = store.reserve_simulation(run["run_id"], "gateway:" + plan["plan_hash"])
            if reservation["idempotent"]:
                return _response("rejected", "IDEMPOTENT_REPLAY", "This frozen candidate has already been dispatched for the run.", retryable=False,
                                 reservation_id=reservation["reservation_id"], reservation_state=reservation["state"])
        accepted = JOBS.submit(task, plan, mode)
        if accepted["status"] == "accepted":
            JOB_CONTEXT[accepted["job_id"]] = {"task": task, "plan": plan, "store": store,
                                                "run_id": run["run_id"] if run is not None else None,
                                                "reservation": reservation, "report": None}
            return _response("accepted", accepted["code"], "Isolated simulation job submitted.", job_id=accepted["job_id"])
        if store is not None and reservation is not None:
            store.settle_simulation(reservation["reservation_id"])
        return _response("rejected", accepted["code"], accepted["summary"], retryable=True)
    except Exception as exc:
        return _response("rejected", "INVALID_ACTION", "Simulation dispatch rejected.", retryable=True, violations=[{"message": str(exc)}])


@mcp.tool()
def wg_get_simulation(job_id: str) -> dict:
    """Read the status or result of an isolated simulation job."""
    try:
        result = JOBS.get(job_id)
        context = JOB_CONTEXT.get(job_id)
        if context and context["store"] is not None and context["reservation"] is not None and result["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            context["store"].settle_simulation(context["reservation"]["reservation_id"])
        code = "SIMULATION_" + result["status"]
        return _response("ok" if result["status"] == "SUCCEEDED" else result["status"].lower(), code,
                         "Simulation job status loaded.", retryable=result["status"] == "RUNNING", job=result)
    except Exception as exc:
        return _response("rejected", "NOT_FOUND", "Simulation job is unavailable.", violations=[{"message": str(exc)}])


@mcp.tool()
def wg_verify_candidate(job_id: str) -> dict:
    """Recompute metrics and hard constraints from a completed simulation result."""
    try:
        state = JOBS.get(job_id)
        if state["status"] != "SUCCEEDED":
            return _response("rejected", "ARTIFACT_INVALID", "Simulation has not completed successfully.", retryable=state["status"] == "RUNNING")
        context = JOB_CONTEXT[job_id]
        task, plan = context["task"], context["plan"]
        if state["result"]["mode"] == "fixture":
            catalog, scene = load(FIXTURES / "action-catalog.json"), _fixture_scene(task)
        else:
            if task["schema_version"] in {"0.3.0", "0.4.0"}:
                from services.hydraulics.compiler import assets
                _, _, scene, catalog = assets(task["network_ref"]["network_id"])
            else:
                from services.hydraulics.adapter import network_assets
                _, _, scene, catalog = network_assets(task["network_ref"]["network_id"])
        report = verify(task, plan, state["result"], catalog, scene)
        context["report"] = report
        return _response("ok", "VERIFICATION_COMPLETED", "Independent verification completed.", report=report)
    except Exception as exc:
        return _response("rejected", "ARTIFACT_INVALID", "Verification failed.", violations=[{"message": str(exc)}])


@mcp.tool()
def wg_create_perturbation(plan_json: str, parameters_json: str, run_id: str | None = None, task_json: str | None = None) -> dict:
    """Validate one review perturbation in the task's declared bounded domain."""
    try:
        # Compatibility for the former positional (task_json, plan_json, parameters_json) helper.
        if task_json is None and isinstance(run_id, str) and run_id.lstrip().startswith("{"):
            task_json, plan_json, parameters_json, run_id = plan_json, parameters_json, run_id, None
        task, _store, _run = _task_for_call(run_id, task_json, "wg_create_perturbation")
        _unused, plan = _objects("{}", plan_json)
        parameters = json.loads(parameters_json)
        if not isinstance(parameters, dict) or set(parameters) != {"demand_multiplier"}:
            raise ValueError("parameters_json must contain only demand_multiplier")
        _, catalog = _catalog_for(task["network_ref"]["network_id"], task["schema_version"])
        _preflight_action_plan(task, plan, catalog)
        multiplier = parameters["demand_multiplier"]
        if multiplier not in task["perturbation_domain"]["demand_multipliers"]:
            raise ValueError("Demand multiplier is outside the frozen perturbation domain")
        perturbation = {"perturbation_id": "pert-" + digest({"task_ref": plan["task_ref"], "candidate_id": plan["candidate_id"], "parameters": parameters})[7:23],
                        "candidate_id": plan["candidate_id"], "parameters": parameters,
                        "required_labels": task["perturbation_domain"]["required"]}
        return _response("ok", "PERTURBATION_CREATED", "Bounded review perturbation created; it must still be simulated.", perturbation=perturbation)
    except Exception as exc:
        return _response("rejected", "INPUT_INVALID", "Perturbation request is outside the frozen domain.", retryable=True, violations=[{"message": str(exc)}])


def _pareto_front(reports: list[dict]) -> list[dict]:
    """Return all non-dominated verified candidates without inventing weights."""
    eligible = [report for report in reports if report["admission"]["recommendation_eligible"] == "pass"]
    front = []
    for candidate in eligible:
        metric = candidate["metrics"]
        dominated = any(
            other is not candidate
            and other["metrics"]["shortage_m3"] <= metric["shortage_m3"]
            and other["metrics"]["action_cost"] <= metric["action_cost"]
            and (other["metrics"]["shortage_m3"] < metric["shortage_m3"]
                 or other["metrics"]["action_cost"] < metric["action_cost"])
            for other in eligible
        )
        if not dominated:
            front.append(candidate)
    return sorted(front, key=lambda report: report["candidate_id"])


@mcp.tool()
def wg_compare_candidates(reports_json: str | None = None, run_id: str | None = None, task_json: str | None = None,
                          job_ids_json: str | None = None) -> dict:
    """Compare server-held verification reports without a hidden weighted score.

    In strict AGH mode, callers provide only job identifiers.  Reports are
    retrieved from the gateway's own verification results and tied to the
    host-bound run; arbitrary report JSON is accepted only for local fixture
    development.
    """
    try:
        if _strict_gateway():
            if reports_json is not None or task_json is not None:
                raise ValueError("Strict gateway rejects caller-supplied reports and task_json")
            task, _store, run = _task_for_call(run_id, None, "wg_compare_candidates")
            if not isinstance(job_ids_json, str):
                raise ValueError("job_ids_json is required in strict gateway mode")
            job_ids = json.loads(job_ids_json)
            if not isinstance(job_ids, list) or not job_ids or not all(isinstance(item, str) for item in job_ids):
                raise ValueError("job_ids_json must be a non-empty string array")
            reports = []
            for job_id in job_ids:
                context = JOB_CONTEXT.get(job_id)
                if not context or context.get("report") is None:
                    raise ValueError("Each compared job must have a server-produced verification report")
                context_store = context.get("store")
                if (context_store is None or context_store.database != _store.database
                        or context_store.artifacts != _store.artifacts or context.get("run_id") != run["run_id"]):
                    raise ValueError("Verification report belongs to another run")
                reports.append(context["report"])
        else:
            if task_json is None or reports_json is None:
                raise ValueError("task_json and reports_json are required outside a strict AGH gateway")
            task, _ = _objects(task_json)
            reports = json.loads(reports_json)
        if not isinstance(reports, list) or not reports:
            raise ValueError("reports_json must be a non-empty array")
        for report in reports:
            validate("VerificationReport", report)
            require(report["task_ref"] == {"task_id": task["task_id"], "revision": task["revision"], "network_hash": task["network_ref"]["sha256"], "constraint_hash": task["constraint_hash"]}, "Report task version mismatch")
        eligible = [report for report in reports if report["admission"]["recommendation_eligible"] == "pass"]
        front = _pareto_front(reports)
        return _response("ok", "CANDIDATES_COMPARED", "Reports compared only among candidates that independently passed recommendation admission.",
                         eligible_candidate_ids=[r["candidate_id"] for r in front], selected_candidate_id=front[0]["candidate_id"] if len(front) == 1 else None,
                         excluded_candidate_ids=[r["candidate_id"] for r in reports if r not in eligible])
    except Exception as exc:
        return _response("rejected", "ARTIFACT_INVALID", "Candidate reports cannot be compared.", violations=[{"message": str(exc)}])


@mcp.tool()
def wg_build_evidence(run_json: str) -> dict:
    """Hash the run artifacts and expose missing AGH evidence instead of concealing it."""
    try:
        run = json.loads(run_json)
        validate("RunRecord", run)
        evidence = build_evidence(ROOT / "artifacts", run)
        code = "EVIDENCE_READY" if evidence["live_export_allowed"] else "ELIGIBILITY_INCOMPLETE"
        return _response("ok", code, "Evidence manifest built; missing required provenance remains visible.", evidence=evidence)
    except Exception as exc:
        return _response("rejected", "ARTIFACT_INVALID", "Evidence package could not be built.", violations=[{"message": str(exc)}])


@mcp.tool()
def wg_validate_action_plan(task_json: str, plan_json: str) -> dict:
    """Preflight an ActionPlan against its immutable TaskSpec and approved catalog.

    ``task_json`` and ``plan_json`` must each be one JSON object. They are
    strings at the MCP boundary because the current AGH catalog accepts a
    closed scalar schema more reliably than arbitrary open JSON objects.

    A successful response only means the candidate is syntactically and
    policy-valid.  It does not run hydraulics, prove feasibility, or authorize
    a physical operation.  Rejections are structured feedback for AGH to use
    when revising a candidate.
    """

    try:
        task = json.loads(task_json)
        plan = json.loads(plan_json)
        if not isinstance(task, dict) or not isinstance(plan, dict):
            raise ValueError("task_json and plan_json must each decode to an object")
        network_id = task["network_ref"]["network_id"]
        _, catalog = _catalog_for(network_id, task["schema_version"])
        summary = _preflight_action_plan(task, plan, catalog)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        return _response("rejected", "INVALID_ACTION", "Candidate failed deterministic task or action-catalog validation.", retryable=True, violations=[{"message": str(exc)}])

    return _response("ok", "ACTION_PLAN_VALID", "Candidate passed preflight only; hydraulics and verification are still required.", **summary)


if __name__ == "__main__":
    mcp.run()
