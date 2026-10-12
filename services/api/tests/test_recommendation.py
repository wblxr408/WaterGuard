"""Isolated host aggregation regression tests; these are not native Live evidence."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.api.agh import _finalize_recommendation, LiveExecutor
from services.api.runtime import RunStore
from services.api.workbench import run_results
from waterguard_contracts.core import FIXTURES, load


class RecommendationTests(unittest.TestCase):
    def test_nominal_identity_and_all_gates_agree_with_parent(self):
        self.exercise(stress_pass=True, provenance=True, expected='FEASIBLE_FOUND')

    def test_failed_stress_is_completed_evidence_and_rejects_recommendation(self):
        self.exercise(stress_pass=False, provenance=True, expected='NO_FEASIBLE_FOUND')

    def test_missing_provenance_cannot_certify_physical_success(self):
        self.exercise(stress_pass=True, provenance=False, expected='INCOMPLETE')

    def exercise(self, *, stress_pass, provenance, expected):
        with tempfile.TemporaryDirectory() as temp:
            store = RunStore(Path(temp) / 'state.db', Path(temp) / 'artifacts')
            task = store.save_task(load(FIXTURES / 'task.json'))
            parent = store.create_run(task, 'live')
            store.transition(parent['run_id'], 'RUNNING')
            nominal = store.create_run(task, 'benchmark')
            stress = store.create_run(task, 'benchmark')
            plan = load(FIXTURES / 'candidate-b.json')
            source = load(FIXTURES / 'result-b.json')
            original = load(FIXTURES / 'verification-b.json')
            for child, simulation_id in [(nominal, 'sim-nominal-isolated'), (stress, 'sim-stress-isolated')]:
                store.link_live_child(parent['run_id'], child['run_id'])
                report, result = copy.deepcopy(original), copy.deepcopy(source)
                report['simulation_id'] = simulation_id
                for part in ('artifact', 'series', 'diagnostics'):
                    result[part]['simulation_id'] = simulation_id
                if child == stress and not stress_pass:
                    report['violations'] = load(FIXTURES / 'verification-a.json')['violations']
                    report['admission']['nominal_constraints_pass'] = 'fail'
                for name, value in [('plan.json', plan), ('result.json', result), ('verification.json', report)]:
                    store._write_json(f"{child['run_id']}/candidates/{plan['candidate_id']}/{name}", value)
            store._write_json(f"{stress['run_id']}/perturbation.json", dict(perturbation_id='isolated-plus-10',
                candidate_id=plan['candidate_id'], parameters={'demand_multiplier': 1.1}, required_labels=task['perturbation_domain']['required']))
            executor = LiveExecutor(store, None)
            try:
                executor.publish_children(parent['run_id'])
                with patch('services.evidence.service._validated_agh_association', return_value=provenance):
                    summary = _finalize_recommendation(store, parent['run_id'], task, store.live_children(parent['run_id']))
                self.assertEqual(summary['conclusion'], expected)
                rows = run_results(store, parent['run_id'])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['artifact']['simulation_id'], 'sim-nominal-isolated')
                report = rows[0]['verification']
                self.assertEqual(report['coverage']['completed_stress_tests'], task['perturbation_domain']['required'])
                self.assertEqual(report['admission']['required_stress_tests_pass'], 'pass' if stress_pass else 'fail')
                self.assertEqual(report['admission']['recommendation_eligible'], 'pass' if expected == 'FEASIBLE_FOUND' else 'fail')
                self.assertEqual(store.get_run(parent['run_id'])['conclusion'], expected)
                self.assertEqual(store._read_json(store._run_directory(nominal['run_id']) / 'candidates' / plan['candidate_id'] / 'verification.json')['admission']['required_stress_tests_pass'], 'pending')
            finally:
                executor.close()
