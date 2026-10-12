"""Build an explicit evidence inventory; never invent native AGH records."""

from __future__ import annotations

import sys
import uuid
import re
import os
import tempfile
import zipfile
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
    if not isinstance(run.get("agh_session_ref"), str) or not run["agh_session_ref"]:
        return False
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
    # Run records begin with conservative placeholders.  Resolve provenance
    # placeholders from the files that have actually been persisted instead of
    # carrying a stale "missing" claim into a later evidence export.
    missing = list(run.get("missing_items", []))
    terminal = run.get('status') in {'COMPLETED', 'ERROR', 'CANCELLED', 'BUDGET_EXHAUSTED', 'SUPERSEDED'}
    missing = [item for item in missing if item != 'execution_in_progress']
    if not terminal:
        missing.append('execution_in_progress')
    if native_ref is not None:
        missing = [item for item in missing if item != "native_agh_trace"]
    if association_ref is not None:
        missing = [item for item in missing if item != "agh_event_association"]
    if run.get("agh_session_ref") and run.get("model_config_ref"):
        missing = [item for item in missing if item != "agh_host_not_configured"]
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
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file() and p != run_dir / "evidence-manifest.json"):
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
        "live_export_allowed": bool(terminal and run["mode"] == "live" and run.get("agh_session_ref") and run.get("model_config_ref") and native_ref and association_ref),
    }
    validate("EvidenceManifest", evidence)
    output = run_dir / "evidence-manifest.json"
    from services.hydraulics.storage import write
    write(output, evidence)
    return evidence


def export_evidence(artifacts_root: Path, run_id: str) -> Path:
    """Export exactly the hash-checked run inventory, with an atomic ZIP publish."""
    from waterguard_contracts.core import load
    if not re.fullmatch(r"run-[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid run identifier")
    root = Path(artifacts_root).resolve()
    run_dir = (root / run_id).resolve()
    if not run_dir.is_relative_to(root):
        raise ValueError("Evidence path escapes storage")
    manifest = load(run_dir / "evidence-manifest.json")
    validate("EvidenceManifest", manifest)
    if manifest["run_id"] != run_id:
        raise ValueError("Evidence run binding mismatch")
    exports = root / "exports"
    exports.mkdir(exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=exports, suffix=".zip")
    os.close(descriptor)
    target = exports / f"{run_id}-{manifest['bundle_id']}.zip"
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in manifest["artifacts"]:
                relative = entry["ref"].removeprefix("artifact:")
                path = (run_dir / relative).resolve()
                if not path.is_relative_to(run_dir) or not path.is_file():
                    raise ValueError("Evidence contains an unsafe or missing file")
                payload = path.read_bytes()
                if digest_bytes(payload) != entry["sha256"] or manifest["hashes"].get(entry["ref"]) != entry["sha256"]:
                    raise ValueError(f"Evidence hash mismatch: {relative}")
                archive.writestr(relative, payload)
            archive.write(run_dir / "evidence-manifest.json", "evidence-manifest.json")
        os.replace(temporary, target)
        return target
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
