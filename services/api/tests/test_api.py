from __future__ import annotations

import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))
from waterguard_contracts.core import FIXTURES, load

from services.api.main import create_app
from services.api.runtime import RunStore
from services.evidence.service import build_evidence


class ApiTests(unittest.TestCase):
    def setUp(self):
        sandbox_temp = ROOT / ".test-artifacts"
        sandbox_temp.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=sandbox_temp)
        root = Path(self.temp.name)
        self.client = TestClient(create_app(RunStore(root / "state.sqlite3", root / "artifacts")))
        self.task = load(FIXTURES / "task.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_task_fixture_run_events_and_idempotent_cancel(self):
        self.assertEqual(self.client.post("/api/tasks", json=self.task).status_code, 201)
        first = self.client.post(f"/api/tasks/{self.task['task_id']}/runs", json={"mode": "fixture", "idempotency_key": "same"})
        self.assertEqual(first.status_code, 202)
        second = self.client.post(f"/api/tasks/{self.task['task_id']}/runs", json={"mode": "fixture", "idempotency_key": "same"})
        self.assertEqual(first.json()["run_id"], second.json()["run_id"])
        run_id = first.json()["run_id"]
        events = self.client.get(f"/api/runs/{run_id}/events")
        self.assertEqual(events.status_code, 200)
        self.assertIn("id: 1", events.text)
        cancelled = self.client.post(f"/api/runs/{run_id}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        again = self.client.post(f"/api/runs/{run_id}/cancel")
        self.assertEqual(again.status_code, 200)
        resumed = self.client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "1"})
        self.assertIn("id: 2", resumed.text)
        self.assertNotIn("id: 1", resumed.text)

    def test_live_run_is_rejected_without_agh_host(self):
        self.client.post("/api/tasks", json=self.task)
        with patch('services.api.agh.bridge', side_effect=RuntimeError('AGH daemon unavailable')):
            response = self.client.post(f"/api/tasks/{self.task['task_id']}/runs", json={"mode": "live"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "AGH_UNAVAILABLE")

    def test_rejects_altered_constraint_hash(self):
        broken = dict(self.task)
        broken["objectives"] = ["minimize_action_cost"]
        response = self.client.post("/api/tasks", json=broken)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "INPUT_INVALID")

    def test_reads_candidates_series_and_evidence_from_artifacts(self):
        store = RunStore(Path(self.temp.name) / "second.sqlite3", Path(self.temp.name) / "second-artifacts")
        client = TestClient(create_app(store))
        task = store.save_task(self.task)
        run = store.create_run(task, "fixture")
        candidate = load(FIXTURES / "candidate-b.json")
        store._write_json(f"{run['run_id']}/candidates/{candidate['candidate_id']}/plan.json", candidate)
        result = load(FIXTURES / "result-b.json")
        simulation = result["artifact"]["simulation_id"]
        store._write_json(f"{run['run_id']}/simulations/{simulation}/series.json", result["series"])
        build_evidence(store.artifacts, run)
        self.assertEqual(client.get(f"/api/runs/{run['run_id']}/candidates").json()[0]["candidate_id"], "candidate-b")
        self.assertEqual(client.get(f"/api/simulations/{simulation}/series", params={"node_id": "J1"}).status_code, 200)
        self.assertEqual(client.get(f"/api/runs/{run['run_id']}/evidence").json()["run_id"], run["run_id"])

