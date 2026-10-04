from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))
from waterguard_contracts.core import FIXTURES, check_plan, load

from services.candidate_generation.generator import generate_candidates


class GeneratorTests(unittest.TestCase):
    def test_generates_valid_bounded_plans_and_excludes_fault_link(self):
        task = load(FIXTURES / "task.json")
        catalog = load(FIXTURES / "action-catalog.json")
        candidates = generate_candidates(task, catalog, limit=8)
        self.assertEqual(candidates[0]["actions"], [])
        self.assertLessEqual(len(candidates), 8)
        for candidate in candidates:
            check_plan(task, candidate, catalog)
            self.assertNotIn(task["fault"]["link_id"], [a["link_id"] for a in candidate["actions"]])

