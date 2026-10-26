"""Current-change behavior contract for decision studies (not an existing-suite run)."""
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from services.api.main import create_app
from services.api.runtime import RunStore
from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details


class DecisionStudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name)/'state.db', Path(self.temp.name)/'artifacts')
        self.app = create_app(self.store)
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()
        self.task = propose_task(TaskProposal(network_id='epa-net1', fault_kind='closure',
            threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95, candidate_limit=6))
        self.plan = benchmark_plans(self.task, network_details('epa-net1')['catalog'])[0]

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def payload(self, **overrides):
        return dict(task=self.task, plan=self.plan, kind='boundary', demand_multipliers=[.9,1,1.2],
                    repair_end_times_s=[6300,7200,8100], max_simulations=5, **overrides)

    def preview(self, body):
        response = self.client.post('/api/decision-studies/preview', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait(self, study_id):
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            state = self.client.get(f'/api/decision-studies/{study_id}').json()
            if state['status'] not in ('QUEUED','RUNNING'):
                return state
            time.sleep(.02)
        self.fail('Decision study did not finish')

    def test_partial_boundary_preserves_unknowns_and_budget(self):
        body = self.payload()
        original = copy.deepcopy(body)
        preview = self.preview(body)
        self.assertEqual(len(preview['points']), 9)
        self.assertTrue(all(p['state']=='unknown' for p in preview['points']))
        calls=[]
        def evaluate(task, plan, directory, **kwargs):
            calls.append((task['hydraulic_options']['demand_multiplier'],task['fault']['end_s']))
            return dict(state='normal' if calls[-1][0]<=1 else 'gap', metrics={'shortage_m3':10*calls[-1][0]},
                        report={'violations':[]}, simulation_id=f'sim-{len(calls)}', actions=plan['actions'])
        with patch.object(self.app.state.decision_studies, 'evaluate', side_effect=evaluate):
            response=self.client.post('/api/decision-studies',json=body)
            self.assertEqual(response.status_code,202,response.text)
            study=self.wait(response.json()['study_id'])
        self.assertEqual(study['status'],'COMPLETED')
        self.assertEqual(study['simulations'],5)
        self.assertEqual(sum(p['state']=='unknown' for p in study['points']),4)
        self.assertEqual(len(set(calls)),5)
        self.assertEqual(body,original)
        self.assertEqual(self.client.get('/api/decision-studies').json()[0]['study_id'],study['study_id'])

    def test_rescue_uses_tested_cost_and_preserves_source_thresholds(self):
        body=self.payload();body.update(kind='rescue',rescue_options=[
            dict(label='提前一格',cost=1,repair_end_s=6300),dict(label='提前两格',cost=3,repair_end_s=5400)])
        preview=self.preview(body)
        self.assertEqual(len(preview['points']),3)
        def evaluate(task,plan,directory,**kwargs):
            self.assertEqual(task['hard_constraints'],self.task['hard_constraints'])
            passed=task['fault']['end_s']==5400
            return dict(state='normal' if passed else 'gap',metrics={'shortage_m3':0 if passed else 10},
                        report={'violations':[]},simulation_id='sim-test',actions=plan['actions'])
        with patch.object(self.app.state.decision_studies,'evaluate',side_effect=evaluate):
            study=self.wait(self.client.post('/api/decision-studies',json=body).json()['study_id'])
        selected=next(p for p in study['points'] if p['point_id']==study['selected_point_id'])
        self.assertEqual(selected['label'],'提前两格')
        self.assertEqual(selected['cost'],3)

    def test_event_keeps_executed_actions_and_moves_future_to_response_floor(self):
        plans=benchmark_plans(self.task,network_details('epa-net1')['catalog'])
        body=self.payload();body.update(kind='event',plan=plans[1],event_time_s=5400,
            response_time_s=600,new_repair_end_s=9000)
        preview=self.preview(body)
        original_prefix=[a for a in plans[1]['actions'] if a['simulation_time_s']<5400]
        self.assertTrue(original_prefix)
        self.assertGreater(len(preview['points']),1)
        for point in preview['points'][1:]:
            actions=point['plan']['actions']
            self.assertEqual([a for a in actions if a['simulation_time_s']<5400],original_prefix)
            self.assertTrue(all(a['simulation_time_s']>=6300 for a in actions if a not in original_prefix))
        self.assertEqual(preview['points'][0]['task']['fault']['end_s'],9000)
        self.assertEqual(self.task['fault']['end_s'],7200)

    def test_cancel_stops_current_evaluation_and_leaves_unvisited_points(self):
        body=self.payload();self.preview(body)
        entered=threading.Event()
        def evaluate(task,plan,directory,*,cancel_event,**kwargs):
            entered.set();self.assertTrue(cancel_event.wait(5))
            return dict(state='invalid',metrics={},report={'violations':[]},simulation_id='sim-cancel',actions=[])
        with patch.object(self.app.state.decision_studies,'evaluate',side_effect=evaluate):
            study_id=self.client.post('/api/decision-studies',json=body).json()['study_id']
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.client.post(f'/api/decision-studies/{study_id}/cancel').status_code,200)
            study=self.wait(study_id)
        self.assertEqual(study['status'],'CANCELLED')
        self.assertTrue(any(p['state']=='unknown' for p in study['points']))

    def test_event_remaining_loss_counts_only_the_overlap_after_event(self):
        from services.api.decision_studies import remaining_outcome
        task=copy.deepcopy(self.task)
        task['hard_constraints']['node_ids']=['n']
        task['time_window']['end_s']=5400
        frames=[dict(time_s=3600,duration_s=900,nodes=[dict(node_id='n',pressure_m=10,
                    expected_demand_m3s=.001,delivered_demand_m3s=.0005)]),
                dict(time_s=4500,duration_s=900,nodes=[dict(node_id='n',pressure_m=25,
                    expected_demand_m3s=.001,delivered_demand_m3s=.001)])]
        outcome=remaining_outcome(task,frames,4050)
        self.assertAlmostEqual(outcome['shortage_m3'],.225)
        self.assertAlmostEqual(outcome['mean_node_time_pass_ratio'],2/3)
        self.assertEqual(outcome['per_node'][0]['normal_s'],900)

    def test_actual_boundary_point_uses_hydraulics_and_returns_dense_report(self):
        body=self.payload();body.update(demand_multipliers=[1],repair_end_times_s=[7200],max_simulations=1)
        self.preview(body)
        study=self.wait(self.client.post('/api/decision-studies',json=body).json()['study_id'])
        self.assertEqual(study['status'],'COMPLETED',study.get('error'))
        point=study['points'][0]
        self.assertEqual(point['report']['validity'],'valid')
        self.assertEqual(point['report']['admission']['simulation_valid'],'pass')
        self.assertEqual(point['metrics']['assessed_node_count'],len(self.task['hard_constraints']['node_ids']))
        self.assertGreaterEqual(point['metrics']['shortage_m3'],0)
        self.assertEqual(point['task']['hard_constraints'],self.task['hard_constraints'])

    def test_multifidelity_counts_both_levels_and_never_promotes_coarse_pass(self):
        body=self.payload();body.update(max_simulations=6,use_surrogate=True,high_fidelity_budget=2)
        self.preview(body)
        calls=[]
        def evaluate(task,plan,directory,**kwargs):
            fidelity=kwargs.get('fidelity','high');calls.append(fidelity)
            return dict(state='normal',metrics={'shortage_m3':0,'min_pressure_m':21},report={'violations':[]},
                simulation_id=f'sim-{len(calls)}',actions=plan['actions'],
                node_pressure_m={n:21+(2 if fidelity=='high' else 0) for n in task['hard_constraints']['node_ids']})
        with patch.object(self.app.state.decision_studies,'evaluate',side_effect=evaluate):
            study=self.wait(self.client.post('/api/decision-studies',json=body).json()['study_id'])
        self.assertEqual(calls.count('low'),4)
        self.assertEqual(calls.count('high'),2)
        self.assertEqual(study['simulations'],6)
        self.assertEqual(sum(p['state']=='unknown' for p in study['points']),7)
        self.assertEqual(study['surrogate']['training_pairs'],2)
        predicted=[p['prediction'] for p in study['points'] if p.get('prediction')]
        self.assertTrue(predicted)
        self.assertTrue(all(0<=p['min_supply_ratio']<=1 for p in predicted))

    def test_event_selection_preserves_frozen_objective_order(self):
        from services.api.decision_studies import DecisionStudies
        task=copy.deepcopy(self.task)
        task['objectives']=['maximize_emergency_node_coverage','maximize_emergency_mean_availability','minimize_shortage']
        def point(identifier,count,mean):
            return dict(point_id=identifier,state='gap',task=task,
                report={'candidate_id':identifier,'metrics':{'nodes_reaching_emergency_service':count,
                    'emergency_mean_node_time_pass_ratio':mean,'shortage_m3':10}},
                remaining={'nodes_reaching_emergency_service':count,'emergency_mean_node_time_pass_ratio':mean,
                    'mean_node_time_pass_ratio':mean,'shortage_m3':10})
        study={'kind':'event','points':[point('more-nodes',5,.7),point('higher-average',4,.9)]}
        DecisionStudies._select(study)
        self.assertEqual(study['selected_point_id'],'more-nodes')


if __name__=='__main__':
    unittest.main()
