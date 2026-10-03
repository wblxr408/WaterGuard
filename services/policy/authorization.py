"""Trusted host identity checks; never authorize a role supplied by a model."""

from __future__ import annotations

from dataclasses import dataclass


ROLE_CAPABILITIES = {
    "task_modeler": {"wg_inspect_network", "wg_propose_task"},
    "planner": {"wg_inspect_network", "wg_generate_candidates", "wg_submit_plan", "wg_simulate_candidate", "wg_get_simulation", "wg_verify_candidate", "wg_compare_candidates", "wg_build_evidence"},
    "reviewer": {"wg_inspect_network", "wg_get_simulation", "wg_verify_candidate", "wg_create_perturbation", "wg_compare_candidates"},
}


class PermissionDenied(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionIdentity:
    """Created only by an AGH host after authenticating the native session."""
    agh_session_ref: str
    role: str
    host_verified: bool


def authorize(identity: ExecutionIdentity, tool_name: str) -> None:
    if not identity.host_verified or not identity.agh_session_ref:
        raise PermissionDenied("Tool call lacks a host-verified AGH session identity")
    if tool_name not in ROLE_CAPABILITIES.get(identity.role, set()):
        raise PermissionDenied(f"Role {identity.role!r} cannot call {tool_name}")
