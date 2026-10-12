"""Unequal intervals, node fairness and arbitrary demand growth are business tests."""
import copy
import unittest

from services.api.workbench import TaskProposal, propose_task
from services.hydraulics.compiler import validate_perturbation
from services.hydraulics.configuration import ranking_key, stress_multiplier
from waterguard_contracts.core import FIXTURES, _verify_result, load, task_hash


class DispatchObjectiveTests(unittest.TestCase):
    def test_union_of_pressure_and_supply_failure_is_duration_weighted_per_node(self):
        task, plan, result, catalog, scene = [load(FIXTURES / n) for n in
            ('task.json', 'candidate-b.json', 'result-b.json', 'action-catalog.json', 'scene.json')]
        task['objectives'] = ['maximize_min_node_availability', 'maximize_mean_node_availability']
        task['constraint_hash'] = task_hash(task)
        from waterguard_contracts.core import task_ref, plan_hash, input_hash
        plan['task_ref'] = task_ref(task)
        plan['plan_hash'] = plan_hash(plan)
        result['artifact']['task_ref'] = task_ref(task)
        result['artifact']['input_hash'] = input_hash(task, plan, result['artifact']['engine_version'])
        nodes = copy.deepcopy(result['series']['frames'][-1]['nodes'])
        frames = [dict(time_s=3600, duration_s=100, nodes=copy.deepcopy(nodes)),
                  dict(time_s=3700, duration_s=7100, nodes=copy.deepcopy(nodes))]
        frames[0]['nodes'][0].update(pressure_m=0, delivered_demand_m3s=0)
        report = _verify_result(task, plan, result, catalog, scene, 'fixture', 'duration-test', assessment_frames=frames)
        metrics = report['metrics']
        first = metrics['node_availability'][0]
        self.assertEqual(first['normal_supply_duration_s'], 7100)
        self.assertEqual(first['assessed_duration_s'], 7200)
        self.assertAlmostEqual(metrics['min_node_time_pass_ratio'], 7100 / 7200)
        self.assertAlmostEqual(metrics['mean_node_time_pass_ratio'], (7100 / 7200 + 2) / 3)
        self.assertEqual(report['admission']['nominal_constraints_pass'], 'fail')

    def test_arbitrary_fractional_growth_changes_frozen_domain_and_rejects_wrong_label(self):
        task = propose_task(TaskProposal(threshold_mode='manual', min_pressure_m=20,
                            min_supply_ratio=.95, demand_increase_pct=23.75))
        self.assertEqual(task['perturbation_domain']['required'], ['demand-plus-23.75pct'])
        self.assertEqual(task['perturbation_domain']['demand_multipliers'], [1, 1.2375])
        perturbation = dict(perturbation_id='variable-stress', candidate_id='plan',
            parameters={'demand_multiplier': 1.2375}, required_labels=['demand-plus-23.75pct'])
        validate_perturbation(task, {'candidate_id': 'plan'}, perturbation)
        perturbation['parameters']['demand_multiplier'] = 1
        with self.assertRaisesRegex(ValueError, 'actual demand increase'):
            validate_perturbation(task, {'candidate_id': 'plan'}, perturbation)

    def test_growth_is_relative_to_nonunit_nominal_demand(self):
        task = propose_task(TaskProposal(threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95,
            hydraulic_options={'demand_multiplier': 1.2}, demand_increase_pct=25))
        self.assertEqual(task['perturbation_domain']['demand_multipliers'], [1.2, 1.5])
        task['perturbation_domain']['demand_multipliers'].insert(1, 1.3)
        self.assertEqual(stress_multiplier(task), 1.5)

    def test_frozen_priority_protects_worst_node_before_volume(self):
        task = {'objectives': ['maximize_min_node_availability', 'maximize_mean_node_availability', 'minimize_shortage']}
        a = dict(candidate_id='a', metrics=dict(min_node_time_pass_ratio=.8, mean_node_time_pass_ratio=.95, shortage_m3=1))
        b = dict(candidate_id='b', metrics=dict(min_node_time_pass_ratio=.9, mean_node_time_pass_ratio=.94, shortage_m3=2))
        self.assertLess(ranking_key(task, b), ranking_key(task, a))
        task['objectives'] = ['minimize_shortage']
        self.assertLess(ranking_key(task, a), ranking_key(task, b))
