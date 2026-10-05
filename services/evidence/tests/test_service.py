from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))
from waterguard_contracts.core import FIXTURES, load

from services.api.runtime import RunStore
from services.evidence.service import build_evidence


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / ".test-artifacts"
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fixture_evidence_exposes_missing_native_trace(self):
        store = RunStore(self.root / "state.sqlite3", self.root / "artifacts")
        task = store.save_task(load(FIXTURES / "task.json"))
        run = store.create_run(task, "fixture")
        evidence = build_evidence(self.root / "artifacts", run)
        self.assertFalse(evidence["live_export_allowed"])
        self.assertIn("native_agh_trace", evidence["missing_items"])
        self.assertGreaterEqual(len(evidence["artifacts"]), 2)

    def test_live_evidence_rejects_an_empty_or_unbound_association(self):
        store = RunStore(self.root / "state.sqlite3", self.root / "artifacts")
        task = store.save_task(load(FIXTURES / "task.json"))
        # The development store cannot create live runs; use its correctly
        # bound artifact layout to unit-test evidence validation alone.
        run = store.create_run(task, "fixture")
        run.update(mode="live", agh_session_ref="agh-session-1", model_config_ref="model-config-1")
        agh = self.root / "artifacts" / run["run_id"] / "agh"
        (agh / "native").mkdir(parents=True)
        (agh / "native" / "trace.jsonl").write_text("{}\n", encoding="utf-8")
        (agh / "association.json").write_text(json.dumps({"agh_session_ref": "other", "events": []}), encoding="utf-8")

        evidence = build_evidence(self.root / "artifacts", run)

        self.assertFalse(evidence["live_export_allowed"])
        self.assertIsNone(evidence["association_ref"])
        self.assertIn("agh_event_association", evidence["missing_items"])
