"""Relative improvement counts the basic-service tier, and regressions in either tier are visible."""
import unittest

import services.api.runtime  # noqa: F401  (puts the contracts package on sys.path)
from services.verifier.mitigation import mitigation_record

OBJECTIVES = ['maximize_emergency_node_coverage', 'maximize_emergency_mean_availability',
              'maximize_min_node_availability', 'maximize_mean_node_availability', 'minimize_shortage']
TASK = dict(objectives=OBJECTIVES, emergency_floor=dict(min_pressure_m=15, min_supply_ratio=.75))


def report(cid, normal, emergency, shortage=10):
    """normal / emergency are lists of seconds served out of 100 per node."""
    rows = [dict(node_id=f'J{i}', assessed_duration_s=100, normal_supply_duration_s=n, normal_supply_time_ratio=n / 100,
                 emergency_supply_duration_s=e, emergency_supply_time_ratio=e / 100)
            for i, (n, e) in enumerate(zip(normal, emergency))]
    ratios = [e / 100 for e in emergency]
    return dict(candidate_id=cid, validity='valid', task_ref='t', coverage='same', violations=[1],
                metrics=dict(node_availability=rows, shortage_m3=shortage, assessed_node_count=len(rows),
                             min_node_time_pass_ratio=min(normal) / 100, mean_node_time_pass_ratio=sum(normal) / len(normal) / 100,
                             fully_compliant_node_count=sum(1 for n in normal if n == 100),
                             emergency_min_node_time_pass_ratio=min(ratios), emergency_mean_node_time_pass_ratio=sum(ratios) / len(ratios),
                             nodes_reaching_emergency_service=sum(1 for e in emergency if e == 100)))


class EmergencyAwareImprovementTests(unittest.TestCase):
    def test_gain_only_in_basic_service_time_is_an_improvement(self):
        base = report('base', normal=[0, 0, 100], emergency=[40, 100, 100])
        better = report('better', normal=[0, 0, 100], emergency=[90, 100, 100])
        record = mitigation_record(TASK, base, better)
        self.assertTrue(record['improves_baseline'])
        self.assertTrue(record['any_emergency_improved'])
        self.assertFalse(record['any_node_improved'])
        self.assertEqual([r['emergency_delta_s'] for r in record['per_node']], [50, 0, 0])

    def test_regression_in_basic_service_is_flagged_even_if_normal_tier_is_untouched(self):
        base = report('base', normal=[0, 0, 100], emergency=[40, 100, 100])
        mixed = report('mixed', normal=[10, 0, 100], emergency=[90, 60, 100])
        record = mitigation_record(TASK, base, mixed)
        self.assertTrue(record['any_emergency_regressed'])
        self.assertFalse(record['any_node_regressed'])

    def test_no_change_in_either_tier_is_not_an_improvement(self):
        base = report('base', normal=[0, 50, 100], emergency=[40, 100, 100])
        same = report('same', normal=[0, 50, 100], emergency=[40, 100, 100])
        record = mitigation_record(TASK, base, same)
        self.assertFalse(record['improves_baseline'])
        self.assertFalse(record['any_emergency_improved'])

    def test_normal_tier_only_task_keeps_previous_behaviour(self):
        task = dict(objectives=['maximize_min_node_availability', 'maximize_mean_node_availability'])
        base = report('base', normal=[0, 50], emergency=[0, 50])
        better = report('better', normal=[20, 50], emergency=[0, 50])
        for r in (base, better):
            for row in r['metrics']['node_availability']:
                row.pop('emergency_supply_duration_s'), row.pop('emergency_supply_time_ratio')
        record = mitigation_record(task, base, better)
        self.assertTrue(record['improves_baseline'])
        self.assertFalse(record['any_emergency_improved'])
        self.assertFalse(record['any_emergency_regressed'])


if __name__ == '__main__':
    unittest.main()
