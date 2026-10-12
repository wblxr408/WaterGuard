"""Verify experimental selection and formal delivery without invoking a live model."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from experiments import realtime_dispatch as rt


class RealtimeMitigationTests(unittest.TestCase):
    def setUp(self):
        self.output = tempfile.TemporaryDirectory()
        self.addCleanup(self.output.cleanup)

    def reports(self):
        task = dict(objectives=['maximize_min_node_availability', 'maximize_mean_node_availability'],
            budget={'max_wall_time_s': 10, 'max_simulations': 4}, action_limits={'earliest_s': 4500},
            response_latency={'earliest_effective_s': 4500}, time_window={'evaluation_start_s': 3600},
            fault={'start_s': 3600}, perturbation_domain={'required': ['demand-plus-10pct'], 'demand_multipliers': [1, 1.1]})
        def report(cid, values):
            return dict(candidate_id=cid, task_ref={'task_id': 'same'}, coverage={'start_s': 3600, 'end_s': 10800},
                validity='valid', violations=[{'code': 'MIN_PRESSURE'}], metrics=dict(
                    min_node_time_pass_ratio=min(values)/7200, mean_node_time_pass_ratio=sum(values)/14400,
                    min_pressure_m=10, shortage_m3=50, supply_ratio=.9,
                    node_availability=[dict(node_id=n, assessed_duration_s=7200, normal_supply_duration_s=s,
                        normal_supply_time_ratio=s/7200) for n,s in zip(['a','b'], values)]))
        baseline=report('base', [0, 0])
        fair=report('fair', [200, 5000])
        breadth=report('breadth', [100, 7000])
        def plan(cid):
            return dict(candidate_id=cid, plan_hash='same-plan', actions=[] if cid == 'base' else
                [dict(kind='link_close', link_id='11', simulation_time_s=4500)])
        return task, [plan('base'), plan('breadth'), plan('fair')], [baseline,breadth,fair]

    def test_selection_obeys_frozen_worst_node_priority(self):
        task, plans, reports = self.reports()
        adapter=Mock()
        adapter.simulate.return_value={'artifact': {'simulation_id': 'simulation'}}
        with patch.object(rt, 'benchmark_plans', return_value=plans), patch.object(rt, 'verify', side_effect=reports[1:]):
            chosen=rt.choose_selected(task, {}, {}, adapter, reports[0], Path(self.output.name))
        self.assertEqual(chosen[3]['plan']['candidate_id'], 'fair')

    def test_nominal_failure_still_delivers_and_revalidates_same_rescue(self):
        task, plans, reports = self.reports()
        task['emergency_floor'] = {'min_pressure_m': 15, 'min_supply_ratio': .75, 'source': 'Explicit test fixture'}
        adapter=Mock()
        adapter.simulate.return_value={'artifact': {'simulation_id': 'simulation'}}
        scenario=dict(id='case', network='test', note='controlled test', demand_increase_pct=10)
        with tempfile.TemporaryDirectory() as temp, patch.object(rt, 'build_task', return_value=task), \
             patch.object(rt, 'network_details', return_value={'catalog': {}, 'scene': {}}), \
             patch.object(rt, 'benchmark_plans', return_value=plans), patch.object(rt, 'PhysicalAdapter', return_value=adapter), \
             patch.object(rt, 'verify', side_effect=[*reports, reports[-1]]):
            result=rt.run_scenario(scenario, Path(temp))
        self.assertEqual(result['conclusion'], 'BEST_EFFORT_MITIGATION')
        self.assertIsNone(result['selected'])
        self.assertEqual(result['emergency_rescue']['candidate_id'], 'fair')
        self.assertFalse(result['emergency_rescue']['is_recommendation'])
        self.assertEqual(adapter.simulate.call_args.args[1], plans[-1])
        self.assertEqual(adapter.simulate.call_args.kwargs['perturbation']['parameters']['demand_multiplier'], 1.1)

    def test_nominal_search_reserves_a_slot_for_same_plan_stress(self):
        task, plans, reports = self.reports()
        task['budget']['max_simulations'] = 3
        adapter = Mock()
        adapter.simulate.return_value = {'artifact': {'simulation_id': 'simulation'}}
        with patch.object(rt, 'benchmark_plans', return_value=plans), patch.object(rt, 'verify', side_effect=reports[1:]):
            rt.choose_selected(task, {}, {}, adapter, reports[0], Path(self.output.name))
        self.assertEqual(adapter.simulate.call_count, 1)
