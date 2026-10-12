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
    def test_arbitrary_growth_is_executed_against_real_expected_demands(self):
        from services.api.workbench import TaskProposal, propose_task
        from services.hydraulics.scenarios import rebind
        import pandas as pd
        task = propose_task(TaskProposal(fault_kind='normal', threshold_mode='manual', min_pressure_m=20,
            min_supply_ratio=.95, demand_increase_pct=23.75, hydraulic_options={'demand_multiplier': 1.2}))
        _, plan = physical_case('epa-net1', 'normal')
        rebind(task, plan)
        perturbation = dict(perturbation_id='real-variable-stress', candidate_id=plan['candidate_id'],
            parameters={'demand_multiplier': task['perturbation_domain']['demand_multipliers'][1]},
            required_labels=task['perturbation_domain']['required'])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            adapter = PhysicalAdapter(output)
            nominal = adapter.simulate(task, plan)
            stressed = adapter.simulate(task, plan, perturbation=perturbation)
            for result in (nominal, stressed):
                self.assertEqual(result['artifact']['status'], 'SUCCEEDED', result['diagnostics'])
            before = pd.read_csv(output / nominal['artifact']['simulation_id'] / 'expected-demand.csv', index_col=0)
            after = pd.read_csv(output / stressed['artifact']['simulation_id'] / 'expected-demand.csv', index_col=0)
            ratios = (after / before).stack().dropna()
            self.assertTrue((abs(ratios - 1.2375) < 1e-12).all())
            _, _, scene, catalog = assets('epa-net1')
            report = verify(task, plan, stressed, catalog, scene, perturbation,
                artifact_dir=output / stressed['artifact']['simulation_id'])
            self.assertEqual(report['coverage']['completed_stress_tests'], ['demand-plus-23.75pct'])
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
            report = verify(task, plan, result, catalog, scene, perturbation, artifact_dir=folder)
            self.assertEqual(report["coverage"]["completed_stress_tests"], ["demand-plus-10pct"])

    def test_nominal_demand_cannot_be_relabelled_as_ten_percent_stress(self):
        task, plan = physical_case('epa-net1', 'normal')
        from services.hydraulics.compiler import validate_perturbation
        with self.assertRaisesRegex(ValueError, 'actual demand increase'):
            validate_perturbation(task, plan, dict(perturbation_id='false-label', candidate_id=plan['candidate_id'],
                parameters={'demand_multiplier': 1}, required_labels=['demand-plus-10pct']))


if __name__ == "__main__":
    unittest.main()
