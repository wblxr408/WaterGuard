from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))
from waterguard_contracts.core import FIXTURES, load, task_hash

from services.api.runtime import ConflictError, RunStore
from services.policy.authorization import ExecutionIdentity, PermissionDenied, authorize


class PolicyTests(unittest.TestCase):
    def test_only_host_verified_role_can_call_tool(self):
        authorize(ExecutionIdentity("native-session", "planner", True), "wg_submit_plan")
        with self.assertRaises(PermissionDenied):
            authorize(ExecutionIdentity("native-session", "reviewer", True), "wg_submit_plan")
        with self.assertRaises(PermissionDenied):
            authorize(ExecutionIdentity("", "planner", False), "wg_submit_plan")

    def test_simulation_reservation_is_atomic_and_idempotent(self):
        parent = ROOT / ".test-artifacts"; parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as root:
            store = RunStore(Path(root) / "db.sqlite3", Path(root) / "artifacts")
            task = copy.deepcopy(load(FIXTURES / "task.json"))
            task["budget"]["max_simulations"] = 2
            task["budget"]["max_parallel"] = 1
            task["constraint_hash"] = task_hash(task)
            run = store.create_run(store.save_task(task), "fixture")
            first = store.reserve_simulation(run["run_id"], "request-1")
            self.assertTrue(store.reserve_simulation(run["run_id"], "request-1")["idempotent"])
            with self.assertRaisesRegex(ConflictError, "concurrency"):
                store.reserve_simulation(run["run_id"], "request-2")
            store.settle_simulation(first["reservation_id"])
            second = store.reserve_simulation(run["run_id"], "request-2")
            store.settle_simulation(second["reservation_id"])
            with self.assertRaisesRegex(ConflictError, "budget"):
                store.reserve_simulation(run["run_id"], "request-3")

    def test_gateway_job_is_persisted_and_restart_settles_reservation(self):
        parent = ROOT / ".test-artifacts"; parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as root:
            store = RunStore(Path(root) / "db.sqlite3", Path(root) / "artifacts")
            task = copy.deepcopy(load(FIXTURES / "task.json"))
            task["budget"]["max_simulations"] = 1
            task["constraint_hash"] = task_hash(task)
            run = store.create_run(store.save_task(task), "fixture")
            reservation = store.reserve_simulation(run["run_id"], "gateway:plan-1")
            created = store.create_simulation_job(run["run_id"], "job-restart-test", "candidate-a", reservation["reservation_id"])
            self.assertEqual(created["status"], "QUEUED")
            store.update_simulation_job("job-restart-test", "RUNNING")

            recovered = store.recover_simulation_jobs()

            self.assertEqual(len(recovered), 1)
            self.assertEqual(store.get_simulation_job("job-restart-test")["status"], "ERROR")
            self.assertEqual(store.get_run(run["run_id"])["budget_usage"]["simulations"], 1)
