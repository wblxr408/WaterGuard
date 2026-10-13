"""Strategy rows are scored by the same frozen order and never invent a gain."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'experiments'))
sys.path.insert(0, str(ROOT / 'packages/contracts'))
import livelihood_compare as comparison  # noqa: E402
from livelihood_compare import best_of, strategy_row  # noqa: E402

TASK = dict(objectives=['maximize_emergency_node_coverage', 'maximize_emergency_mean_availability',
                        'maximize_min_node_availability', 'maximize_mean_node_availability', 'minimize_shortage'])


def record(cid, served, shortage, *, valid=True, actions=1):
    return dict(plan=dict(candidate_id=cid, actions=[{}] * actions), report=dict(
        validity='valid' if valid else 'invalid', violations=[1], candidate_id=cid,
        metrics=dict(nodes_reaching_emergency_service=served, assessed_node_count=10, shortage_m3=shortage,
                     emergency_mean_node_time_pass_ratio=served / 10, min_node_time_pass_ratio=0, mean_node_time_pass_ratio=.5,
                     emergency_min_node_time_pass_ratio=0, fully_compliant_node_count=1)))


class StrategyRowTests(unittest.TestCase):
    def test_best_is_chosen_by_the_livelihood_first_order_and_invalid_runs_are_ignored(self):
        records = [record('base', 4, 30, actions=0), record('a', 6, 20), record('b', 8, 25), record('bad', 10, 1, valid=False)]
        self.assertEqual(best_of(TASK, records)['plan']['candidate_id'], 'b')

    def test_row_reports_served_nodes_and_shortage_against_the_same_baseline(self):
        base, best = record('base', 4, 30, actions=0), record('b', 8, 25)
        row = strategy_row('first_aid', TASK, best, simulations=4, model_calls=0, wall_time_s=12.5, baseline=base)
        self.assertEqual((row['served_nodes_vs_baseline'], row['shortage_reduction_m3']), (4, 5))
        self.assertEqual((row['simulations'], row['model_calls']), (4, 0))

    def test_no_valid_candidate_gives_an_empty_row_not_zeros(self):
        row = strategy_row('hybrid', TASK, best_of(TASK, [record('bad', 1, 1, valid=False)]), simulations=3, model_calls=9, wall_time_s=1)
        self.assertIsNone(row['best'])
        self.assertNotIn('served_nodes_vs_baseline', row)

    def test_hybrid_attributes_the_actual_delivery_and_preserves_zero_gain(self):
        task = {**TASK, 'task_id': 'frozen', 'revision': 1, 'network_ref': {'sha256': 'network'}, 'constraint_hash': 'same'}
        run = dict(run_id='run-real', task_ref=dict(task_id='frozen', revision=1, network_hash='network', constraint_hash='same'),
                   status='COMPLETED', conclusion='BEST_EFFORT_MITIGATION',
                   budget_usage=dict(simulations=8, model_calls=39, wall_time_s=602))
        first, tried = record('first', 8, 25), record('refined', 7, 30)
        gain = dict(improved=False, nodes_reaching_emergency_service=0, shortage_reduction_m3=0,
                    raw_difference=dict(nodes_reaching_emergency_service=-1))
        summary = dict(mitigation_candidate_id='first', selected_candidate_id=None,
                       delivery_source='first_aid', refinement_gain=gain)
        row = comparison.hybrid_row(task, [record('base', 4, 30, actions=0), first, tried], run, summary)
        self.assertEqual(row['candidate_id'], 'first')
        self.assertEqual(row['delivery_source'], 'first_aid')
        self.assertEqual(row['refinement_gain']['raw_difference']['nodes_reaching_emergency_service'], -1)
        self.assertEqual((row['simulations'], row['model_calls']), (8, 39))

    def test_failed_hybrid_retains_error_and_usage_without_inventing_a_delivery(self):
        task = {**TASK, 'task_id': 'frozen', 'revision': 1, 'network_ref': {'sha256': 'network'}, 'constraint_hash': 'same'}
        run = dict(run_id='run-error', task_ref=dict(task_id='frozen', revision=1, network_hash='network', constraint_hash='same'),
                   status='ERROR', conclusion='INCOMPLETE', error=dict(code='INTERNAL_ERROR'),
                   budget_usage=dict(simulations=1, model_calls=8, wall_time_s=70))
        row = comparison.hybrid_row(task, [], run, {})
        self.assertIsNone(row['best'])
        self.assertEqual(row['status'], 'ERROR')
        self.assertEqual(row['error']['code'], 'INTERNAL_ERROR')
        self.assertIsNone(row['delivery_source'])

    def test_hybrid_rejects_comparing_a_different_frozen_task(self):
        task = {**TASK, 'task_id': 'frozen', 'revision': 1, 'network_ref': {'sha256': 'network'}, 'constraint_hash': 'same'}
        run = dict(task_ref=dict(task_id='other', revision=1, network_hash='network', constraint_hash='same'), budget_usage={})
        with self.assertRaisesRegex(ValueError, 'same frozen task'):
            comparison.hybrid_row(task, [], run, {})


if __name__ == '__main__':
    unittest.main()
