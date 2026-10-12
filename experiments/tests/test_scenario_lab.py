"""Synthetic diagnosis boundaries; these inputs are not Live competition evidence."""
import unittest
from experiments.scenario_lab import dominated, episodes


def violation(node, start, end, value=10, threshold=20, code='MIN_PRESSURE', unit='m'):
    return dict(node_id=node, start_s=start, end_s=end, observed=value, threshold=threshold, code=code, unit=unit)


class ScenarioLabTests(unittest.TestCase):
    def test_gap_node_and_threshold_preserve_separate_failure_episodes(self):
        rows = [violation('a', 0, 75, 12), violation('a', 75, 150, 3),
                violation('a', 300, 375, 8), violation('b', 75, 150, 0),
                violation('a', 150, 225, 4, threshold=15)]
        result = episodes(list(reversed(rows)))
        self.assertEqual(len(result), 4)
        self.assertEqual(sum(r['duration_s'] for r in result), 375)
        longest = result[0]
        self.assertEqual((longest['node_id'], longest['start_s'], longest['end_s'], longest['worst_observed']), ('a', 0, 150, 3))

    def test_overlapping_intervals_are_rejected_instead_of_double_counted(self):
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            episodes([violation('a', 0, 150), violation('a', 75, 225)])

    def test_pareto_tradeoff_is_not_an_eligibility_claim(self):
        baseline = dict(shortage_m3=100, min_pressure_m=25, action_cost=0)
        action = dict(shortage_m3=5, min_pressure_m=5, action_cost=1)
        self.assertFalse(dominated(baseline, action))
        self.assertFalse(dominated(action, baseline))
        improved = dict(shortage_m3=0, min_pressure_m=30, action_cost=0)
        self.assertTrue(dominated(baseline, improved))
        self.assertFalse(dominated(improved, improved))


if __name__ == '__main__':
    unittest.main()
