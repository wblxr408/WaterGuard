import copy
import tempfile
import unittest
from pathlib import Path

from services.hydraulics.compiler import assets
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.scenarios import physical_case, rebind
from services.hydraulics.thresholds import apply, derive, recommend
from waterguard_contracts.core import verify_benchmark


class ThresholdTests(unittest.TestCase):
    def test_derivation_excludes_warmup_and_rejects_invalid_reference(self):
        def node(p, d=1, q=.8):
            return dict(pressure_m=p, expected_demand_m3s=d, delivered_demand_m3s=q)
        window = dict(evaluation_start_s=3600, end_s=7200)
        series = dict(frames=[dict(time_s=0, nodes=[node(1)]), dict(time_s=3600, nodes=[node(10), node(20, 0, 0)])])
        values = derive(series, window)
        self.assertEqual(values['min_pressure_m'], 9.5)
        self.assertEqual(values['min_supply_ratio'], .76)
        for bad in [node(-1), node(float('nan')), node(10, 1, 0)]:
            with self.assertRaises(ValueError):
                derive(dict(frames=[dict(time_s=3600, nodes=[bad])]), window)

    def test_real_baseline_passes_and_rupture_remains_visible_without_changing_physics(self):
        policy = recommend('china-zhijiang')
        self.assertEqual(policy['min_pressure_m'], 12.87)
        self.assertLess(policy['min_supply_ratio'], .95)
        _, _, scene, catalog = assets('china-zhijiang')
        with tempfile.TemporaryDirectory() as folder:
            adapter = PhysicalAdapter(Path(folder))
            thresholds = []
            for kind, expected in [('normal', 'pass'), ('rupture', 'fail')]:
                task, plan = physical_case('china-zhijiang', kind)
                physics = copy.deepcopy(task['hydraulic_options'])
                apply(task, policy)
                rebind(task, plan)
                self.assertEqual(task['hydraulic_options'], physics)
                result = adapter.simulate(task, plan)
                self.assertEqual(result['artifact']['status'], 'SUCCEEDED', result['diagnostics'])
                report = verify_benchmark(task, plan, result, catalog, scene)
                self.assertEqual(report['admission']['nominal_constraints_pass'], expected)
                thresholds.append(task['hard_constraints'])
            self.assertEqual(thresholds[0], thresholds[1])
