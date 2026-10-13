"""Declared priority service points are frozen input with independent coverage metrics."""
import copy
import unittest

import services.api.runtime  # noqa: F401  (puts the contracts package on sys.path)
from waterguard_contracts.core import (FIXTURES, _verify_result, check_task, input_hash, load, plan_hash,
                                       task_hash, task_ref)

OBJECTIVES = ['maximize_priority_emergency_coverage', 'maximize_emergency_node_coverage',
              'maximize_min_node_availability', 'minimize_shortage']


class ServicePriorityTests(unittest.TestCase):
    def setUp(self):
        self.task, self.plan, self.result, self.catalog, self.scene = [
            load(FIXTURES / n) for n in ('task.json', 'candidate-b.json', 'result-b.json', 'action-catalog.json', 'scene.json')]

    def freeze(self, *, priorities=('J1', 'J3'), emergency=True):
        task = copy.deepcopy(self.task)
        task['objectives'] = list(OBJECTIVES)
        if emergency:
            task['emergency_floor'] = dict(min_pressure_m=15, min_supply_ratio=.75, source='Explicit test assumption')
        if priorities is not None:
            task['service_priorities'] = dict(node_ids=list(priorities), basis='Dispatcher-declared hospital and school nodes')
        task['constraint_hash'] = task_hash(task)
        return task

    def report(self, task, *, first_window_pressure):
        plan, result = copy.deepcopy(self.plan), copy.deepcopy(self.result)
        plan['task_ref'] = task_ref(task)
        plan['plan_hash'] = plan_hash(plan)
        result['artifact']['task_ref'] = task_ref(task)
        result['artifact']['input_hash'] = input_hash(task, plan, result['artifact']['engine_version'])
        nodes = result['series']['frames'][-1]['nodes']
        frames = [dict(time_s=3600, duration_s=100, nodes=copy.deepcopy(nodes)),
                  dict(time_s=3700, duration_s=7100, nodes=copy.deepcopy(nodes))]
        first = next(n for n in frames[0]['nodes'] if n['node_id'] == 'J1')
        first.update(pressure_m=first_window_pressure,
                     delivered_demand_m3s=first['expected_demand_m3s'] * (.8 if first_window_pressure >= 15 else 0))
        return _verify_result(task, plan, result, self.catalog, self.scene, 'fixture', 'test', assessment_frames=frames)

    def test_priority_nodes_with_basic_service_are_counted_from_original_intervals(self):
        metrics = self.report(self.freeze(), first_window_pressure=16)['metrics']
        self.assertEqual(metrics['priority_node_count'], 2)
        self.assertEqual(metrics['priority_nodes_reaching_emergency_service'], 2)
        self.assertEqual(metrics['priority_emergency_min_node_time_pass_ratio'], 1)

    def test_priority_node_that_loses_basic_service_is_not_counted(self):
        metrics = self.report(self.freeze(), first_window_pressure=0)['metrics']
        self.assertEqual(metrics['priority_nodes_reaching_emergency_service'], 1)
        self.assertAlmostEqual(metrics['priority_emergency_min_node_time_pass_ratio'], 7100 / 7200)

    def test_no_declaration_means_no_priority_metrics(self):
        metrics = self.report(self.freeze(priorities=None), first_window_pressure=16)['metrics']
        self.assertFalse([k for k in metrics if k.startswith('priority_')])

    def test_declaration_must_name_user_nodes_without_duplicates_and_needs_the_service_tier(self):
        valid = self.freeze()
        check_task(valid, self.catalog, self.scene)
        for bad, message in [(('J9',), 'user node'), (('R1',), 'user node'), (('J1', 'J1'), 'duplicate')]:
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                check_task(self.freeze(priorities=bad), self.catalog, self.scene)
        with self.assertRaisesRegex(ValueError, 'service tier'):
            check_task(self.freeze(emergency=False), self.catalog, self.scene)

    def test_declaration_is_part_of_the_frozen_task_identity(self):
        self.assertNotEqual(self.freeze(priorities=('J1',))['constraint_hash'],
                            self.freeze(priorities=('J2',))['constraint_hash'])
        self.assertNotEqual(self.freeze(priorities=('J1',))['constraint_hash'],
                            self.freeze(priorities=None)['constraint_hash'])


if __name__ == '__main__':
    unittest.main()
