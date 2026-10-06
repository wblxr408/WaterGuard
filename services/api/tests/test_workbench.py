from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from services.api.main import create_app
from services.api.runtime import RunStore
from services.api.workbench import TaskProposal, benchmark_plans, network_details, propose_task
from waterguard_contracts.core import task_hash


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[3] / ".test-artifacts"
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        folder = Path(self.temp.name)
        self.store = RunStore(folder / "state.sqlite3", folder / "artifacts")
        self.app = create_app(self.store)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def task(self, **parameters):
        response = self.client.post("/api/tasks/from-proposal", json=parameters)
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        self.assertEqual(task_hash(task), task["constraint_hash"])
        return task

    def start(self, task, key="test-run"):
        response = self.client.post(f"/api/tasks/{task['task_id']}/runs", json={"mode": "benchmark", "idempotency_key": key})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["run_id"]

    def wait(self, run_id, statuses=("COMPLETED", "ERROR", "CANCELLED")):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            run = self.client.get(f"/api/runs/{run_id}").json()
            if run["status"] in statuses:
                return run
            time.sleep(.05)
        self.fail("Benchmark did not finish within 45 seconds")

    def test_single_frontend_redirect_network_and_server_frozen_task(self):
        page = self.client.get("/?run=run-existing", follow_redirects=False)
        self.assertEqual(page.status_code, 307)
        self.assertEqual(page.headers['location'], 'http://127.0.0.1:5173/?run=run-existing')
        self.assertEqual(self.client.get('/assets/workbench.js').status_code, 404)
        self.assertEqual(self.client.get('/assets/workbench.css').status_code, 404)
        self.assertEqual(self.client.get('/healthz').status_code, 200)
        details = self.client.get('/api/networks/epa-net1').json()
        self.assertEqual(len(details['scene']['nodes']), 11)
        self.assertIn('12', details['fault_link_ids'])
        task = self.task(min_pressure_m=20, min_supply_ratio=.95, candidate_limit=1)
        stored = self.store.get_task(task['task_id'])
        self.assertEqual(task_hash(stored), stored['constraint_hash'])
        # Client values are submission parameters; frozen numeric encoding stays on the server.
        roundtripped = json.loads(json.dumps(task), parse_float=lambda n: int(float(n)) if float(n).is_integer() else float(n))
        self.assertNotEqual(task_hash(roundtripped), task['constraint_hash'])
        bad = self.client.post('/api/tasks/from-proposal', json={'link_id':'9'})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.get('/api/networks/unknown').status_code, 404)
        self.assertEqual(self.client.post('/api/tasks/from-proposal', json={'candidate_limit':0}).status_code, 422)

    def test_real_benchmark_pipeline_and_idempotent_reread(self):
        for network, fault in [('epa-net1','closure'), ('epa-net3','rupture'), ('china-zhijiang','closure')]:
            with self.subTest(network=network):
                task = self.task(network_id=network, fault_kind=fault, candidate_limit=1, threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95)
                run_id = self.start(task)
                run = self.wait(run_id)
                error = self.client.get(f'/api/runs/{run_id}/error')
                self.assertEqual(run['status'], 'COMPLETED', error.text)
                self.assertEqual(run['conclusion'], 'INCOMPLETE')
                self.assertEqual(run['budget_usage']['simulations'], 1)
                result = self.client.get(f'/api/runs/{run_id}/results').json()[0]
                self.assertEqual(result['artifact']['status'], 'SUCCEEDED')
                self.assertEqual(result['verification']['admission']['simulation_valid'], 'pass')
                self.assertEqual(result['verification']['admission']['recommendation_eligible'], 'fail')
                series = self.client.get(f"/api/simulations/{result['artifact']['simulation_id']}/series").json()
                self.assertEqual(len(series['frames']), 12)
                self.assertEqual(self.start(task), run_id)
                # Close the executor to wait for final atomic evidence publication.
        self.app.state.benchmark_executor.close()
        runs = self.client.get('/api/runs').json()
        self.assertEqual(len(runs), 3)
        for run in runs:
            evidence = self.client.get(f"/api/runs/{run['run_id']}/evidence").json()
            self.assertGreater(len(evidence['artifacts']), 20)
            self.assertFalse(evidence['live_export_allowed'])
            events = self.store.events_after(run['run_id'])
            disk = [json.loads(line) for line in (self.store.artifacts/run['run_id']/'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(events, disk)
            self.assertTrue(any(e['type']=='verification.completed' for e in events))

    def test_cancel_reaches_adapter_and_cannot_be_overwritten(self):
        entered, stopped = threading.Event(), threading.Event()
        def simulate(_adapter, _task, _plan, *, timeout_s, cancel_event):
            entered.set()
            self.assertTrue(cancel_event.wait(5))
            stopped.set()
            raise RuntimeError('cancelled worker test')
        with patch('services.hydraulics.runtime.PhysicalAdapter.simulate', simulate):
            task = self.task(candidate_limit=1, threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95)
            run_id = self.start(task)
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.client.post(f'/api/runs/{run_id}/cancel').json()['status'], 'CANCELLED')
            self.assertTrue(stopped.wait(5))
            self.app.state.benchmark_executor.close()
            self.assertEqual(self.client.get(f'/api/runs/{run_id}').json()['status'],'CANCELLED')
            self.assertEqual(self.client.post(f'/api/runs/{run_id}/cancel').json()['status'],'CANCELLED')
            self.assertEqual(self.store.get_evidence(run_id)['mode'],'benchmark')
            self.assertEqual(self.store._read_json(self.store.artifacts/run_id/'run.json'), self.store.get_run(run_id))
            self.assertGreater(self.store.get_run(run_id)['budget_usage']['wall_time_s'], 0)
            self.assertIn('required_stress_tests', self.store.get_evidence(run_id)['missing_items'])

    def test_worker_error_is_visible_and_restart_is_explicit(self):
        with patch('services.hydraulics.runtime.PhysicalAdapter.simulate', side_effect=RuntimeError('engine unavailable')):
            run_id = self.start(self.task(candidate_limit=1, threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95))
            self.assertEqual(self.wait(run_id)['status'], 'ERROR')
            self.assertIn('engine unavailable', self.client.get(f'/api/runs/{run_id}/error').json()['message'])
        pending = self.store.create_run(self.store.save_task(propose_task(TaskProposal(candidate_limit=1))), 'benchmark')
        self.app.state.benchmark_executor.recover()
        self.assertEqual(self.store.get_run(pending['run_id'])['status'], 'ERROR')
        self.assertEqual(self.store._read_json(self.store.artifacts/pending['run_id']/'error.json')['code'],'SERVER_RESTARTED')

    def test_candidate_limit_is_respected_and_fault_cannot_be_operated(self):
        for limit in (1,3,6):
            task = propose_task(TaskProposal(candidate_limit=limit))
            plans = benchmark_plans(task, network_details('epa-net1')['catalog'])
            self.assertEqual(len(plans), limit)
            self.assertEqual(plans[0]['actions'], [])
            self.assertTrue(all(a['link_id'] != task['fault']['link_id'] for p in plans for a in p['actions']))

    def test_adaptive_and_manual_thresholds_freeze_and_drive_visual_workspace(self):
        details = self.client.get('/api/networks/china-zhijiang').json()
        recommendation = details['threshold_recommendation']
        self.assertTrue(recommendation['available'])
        automatic = self.task(network_id='china-zhijiang', fault_kind='normal', candidate_limit=1)
        self.assertEqual(automatic['hard_constraints']['min_pressure_m'], recommendation['min_pressure_m'])
        manual = self.task(network_id='china-zhijiang', fault_kind='normal', candidate_limit=1,
                           threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95)
        evaluated_frames = []
        for task, expected in [(automatic, 'pass'), (manual, 'fail')]:
            run_id = self.start(task)
            self.assertEqual(self.wait(run_id)['status'], 'COMPLETED')
            # Packaging follows terminal state publication; wait for the complete view.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                response = self.client.get(f'/api/runs/{run_id}/workspace')
                if response.status_code == 200: break
                time.sleep(.05)
            self.assertEqual(response.status_code, 200, response.text)
            workspace = response.json()
            candidate = workspace['candidates'][0]
            self.assertEqual(candidate['verification']['admission']['nominal_constraints_pass'], expected)
            self.assertEqual(candidate['task']['constraint_hash'], task['constraint_hash'])
            self.assertEqual(len(candidate['series']['frames'][0]['nodes']), 113)
            evaluated_frames.append(candidate['series']['frames'])
        from services.hydraulics.replay import compare_frames
        comparison = compare_frames(*evaluated_frames)
        self.assertTrue(comparison['numerically_equivalent'], comparison)
        self.assertEqual(self.store.get_task(automatic['task_id']), automatic)
        for fields in [dict(threshold_mode='manual'), dict(threshold_mode='manual', min_pressure_m=10),
                       dict(threshold_mode='adaptive', min_pressure_m=10), dict(threshold_mode='manual', min_pressure_m=-1, min_supply_ratio=.8)]:
            self.assertEqual(self.client.post('/api/tasks/from-proposal', json=fields).status_code, 422)
