"""Business admission must use internal intervals and refuse unbound evidence."""
import tempfile
import unittest
from pathlib import Path

from services.hydraulics.compiler import assets
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.scenarios import physical_case
from services.verifier.service import verify, dense_frames
from waterguard_contracts.core import load, verify_benchmark


class DenseVerifierTests(unittest.TestCase):
    def test_stress_report_marks_actual_test_outcome_without_fabricating_recommendation(self):
        task, plan = physical_case('epa-net1', 'normal')
        perturbation = dict(perturbation_id='dense-stress-test', candidate_id=plan['candidate_id'],
            parameters={'demand_multiplier': 1.1}, required_labels=task['perturbation_domain']['required'])
        with tempfile.TemporaryDirectory() as output:
            result = PhysicalAdapter(Path(output)).simulate(task, plan, perturbation=perturbation)
            _, _, scene, catalog = assets('epa-net1')
            report = verify(task, plan, result, catalog, scene, perturbation,
                            artifact_dir=Path(output) / result['artifact']['simulation_id'])
            passed = report['validity'] == 'valid' and not report['violations']
            self.assertEqual(report['admission']['required_stress_tests_pass'], 'pass' if passed else 'fail')
            self.assertEqual(report['admission']['recommendation_eligible'], 'fail')
            self.assertNotIn('required_stress_tests_pending', report['recommendation_blockers'])

    def test_raw_interval_integration_and_tamper_rejection(self):
        task, plan = physical_case('epa-net3', 'rupture')
        with tempfile.TemporaryDirectory() as output:
            result = PhysicalAdapter(Path(output)).simulate(task, plan)
            self.assertEqual(result['artifact']['status'], 'SUCCEEDED', result['diagnostics'])
            folder = Path(output) / result['artifact']['simulation_id']
            _, _, scene, catalog = assets('epa-net3')
            report = verify(task, plan, result, catalog, scene, artifact_dir=folder)
            dense = load(folder / 'hydraulic-analysis.json')['dense_metrics']
            self.assertAlmostEqual(report['metrics']['shortage_m3'], dense['shortage_m3'], places=7)
            self.assertAlmostEqual(report['metrics']['supply_ratio'], dense['supply_ratio'], places=10)
            self.assertEqual(report['metrics']['min_pressure_m'], dense['min_pressure_m'])
            frames = dense_frames(folder, task)
            self.assertGreater(len(frames), 8)
            self.assertEqual(sum(f['duration_s'] for f in frames), 7200)
            self.assertTrue(any(v['start_s'] % 900 for v in report['violations']))
            public = verify_benchmark(task, plan, result, catalog, scene)
            self.assertNotEqual(report['metrics']['shortage_m3'], public['metrics']['shortage_m3'])
            with self.assertRaises(ValueError):
                verify(task, plan, result, catalog, scene)
            (folder / 'raw-node-pressure.csv').write_text('tampered', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                verify(task, plan, result, catalog, scene, artifact_dir=folder)

    def test_internal_low_pressure_cannot_be_hidden_by_public_samples(self):
        from waterguard_contracts.core import FIXTURES, _verify_result
        task, plan, result, catalog, scene = [load(FIXTURES / name) for name in
            ('task.json', 'candidate-b.json', 'result-b.json', 'action-catalog.json', 'scene.json')]
        public = verify(task, plan, result, catalog, scene)
        self.assertEqual(public['admission']['nominal_constraints_pass'], 'pass')
        import copy
        frames = copy.deepcopy(result['series']['frames'])
        chosen = next(f for f in frames if f['time_s'] >= task['time_window']['evaluation_start_s'])
        chosen['nodes'][0]['pressure_m'] = 0
        report = _verify_result(task, plan, result, catalog, scene, 'fixture', 'isolated-interval-test', assessment_frames=frames)
        self.assertEqual(report['admission']['nominal_constraints_pass'], 'fail')
        self.assertTrue(any(v['code'] == 'MIN_PRESSURE' for v in report['violations']))
