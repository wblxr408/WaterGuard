"""Build an explicit evidence inventory; never invent native AGH records."""

from __future__ import annotations

import sys
import uuid
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))

from waterguard_contracts.core import digest_bytes, validate


def _kind(path: Path) -> str:
    if path.name == "events.jsonl":
        return "application_events"
    if path.name == "run.json":
        return "run_record"
    if path.suffix == ".json":
        return "json_artifact"
    return "binary_artifact"


def build_evidence(artifacts_root: Path, run: dict) -> dict:
    """Create a hash-indexed manifest for a run's existing files.

    ``live_export_allowed`` stays false without both an AGH session reference
    and a real saved native trace.  This prevents fixture/development records
    being presented as competition evidence.
    """
    if not re.fullmatch(r"run-[a-zA-Z0-9_-]+", run["run_id"]):
        raise ValueError("Run ID is not safe for an artifact path")
    root = Path(artifacts_root).resolve()
    run_dir = (root / run["run_id"]).resolve()
    if not run_dir.is_relative_to(root):
        raise ValueError("Run artifacts escape configured root")
    if not run_dir.is_dir():
        raise ValueError("Run artifact directory is missing")
    native = run_dir / "agh" / "native"
    native_ref = "artifact:agh/native" if native.is_dir() and any(native.rglob("*")) else None
    association = run_dir / "agh" / "association.json"
    association_ref = "artifact:agh/association.json" if association.is_file() else None
    missing = list(run.get("missing_items", []))
    if native_ref is None:
        if "native_agh_trace" not in missing:
            missing.append("native_agh_trace")
    if association_ref is None:
        missing.append("agh_event_association")
    entries = []
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file() and p.name != "evidence-manifest.json"):
        relative = path.relative_to(run_dir).as_posix()
        reference = "artifact:" + relative
        entries.append({"ref": reference, "sha256": digest_bytes(path.read_bytes()), "kind": _kind(path)})
    if not entries:
        raise ValueError("Run has no artifacts to evidence")
    evidence = {
        "schema_version": run["schema_version"], "bundle_id": "bundle-" + uuid.uuid4().hex,
        "run_id": run["run_id"], "mode": run["mode"], "native_trace_ref": native_ref,
        "association_ref": association_ref, "artifacts": entries,
        "hashes": {entry["ref"]: entry["sha256"] for entry in entries}, "redactions": [],
        "missing_items": missing, "live_export_allowed": bool(run["mode"] == "live" and native_ref and association_ref),
    }
    validate("EvidenceManifest", evidence)
    output = run_dir / "evidence-manifest.json"
    from services.hydraulics.storage import write
    write(output, evidence)
    return evidence
