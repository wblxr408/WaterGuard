"""Generate auditable baseline and one-action candidates, never agent decisions."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))

from waterguard_contracts.core import check_plan, plan_hash, require, task_ref, validate


def _plan(task: dict, candidate_id: str, actions: list[dict], reason: str) -> dict:
    plan = dict(schema_version=task["schema_version"], candidate_id=candidate_id, task_ref=task_ref(task),
                parent_candidate_id=None, actions=actions, source="enumeration", reason=reason, plan_hash="")
    plan["plan_hash"] = plan_hash(plan)
    return plan


def generate_candidates(task: dict, catalog: dict, *, limit: int = 64) -> list[dict]:
    """Return a stable finite set; the caller chooses what to simulate.

    It intentionally considers only zero or one explicit action.  This is a
    safe initial enumeration rather than a hidden multi-step optimization loop.
    """
    require(isinstance(limit, int) and 1 <= limit <= 256, "Candidate limit must be between 1 and 256")
    facilities = {facility["link_id"]: facility for facility in catalog["facilities"]}
    limits = task["action_limits"]
    times = range(limits["earliest_s"], limits["latest_s"] + 1, task["time_window"]["step_s"])
    candidates = [_plan(task, "enum-baseline", [], "Deterministic no-action baseline.")]
    # The baseline is itself a candidate and therefore consumes one of the
    # caller's bounded simulation slots.  Returning early also avoids doing
    # unnecessary catalogue work for a one-run diagnostic budget.
    if limit == 1:
        validate("ActionPlan", candidates[0])
        check_plan(task, candidates[0], catalog)
        return candidates
    forced_link = task["fault"]["link_id"] if task["fault"] else None
    for link_id in sorted(limits["allowed_link_ids"]):
        if link_id == forced_link:
            continue
        facility = facilities.get(link_id)
        if not facility:
            continue
        for kind in sorted(set(limits["allowed_kinds"]) & set(facility["allowed_actions"])):
            for time_s in times:
                action = {"kind": kind, "link_id": link_id, "simulation_time_s": time_s}
                candidate = _plan(task, f"enum-{link_id}-{kind}-{time_s}", [action], "One-action finite-domain enumeration.")
                check_plan(task, candidate, catalog)
                candidates.append(candidate)
                if len(candidates) >= limit:
                    return candidates
    for candidate in candidates:
        validate("ActionPlan", candidate)
        check_plan(task, candidate, catalog)
    return candidates
