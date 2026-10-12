"""Consumer-level dispatch outcomes; synthetic host records are not live evidence."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.api.agh import _finalize_recommendation
from services.api.runtime import RunStore
from services.api.workbench import TaskProposal, propose_task, network_details, benchmark_plans
from waterguard_contracts.core import FIXTURES, load, task_hash, task_ref, plan_hash, input_hash, _verify_result


class MitigationTests(unittest.TestCase):
    def test_default_search_reaches_multiple_facilities(self):
        task = propose_task(TaskProposal(threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95))
        plans = benchmark_plans(task, network_details('epa-net1')['catalog'])
        self.assertTrue(any(len({a['link_id'] for a in p['actions']}) >= 3 for p in plans))

    def test_confirmed_floor_is_frozen_for_new_standard_tasks(self):
        task = propose_task(TaskProposal(threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95))
        self.assertEqual(task.get('emergency_floor', {}).get('min_pressure_m'), 15)
        self.assertEqual(task['emergency_floor']['min_supply_ratio'], .75)
        self.assertEqual(task['hard_constraints']['min_pressure_m'], 20)
        self.assertEqual(task['hard_constraints']['min_supply_ratio'], .95)

    def test_explicit_null_or_lower_custom_gate_does_not_enable_a_stricter_emergency_floor(self):
        for extra in ({'emergency_floor': None}, {'min_pressure_m': 10, 'min_supply_ratio': .7}):
            values = dict(threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95)
            values.update(extra)
            task = propose_task(TaskProposal(**values))
            self.assertNotIn('emergency_floor', task)

    def test_multi_isolation_and_backup_supply_are_reachable_within_budget(self):
        details = network_details('epa-net3')
        pump = next(f for f in details['catalog']['facilities'] if f['link_id'] == '10')
        task = propose_task(TaskProposal(network_id='epa-net3', threshold_mode='manual',
            min_pressure_m=20, min_supply_ratio=.95, candidate_limit=16, max_actions=4,
            response_latency={'decision_wall_clock_s': 500},
            control_overrides=[dict(link_id='10', control_names=pump['native_control_names'], reason='Emergency backup')]))
        task['action_limits']['allowed_link_ids'].append('10')
        task['constraint_hash'] = task_hash(task)
        plans = benchmark_plans(task, details['catalog'])
        combined = [p for p in plans if any(a['kind'] == 'pump_on' and a['link_id'] == '10' for a in p['actions'])
                    and len({a['link_id'] for a in p['actions'] if a['kind'] == 'link_close'}) >= 2]
        self.assertTrue(combined, 'The search must reach multi-isolation plus backup supply')
        self.assertLessEqual(len(plans), 16)
        for p in plans:
            self.assertLessEqual(len(p['actions']), 4)
            for action in p['actions']:
                self.assertGreaterEqual(action['simulation_time_s'], 4500)
                self.assertNotEqual(action['link_id'], task['fault']['link_id'])

    def interval_report(self, emergency=False):
        task, plan, result, catalog, scene = [load(FIXTURES / n) for n in
            ('task.json', 'candidate-b.json', 'result-b.json', 'action-catalog.json', 'scene.json')]
        task['objectives'] = ['maximize_min_node_availability', 'maximize_mean_node_availability']
        if emergency:
            task['emergency_floor'] = dict(min_pressure_m=15, min_supply_ratio=.75, source='Explicit test assumption')
        task['constraint_hash'] = task_hash(task)
        plan['task_ref'] = task_ref(task)
        plan['plan_hash'] = plan_hash(plan)
        result['artifact']['task_ref'] = task_ref(task)
        result['artifact']['input_hash'] = input_hash(task, plan, result['artifact']['engine_version'])
        nodes = result['series']['frames'][-1]['nodes']
        frames = [dict(time_s=3600, duration_s=100, nodes=copy.deepcopy(nodes)),
                  dict(time_s=3700, duration_s=7100, nodes=copy.deepcopy(nodes))]
        frames[0]['nodes'][0].update(pressure_m=16 if emergency else 0,
            delivered_demand_m3s=frames[0]['nodes'][0]['expected_demand_m3s'] * .8 if emergency else 0)
        report = _verify_result(task, plan, result, catalog, scene, 'fixture', 'test', assessment_frames=frames)
        return report

    def test_interval_resilience_counts_all_nodes_without_emergency_threshold(self):
        report = self.interval_report()
        self.assertEqual(report['metrics'].get('fully_compliant_node_count'), 2)
        self.assertEqual(report['metrics'].get('assessed_node_count'), 3)
        self.assertAlmostEqual(report['metrics']['min_node_time_pass_ratio'], 7100 / 7200)
        self.assertNotIn('nodes_reaching_emergency_service', report['metrics'])
        self.assertEqual(report['admission']['recommendation_eligible'], 'fail')

    def test_explicit_emergency_floor_measures_coverage_without_relaxing_hard_gate(self):
        report = self.interval_report(emergency=True)
        self.assertEqual(report['metrics']['fully_compliant_node_count'], 2)
        self.assertEqual(report['metrics']['nodes_reaching_emergency_service'], 3)
        self.assertEqual(report['metrics']['emergency_min_node_time_pass_ratio'], 1)
        self.assertEqual(report['metrics']['emergency_mean_node_time_pass_ratio'], 1)
        self.assertEqual(report['admission']['nominal_constraints_pass'], 'fail')
        self.assertEqual(report['admission']['recommendation_eligible'], 'fail')

    def aggregate(self, *, provenance=True, improvement=True, baseline=True, finalize=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = RunStore(Path(temp.name) / 'db', Path(temp.name) / 'artifacts')
        task = load(FIXTURES / 'task.json')
        task['objectives'] = ['maximize_min_node_availability', 'maximize_mean_node_availability', 'minimize_shortage']
        task['constraint_hash'] = task_hash(task)
        task = store.save_task(task)
        parent = store.create_run(task, 'live')
        store.transition(parent['run_id'], 'RUNNING')
        # The breadth candidate has better average but worse minimum than the fair candidate.
        candidates = [('base', [0, 100], False), ('breadth', [10, 100], True), ('fair', [20, 80], True)]
        for cid, seconds, action in candidates:
            if cid == 'base' and not baseline:
                continue
            if not improvement:
                seconds = [0, 100]
            child = store.create_run(task, 'benchmark')
            store.link_live_child(parent['run_id'], child['run_id'])
            plan = load(FIXTURES / 'candidate-b.json')
            plan.update(candidate_id=cid, task_ref=task_ref(task))
            if not action:
                plan['actions'] = []
            plan['plan_hash'] = plan_hash(plan)
            result = load(FIXTURES / 'result-b.json')
            result['artifact'].update(task_ref=task_ref(task), simulation_id='sim-' + cid)
            report = load(FIXTURES / 'verification-a.json')
            report.update(task_ref=task_ref(task), candidate_id=cid, simulation_id='sim-' + cid)
            report['metrics'].update(min_node_time_pass_ratio=min(seconds)/100,
                mean_node_time_pass_ratio=sum(seconds)/200, shortage_m3=10,
                node_availability=[dict(node_id=n, assessed_duration_s=100, normal_supply_duration_s=s,
                    normal_supply_time_ratio=s/100) for n, s in zip(['J1', 'J2'], seconds)])
            for name, value in [('plan.json', plan), ('result.json', result), ('verification.json', report)]:
                store._write_json(f"{child['run_id']}/candidates/{cid}/{name}", value)
        summary = None
        if finalize:
            with patch('services.evidence.service._validated_agh_association', return_value=provenance):
                summary = _finalize_recommendation(store, parent['run_id'], task, store.live_children(parent['run_id']))
        return store, parent, summary

    def test_wall_budget_retains_verified_mitigation_without_claiming_completed_search(self):
        self.budget_delivery()

    def test_supervisor_deadline_retains_verified_mitigation_after_forced_stop(self):
        self.budget_delivery(hard_deadline=True)

    def test_wall_budget_without_native_provenance_does_not_deliver_mitigation(self):
        self.budget_delivery(provenance=False)

    def budget_delivery(self, *, hard_deadline=False, provenance=True):
        import io
        import itertools
        import json
        import threading
        from unittest.mock import Mock
        from services.api.agh import LiveExecutor
        store, parent, _ = self.aggregate(finalize=False)
        store.bind_agh(parent['run_id'], 'isolated-budget-session', {'id': 'agnes-ai', 'model': 'test'})
        children = store.live_children(parent['run_id'])
        for child in children:
            store.transition(child['run_id'], 'RUNNING')
            store.transition(child['run_id'], 'COMPLETED')
        process = Mock(stdin=io.StringIO(), stdout=io.StringIO(json.dumps(dict(kind='completed',
            budget_exhausted=True, result={'status': 'budget_exhausted'},
            child_runs=[c['run_id'] for c in children])) + '\n'))
        process.wait.return_value = 1 if hard_deadline else 0
        process.poll.return_value = 0
        benchmark = Mock(evidence_lock=threading.RLock())
        executor = LiveExecutor(store, benchmark)
        try:
            from contextlib import nullcontext
            clock = patch('services.api.agh.time.monotonic', side_effect=itertools.chain([0], itertools.repeat(10000))) if hard_deadline else nullcontext()
            with patch('services.api.agh.command', return_value=['isolated-sdk']), \
                 patch('services.api.agh.subprocess.Popen', return_value=process), \
                 patch('services.api.agh.bridge', return_value={}), clock, \
                 patch('services.evidence.service._validated_agh_association', return_value=provenance):
                executor.execute(parent['run_id'], {'id': 'agnes-ai', 'model': 'test'}, threading.Event())
            outcome = store.get_run(parent['run_id'])
            self.assertEqual(outcome['status'], 'BUDGET_EXHAUSTED')
            if not provenance:
                self.assertEqual(outcome['conclusion'], 'INCOMPLETE')
                self.assertIsNone(outcome.get('mitigation_candidate_id'))
                return
            self.assertEqual(outcome['conclusion'], 'BEST_EFFORT_MITIGATION',
                store._read_json(store._run_directory(parent['run_id']) / 'error.json'))
            self.assertEqual(outcome['mitigation_candidate_id'], 'fair')
            self.assertIsNone(outcome['selected_candidate_id'])
            report = store._read_json(store._run_directory(parent['run_id']) / 'candidates/fair/verification.json')
            self.assertEqual(report['admission']['recommendation_eligible'], 'fail')
            self.assertEqual(report['admission']['required_stress_tests_pass'], 'pending')
            self.assertTrue(report['violations'])
        finally:
            executor.close()

    def test_formal_mitigation_keeps_recommendation_failed_and_reports_regressions(self):
        store, parent, summary = self.aggregate()
        self.assertEqual(summary['conclusion'], 'BEST_EFFORT_MITIGATION')
        run = store.get_run(parent['run_id'])
        self.assertIsNone(run['selected_candidate_id'])
        self.assertEqual(run.get('mitigation_candidate_id'), 'fair')
        rescue = summary['mitigation']
        self.assertEqual([r['delta_s'] for r in rescue['per_node']], [20, -20])
        self.assertTrue(rescue['any_node_regressed'])
        self.assertIsNone(rescue['per_node'][0]['emergency_delta_s'])
        report = store._read_json(store._run_directory(parent['run_id']) / 'candidates/fair/verification.json')
        self.assertEqual(report['admission']['recommendation_eligible'], 'fail')
        self.assertTrue(report['violations'])

    def test_no_improvement_is_not_mitigation(self):
        self.assertEqual(self.aggregate(improvement=False)[2]['conclusion'], 'NO_FEASIBLE_FOUND')

    def test_missing_baseline_cannot_claim_rescue(self):
        self.assertNotEqual(self.aggregate(baseline=False)[2]['conclusion'], 'BEST_EFFORT_MITIGATION')

    def test_missing_native_evidence_cannot_claim_formal_mitigation(self):
        self.assertEqual(self.aggregate(provenance=False)[2]['conclusion'], 'INCOMPLETE')
