from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.hydraulics.compiler import assets
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.scenarios import physical_case
from services.verifier.service import verify
from waterguard_contracts.core import digest, input_hash, load


class PerturbationTests(unittest.TestCase):
    def test_out_of_domain_perturbation_is_rejected_before_allocating_artifacts(self):
        task, plan = physical_case("epa-net1", "closure")
        perturbation = {"perturbation_id": "pert-test", "candidate_id": plan["candidate_id"],
                        "parameters": {"demand_multiplier": 999},
                        "required_labels": task["perturbation_domain"]["required"]}
        with tempfile.TemporaryDirectory() as directory:
            adapter = PhysicalAdapter(Path(directory))
            with self.assertRaisesRegex(ValueError, "outside declared domain"):
                adapter.simulate(task, plan, perturbation=perturbation)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_demand_perturbation_is_bound_to_the_real_physical_input(self):
        task, plan = physical_case("epa-net1", "closure")
        parameters = {"demand_multiplier": 1.1}
        perturbation = {
            "perturbation_id": "pert-" + digest({"task_ref": plan["task_ref"], "candidate_id": plan["candidate_id"], "parameters": parameters})[7:23],
            "candidate_id": plan["candidate_id"], "parameters": parameters,
            "required_labels": task["perturbation_domain"]["required"],
        }
        with tempfile.TemporaryDirectory() as directory:
            result = PhysicalAdapter(Path(directory)).simulate(task, plan, perturbation=perturbation)
            self.assertEqual(result["artifact"]["status"], "SUCCEEDED", result["diagnostics"])
            self.assertEqual(result["artifact"]["perturbation_id"], perturbation["perturbation_id"])
            self.assertEqual(result["artifact"]["input_hash"], input_hash(task, plan, result["artifact"]["engine_version"], perturbation))
            self.assertNotEqual(result["artifact"]["input_hash"], input_hash(task, plan, result["artifact"]["engine_version"]))
            folder = Path(directory) / result["artifact"]["simulation_id"]
            self.assertEqual(load(folder / "input-manifest.json")["perturbation"], perturbation)
            self.assertEqual(load(folder / "entity-map.json")["perturbation"], perturbation)
            _wn, _source, scene, catalog = assets("epa-net1")
            report = verify(task, plan, result, catalog, scene, perturbation)
            self.assertEqual(report["coverage"]["completed_stress_tests"], ["demand-plus-10pct"])


if __name__ == "__main__":
    unittest.main()
