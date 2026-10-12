"""Isolated store fixtures test governance; no native/model evidence is claimed."""
import tempfile
import unittest
from pathlib import Path
from services.api.runtime import RunStore, ConflictError
from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details
from services.api.workflow_guard import workflow_hint, reviews


class WorkflowGuardTests(unittest.TestCase):
    def test_stress_hint_uses_frozen_fractional_growth_and_nominal_multiplier(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            store = RunStore(folder / 'state.sqlite3', folder / 'artifacts')
            task = propose_task(TaskProposal(fault_kind='normal', threshold_mode='manual', min_pressure_m=20,
                min_supply_ratio=.95, demand_increase_pct=23.75, hydraulic_options={'demand_multiplier': 1.2}))
            store.save_task(task)
            parent = store.create_run(task, 'live')
            store.transition(parent['run_id'], 'RUNNING')
            plan = benchmark_plans(task, network_details('epa-net1')['catalog'])[0]
            child = store.create_run(task, 'benchmark')
            store.link_live_child(parent['run_id'], child['run_id'])
            store._write_json(f"{child['run_id']}/candidates/{plan['candidate_id']}/plan.json", plan)
            store._write_json(f"{child['run_id']}/candidates/{plan['candidate_id']}/verification.json",
                {'task_ref': child['task_ref'], 'candidate_id': plan['candidate_id'], 'validity': 'valid', 'violations': []})
            hint = workflow_hint(store, store.get_run(parent['run_id']))
            self.assertEqual(hint['required_next'], 'create_perturbation')
            self.assertAlmostEqual(hint['demand_multiplier'], 1.485)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = RunStore(root / 'state.sqlite3', root / 'artifacts')
        self.task = self.store.save_task(propose_task(TaskProposal(fault_kind='normal', candidate_limit=3)))
        self.plans = benchmark_plans(self.task, network_details('epa-net1')['catalog'])
        self.parent = self.store.create_run(self.task, 'live')
        self.store.transition(self.parent['run_id'], 'RUNNING', 'PLANNING')
        self.store.bind_agh(self.parent['run_id'], 'isolated-guard-session', {'id': 'agnes-ai', 'model': 'test'})

    def tearDown(self):
        self.temp.cleanup()

    def child(self, plan, passed=None, stress=False):
        child = self.store.create_run(self.task, 'benchmark')
        self.store.link_live_child(self.parent['run_id'], child['run_id'])
        self.store._write_json(f"{child['run_id']}/submitted-plan.json", plan)
        self.store._write_json(f"{child['run_id']}/candidates/{plan['candidate_id']}/plan.json", plan)
        if stress:
            self.store._write_json(f"{child['run_id']}/perturbation.json", {'required_labels': ['demand-plus-10pct']})
        if passed is not None:
            self.store._write_json(f"{child['run_id']}/candidates/{plan['candidate_id']}/verification.json",
                {'task_ref': child['task_ref'], 'candidate_id': plan['candidate_id'], 'validity': 'valid',
                 'violations': [] if passed else [{'code': 'MIN_PRESSURE'}],
                 'metrics': {'min_node_time_pass_ratio': 1 if passed else .5, 'mean_node_time_pass_ratio': 1 if passed else .8,
                             'shortage_m3': 0 if passed else 30, 'action_cost': len(plan['actions'])}})
        return child

    def test_last_slot_cannot_be_spent_on_another_nominal_when_stress_is_pending(self):
        nominal = self.child(self.plans[0], passed=True)
        for request in ('isolated-first', 'isolated-second'):
            reservation = self.store.reserve_simulation(self.parent['run_id'], request)
            self.store.settle_simulation(reservation['reservation_id'])
        untested = self.child(self.plans[1])
        with self.assertRaisesRegex(ConflictError, 'STRESS_SLOT_RESERVED'):
            self.store.reserve_live_child(untested['run_id'])
        self.assertEqual(self.store.get_run(self.parent['run_id'])['budget_usage']['simulations'], 2)
        hint = workflow_hint(self.store, self.store.get_run(self.parent['run_id']))
        self.assertEqual(hint['required_next'], 'create_perturbation')
        self.assertEqual(hint['source_run_id'], nominal['run_id'])
        stress = self.child(self.plans[0], stress=True)
        self.store.reserve_live_child(stress['run_id'])

    def test_failed_stress_is_tested_and_does_not_trigger_an_identical_retry(self):
        self.child(self.plans[0], passed=True)
        self.child(self.plans[0], passed=False, stress=True)
        nominal, pending, complete = reviews(self.store, self.task, self.store.live_children(self.parent['run_id']))
        self.assertEqual(len(nominal), 1)
        self.assertEqual(pending, [])
        self.assertEqual(complete, [])
        self.assertEqual(workflow_hint(self.store, self.store.get_run(self.parent['run_id']))['required_next'], 'choose_candidate')

    def test_exhaustion_has_only_comparison_and_archive_as_next_step(self):
        self.child(self.plans[0], passed=False)
        for request in ('one', 'two', 'three'):
            reservation = self.store.reserve_simulation(self.parent['run_id'], request)
            self.store.settle_simulation(reservation['reservation_id'])
        hint = workflow_hint(self.store, self.store.get_run(self.parent['run_id']))
        self.assertEqual(hint['required_next'], 'compare_and_archive')
        self.assertEqual(hint['remaining_simulations'], 0)
        self.assertTrue(hint['compare_run_ids'])

    def test_failed_action_requires_real_revision_before_more_static_candidates(self):
        source = self.child(self.plans[1], passed=False)
        hint = workflow_hint(self.store, self.store.get_run(self.parent['run_id']))
        self.assertEqual(hint['required_next'], 'diagnose_and_revise')
        self.assertEqual(hint['source_run_id'], source['run_id'])
        self.assertEqual(hint['candidate_ids'], [])
        revision = dict(self.plans[1], candidate_id='isolated-revision', source='agh', parent_candidate_id=self.plans[1]['candidate_id'])
        self.store._write_json(f"{self.parent['run_id']}/planning/revisions/isolated.json", {'plan': revision})
        self.assertEqual(workflow_hint(self.store, self.store.get_run(self.parent['run_id']))['required_next'], 'simulate_revision')
        self.child(revision, passed=False)
        self.assertEqual(workflow_hint(self.store, self.store.get_run(self.parent['run_id']))['required_next'], 'choose_candidate')

    def test_one_remaining_slot_does_not_force_a_revision_that_cannot_get_stress_review(self):
        self.child(self.plans[1], passed=False)
        for request in ('first', 'second'):
            reservation = self.store.reserve_simulation(self.parent['run_id'], request)
            self.store.settle_simulation(reservation['reservation_id'])
        hint = workflow_hint(self.store, self.store.get_run(self.parent['run_id']))
        self.assertEqual(hint['remaining_simulations'], 1)
        self.assertEqual(hint['required_next'], 'choose_candidate')
