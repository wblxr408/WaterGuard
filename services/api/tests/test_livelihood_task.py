"""Task proposals freeze the livelihood-first objective order only where it is meaningful."""
import unittest

import services.api.runtime  # noqa: F401  (puts the contracts package on sys.path)
from services.api.workbench import TaskProposal, propose_task

BASE = dict(threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95)
NORMAL_TIER = ['maximize_min_node_availability', 'maximize_mean_node_availability', 'minimize_shortage', 'minimize_action_cost']


class LivelihoodTaskTests(unittest.TestCase):
    def test_default_task_with_service_tier_protects_basic_service_before_normal_tier(self):
        task = propose_task(TaskProposal(**BASE))
        self.assertEqual(task['objectives'],
                         ['maximize_emergency_node_coverage', 'maximize_emergency_mean_availability'] + NORMAL_TIER)
        self.assertNotIn('service_priorities', task)

    def test_task_without_service_tier_keeps_the_normal_tier_order(self):
        task = propose_task(TaskProposal(**BASE, emergency_floor=None))
        self.assertEqual(task['objectives'], NORMAL_TIER)

    def test_declared_priority_points_are_frozen_and_ranked_first(self):
        task = propose_task(TaskProposal(**BASE, service_priorities=dict(node_ids=['22', '31'], basis='Hospital and school')))
        self.assertEqual(task['service_priorities'], dict(node_ids=['22', '31'], basis='Hospital and school'))
        self.assertEqual(task['objectives'][0], 'maximize_priority_emergency_coverage')
        self.assertEqual(task['objectives'][1], 'maximize_emergency_node_coverage')
        other = propose_task(TaskProposal(**BASE, service_priorities=dict(node_ids=['22'], basis='Hospital and school')))
        self.assertNotEqual(task['constraint_hash'], other['constraint_hash'])

    def test_unknown_priority_node_is_rejected_at_freeze_time(self):
        with self.assertRaisesRegex(ValueError, 'user nodes'):
            propose_task(TaskProposal(**BASE, service_priorities=dict(node_ids=['nope'], basis='Hospital')))

    def test_priority_points_need_the_service_tier(self):
        with self.assertRaisesRegex(ValueError, 'service tier'):
            propose_task(TaskProposal(**BASE, emergency_floor=None,
                                      service_priorities=dict(node_ids=['22'], basis='Hospital')))


if __name__ == '__main__':
    unittest.main()
