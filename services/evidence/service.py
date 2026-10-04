"""Build an explicit evidence inventory; never invent native AGH records."""

from __future__ import annotations

import sys
import uuid
import re
import os
import tempfile
import zipfile
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
    native_ref = "artifact:agh/native" if native.is_dir() and any(p.is_file() and p.stat().st_size for p in native.rglob("*")) else None
    association = run_dir / "agh" / "association.json"
    association_ref = "artifact:agh/association.json" if association.is_file() else None
    missing = list(run.get("missing_items", []))
    if native_ref is None:
        if "native_agh_trace" not in missing:
            missing.append("native_agh_trace")
    if association_ref is None:
        missing.append("agh_event_association")
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
        "missing_items": missing, "live_export_allowed": False,
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
