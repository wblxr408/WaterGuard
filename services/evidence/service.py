"""Build an explicit evidence inventory; never invent native AGH records."""

from __future__ import annotations

import sys
import uuid
import re
import json
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


def _validated_agh_association(path: Path, run: dict) -> bool:
    """Validate the minimum join between AGH-native and application events.

    A mere directory and a JSON file are not provenance.  A live export needs
    a non-empty native payload and at least one explicit association carrying
    the exact AGH session recorded by the run.  The host writes this compact
    interchange file after each native tool call; its referenced files are
    independently inventory-hashed below.
    """
    native = path.parent / "native"
    payloads = [item for item in native.rglob("*") if item.is_file() and item.stat().st_size > 0]
    if not payloads or not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict) or value.get("agh_session_ref") != run.get("agh_session_ref"):
        return False
    events = value.get("events")
    if not isinstance(events, list) or not events:
        return False
    return all(isinstance(event, dict)
               and isinstance(event.get("application_event_id"), str) and event["application_event_id"]
               and isinstance(event.get("native_tool_call_ref"), str) and event["native_tool_call_ref"]
               for event in events)


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
    native_ref = "artifact:agh/native" if native.is_dir() and any(item.is_file() and item.stat().st_size > 0 for item in native.rglob("*")) else None
    association = run_dir / "agh" / "association.json"
    association_ref = "artifact:agh/association.json" if _validated_agh_association(association, run) else None
    missing = list(run.get("missing_items", []))
    if native_ref is None:
        if "native_agh_trace" not in missing:
            missing.append("native_agh_trace")
    if association_ref is None:
        if "agh_event_association" not in missing:
            missing.append("agh_event_association")
    if run["mode"] == "live" and not run.get("model_config_ref"):
        if "model_configuration" not in missing:
            missing.append("model_configuration")
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
        "missing_items": missing,
        "live_export_allowed": bool(run["mode"] == "live" and run.get("model_config_ref") and native_ref and association_ref),
    }
    validate("EvidenceManifest", evidence)
    output = run_dir / "evidence-manifest.json"
    from services.hydraulics.storage import write
    write(output, evidence)
    return evidence
