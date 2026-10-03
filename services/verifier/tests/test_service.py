from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))
from waterguard_contracts.core import FIXTURES, load

from services.verifier.service import verify


class VerifierServiceTests(unittest.TestCase):
    def test_recomputes_fixture_report(self):
        task = load(FIXTURES / "task.json")
        catalog = load(FIXTURES / "action-catalog.json")
        scene = load(FIXTURES / "scene.json")
        result = load(FIXTURES / "result-b.json")
        report = verify(task, load(FIXTURES / "candidate-b.json"), result, catalog, scene)
        self.assertEqual(report, load(FIXTURES / "verification-b.json"))
