from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))
from waterguard_contracts.core import FIXTURES, load

from services.jobs.runner import SimulationJobManager


class JobRunnerTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-artifacts"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)

    def tearDown(self):
        self.temp.cleanup()

    def test_fixture_job_runs_in_child_process(self):
        manager = SimulationJobManager(Path(self.temp.name) / "output")
        task, plan = load(FIXTURES / "task.json"), load(FIXTURES / "candidate-a.json")
        accepted = manager.submit(task, plan, "fixture")
        self.assertEqual(accepted["status"], "accepted")
        for _ in range(80):
            result = manager.get(accepted["job_id"])
            if result["status"] != "RUNNING":
                break
            time.sleep(0.05)
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["result"]["artifact"]["candidate_id"], "candidate-a")
