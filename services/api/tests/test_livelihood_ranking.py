"""Livelihood-first ranking is a consumer-visible ordering of verified candidates."""
import unittest

import services.api.runtime  # noqa: F401  (puts the contracts package on sys.path)
from services.hydraulics.configuration import ranking_key

LIVELIHOOD = ['maximize_priority_emergency_coverage', 'maximize_emergency_node_coverage',
              'maximize_emergency_mean_availability', 'maximize_min_node_availability',
              'maximize_mean_node_availability', 'minimize_shortage', 'minimize_action_cost']


def candidate(cid, *, coverage, emergency_mean, normal_min=0, normal_mean=.8, shortage=10, priority=None, cost=1):
    metrics = dict(nodes_reaching_emergency_service=coverage, emergency_mean_node_time_pass_ratio=emergency_mean,
                   min_node_time_pass_ratio=normal_min, mean_node_time_pass_ratio=normal_mean,
                   shortage_m3=shortage, action_cost=cost)
    if priority is not None:
        metrics['priority_nodes_reaching_emergency_service'] = priority
    return dict(candidate_id=cid, metrics=metrics)


class LivelihoodRankingTests(unittest.TestCase):
    task = dict(objectives=LIVELIHOOD)

    def order(self, *reports):
        return [r['candidate_id'] for r in sorted(reports, key=lambda r: ranking_key(self.task, r))]

    def test_more_nodes_with_basic_service_beat_a_higher_normal_average(self):
        broad = candidate('broad', coverage=76, emergency_mean=.93, normal_mean=.86)
        polished = candidate('polished', coverage=70, emergency_mean=.90, normal_mean=.95)
        self.assertEqual(self.order(polished, broad), ['broad', 'polished'])

    def test_fully_compliant_candidate_outranks_partial_rescue(self):
        feasible = candidate('feasible', coverage=92, emergency_mean=1, normal_min=1, normal_mean=1, shortage=0)
        rescue = candidate('rescue', coverage=80, emergency_mean=.97, normal_min=0, normal_mean=.9)
        self.assertEqual(self.order(rescue, feasible), ['feasible', 'rescue'])

    def test_equal_service_coverage_falls_back_to_normal_tier(self):
        a = candidate('a', coverage=76, emergency_mean=.93, normal_min=.2, normal_mean=.86)
        b = candidate('b', coverage=76, emergency_mean=.93, normal_min=.1, normal_mean=.92)
        self.assertEqual(self.order(b, a), ['a', 'b'])

    def test_declared_priority_nodes_come_before_everything_else(self):
        protects_priority = candidate('priority', coverage=60, emergency_mean=.80, priority=5)
        protects_more_nodes = candidate('more', coverage=80, emergency_mean=.95, priority=3)
        self.assertEqual(self.order(protects_more_nodes, protects_priority), ['priority', 'more'])

    def test_candidate_missing_a_required_metric_ranks_last(self):
        complete = candidate('complete', coverage=10, emergency_mean=.1)
        missing = dict(candidate_id='missing', metrics=dict(shortage_m3=0, action_cost=0, min_node_time_pass_ratio=1,
                                                             mean_node_time_pass_ratio=1))
        self.assertEqual(self.order(missing, complete), ['complete', 'missing'])

    def test_existing_normal_tier_order_is_unchanged_without_emergency_objectives(self):
        task = dict(objectives=['maximize_min_node_availability', 'maximize_mean_node_availability', 'minimize_shortage'])
        a = candidate('a', coverage=1, emergency_mean=0, normal_min=.8, normal_mean=.95)
        b = candidate('b', coverage=99, emergency_mean=1, normal_min=.9, normal_mean=.94)
        self.assertLess(ranking_key(task, b), ranking_key(task, a))


if __name__ == '__main__':
    unittest.main()
