"""First-aid-then-refine governance; isolated store fixtures, no native/model evidence is claimed."""
import tempfile
import unittest
from pathlib import Path

import services.api.runtime  # noqa: F401  (puts the contracts package on sys.path)
from services.api.runtime import RunStore
from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details
from services.api.workflow_guard import workflow_hint

ASSESSED = 92


def metrics(*, served, normal_mean=.8, normal_min=0., priority=None, violations=True):
    values = dict(min_node_time_pass_ratio=normal_min, mean_node_time_pass_ratio=normal_mean, shortage_m3=30,
                  action_cost=1, assessed_node_count=ASSESSED, nodes_reaching_emergency_service=served,
                  emergency_mean_node_time_pass_ratio=served / ASSESSED, fully_compliant_node_count=0 if violations else ASSESSED)
    if priority is not None:
        values.update(priority_node_count=priority[1], priority_nodes_reaching_emergency_service=priority[0])
    return values


class FirstAidWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = RunStore(root / 'state.sqlite3', root / 'artifacts')
        self.task = self.store.save_task(propose_task(TaskProposal(fault_kind='normal', candidate_limit=8, max_actions=2)))
        self.plans = benchmark_plans(self.task, network_details('epa-net1')['catalog'])
        self.parent = self.store.create_run(self.task, 'live')
        self.store.transition(self.parent['run_id'], 'RUNNING', 'PLANNING')
        self.store.bind_agh(self.parent['run_id'], 'isolated-first-aid', {'id': 'agnes-ai', 'model': 'test'})
        self.used = 0

    def tearDown(self):
        self.temp.cleanup()

    def spend(self, count):
        for _ in range(count):
            self.used += 1
            reservation = self.store.reserve_simulation(self.parent['run_id'], f'spend-{self.used}')
            self.store.settle_simulation(reservation['reservation_id'])

    def child(self, plan, values, *, passed=False):
        child = self.store.create_run(self.task, 'benchmark')
        self.store.link_live_child(self.parent['run_id'], child['run_id'])
        self.store._write_json(f"{child['run_id']}/submitted-plan.json", plan)
        self.store._write_json(f"{child['run_id']}/candidates/{plan['candidate_id']}/plan.json", plan)
        self.store._write_json(f"{child['run_id']}/candidates/{plan['candidate_id']}/verification.json",
            {'task_ref': child['task_ref'], 'candidate_id': plan['candidate_id'], 'validity': 'valid',
             'violations': [] if passed else [{'code': 'MIN_PRESSURE'}], 'metrics': values})
        return child

    def hint(self):
        return workflow_hint(self.store, self.store.get_run(self.parent['run_id']))

    def test_failed_baseline_starts_with_the_first_aid_candidates_in_enumeration_order(self):
        self.child(self.plans[0], metrics(served=60))
        self.spend(1)
        hint = self.hint()
        self.assertEqual(hint['required_next'], 'first_aid')
        self.assertEqual(hint['first_aid_candidate_ids'], [p['candidate_id'] for p in self.plans[1:5]])
        self.assertTrue(all(self.plans[int(c.rsplit('-', 1)[1])]['actions'] for c in hint['first_aid_candidate_ids']))

    def test_first_aid_shrinks_to_keep_a_revision_and_a_stress_slot(self):
        self.child(self.plans[0], metrics(served=60))
        self.spend(5)
        hint = self.hint()
        self.assertEqual(hint['remaining_simulations'], 3)
        self.assertEqual(hint['first_aid_candidate_ids'], [self.plans[1]['candidate_id']])

    def test_without_room_for_first_aid_the_previous_flow_is_unchanged(self):
        self.child(self.plans[0], metrics(served=60))
        self.spend(6)
        hint = self.hint()
        self.assertEqual(hint['remaining_simulations'], 2)
        self.assertEqual(hint['required_next'], 'choose_candidate')

    def test_passing_baseline_never_triggers_first_aid(self):
        self.child(self.plans[0], metrics(served=ASSESSED, violations=False), passed=True)
        self.spend(1)
        self.assertEqual(self.hint()['required_next'], 'create_perturbation')

    def after_first_aid(self, best_values, other_values=None):
        self.child(self.plans[0], metrics(served=60))
        runs = {}
        for index, plan in enumerate(self.plans[1:5]):
            values = best_values if index == 2 else (other_values or metrics(served=62, normal_mean=.9))
            runs[plan['candidate_id']] = self.child(plan, values)
        self.spend(5)
        return runs

    def test_livelihood_gap_requires_diagnosis_from_the_best_first_aid_by_frozen_order(self):
        runs = self.after_first_aid(metrics(served=78, normal_mean=.7))
        hint = self.hint()
        self.assertEqual(hint['required_next'], 'diagnose_and_revise')
        self.assertTrue(hint['refinement_required'])
        self.assertEqual(hint['livelihood_gap'], dict(unserved_nodes=14, priority_unserved_nodes=0, assessed_node_count=ASSESSED))
        self.assertEqual(hint['source_run_id'], runs[self.plans[3]['candidate_id']]['run_id'])
        self.assertEqual(hint['first_aid_candidate_ids'], [])

    def test_priority_points_without_basic_service_keep_refinement_required(self):
        self.task = self.store.save_task(propose_task(TaskProposal(fault_kind='normal', candidate_limit=8, max_actions=2,
            service_priorities=dict(node_ids=['22'], basis='Hospital'))))
        self.plans = benchmark_plans(self.task, network_details('epa-net1')['catalog'])
        self.parent = self.store.create_run(self.task, 'live')
        self.store.transition(self.parent['run_id'], 'RUNNING', 'PLANNING')
        self.store.bind_agh(self.parent['run_id'], 'isolated-priority', {'id': 'agnes-ai', 'model': 'test'})
        self.used = 0
        self.child(self.plans[0], metrics(served=60, priority=(0, 1)))
        for plan in self.plans[1:5]:
            self.child(plan, metrics(served=ASSESSED, priority=(0, 1)))
        self.spend(5)
        hint = self.hint()
        self.assertEqual(hint['required_next'], 'diagnose_and_revise')
        self.assertEqual(hint['livelihood_gap']['priority_unserved_nodes'], 1)

    def test_no_livelihood_gap_means_no_model_search_is_requested(self):
        self.after_first_aid(metrics(served=ASSESSED, normal_mean=.9), other_values=metrics(served=80))
        hint = self.hint()
        self.assertEqual(hint['required_next'], 'compare_and_archive')
        self.assertFalse(hint['refinement_required'])
        self.assertEqual(hint['livelihood_gap']['unserved_nodes'], 0)
        self.assertIn('basic service', hint['reason'])

    def test_unknown_service_metrics_do_not_suppress_refinement(self):
        self.after_first_aid(dict(min_node_time_pass_ratio=0, mean_node_time_pass_ratio=.7, shortage_m3=30, action_cost=1),
                             other_values=dict(min_node_time_pass_ratio=0, mean_node_time_pass_ratio=.6, shortage_m3=40, action_cost=1))
        hint = self.hint()
        self.assertEqual(hint['required_next'], 'diagnose_and_revise')
        self.assertIsNone(hint['livelihood_gap'])


if __name__ == '__main__':
    unittest.main()
