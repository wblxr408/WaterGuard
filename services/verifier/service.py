"""Verify engine artifacts without consulting the model or agent self-assessment."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))

from waterguard_contracts.core import verify_benchmark, verify_fixture


def verify(task: dict, plan: dict, result: dict, catalog: dict, scene: dict) -> dict:
    """Recompute constraints and metrics from stored series in its declared mode."""
    if result.get("mode") == "benchmark":
        return verify_benchmark(task, plan, result, catalog, scene)
    if result.get("mode") == "fixture":
        return verify_fixture(task, plan, result, catalog, scene)
    raise ValueError("Verifier accepts only benchmark or fixture simulation artifacts")
