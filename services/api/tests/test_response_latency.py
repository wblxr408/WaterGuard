"""Real-time response latency: the server must refuse backdated dispatch actions."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))

from services.api.workbench import TaskProposal, propose_task, network_details, benchmark_plans
from waterguard_contracts.core import check_task, check_plan, latency_floor_s, task_hash, plan_hash, task_ref


class ResponseLatencyTests(unittest.TestCase):
    def proposal(self, decision_wall_clock_s=569.632, **extra):
        return TaskProposal(network_id='epa-net1', fault_kind='leak', link_id='22',
            fault_parameters={'leak_area_m2': 0.01865}, threshold_mode='manual',
            min_pressure_m=20, min_supply_ratio=0.95, demand_increase_pct=7.5,
            candidate_limit=16, max_actions=2, max_wall_time_s=1200,
            response_latency=dict(decision_wall_clock_s=decision_wall_clock_s, **extra))

    def test_floor_rounds_up_to_the_next_control_boundary(self):
        # fault 3600, step 300, detection 60 + decision 569.632 + actuation 120 = 749.632 -> ceil/300 = 3 steps
        self.assertEqual(latency_floor_s(3600, 300, 60, 569.632, 120), 4500)
        self.assertEqual(latency_floor_s(3600, 300, 0, 0, 0), 3600)
        self.assertEqual(latency_floor_s(3600, 300, 100, 100, 100), 3900)

    def test_proposal_freezes_floor_and_opens_window_at_it(self):
        task = propose_task(self.proposal())
        self.assertEqual(task['action_limits']['earliest_s'], 4500)
        self.assertEqual(task['time_window']['evaluation_start_s'], task['fault']['start_s'])
        self.assertEqual(task['response_latency']['earliest_effective_s'], 4500)
        self.assertEqual(task['response_latency']['detection_s'], 60)
        self.assertEqual(task['response_latency']['actuation_s'], 120)
        self.assertEqual(task_hash(task), task['constraint_hash'])
        details = network_details('epa-net1')
        check_task(task, details['catalog'], details['scene'])  # does not raise

    def test_check_task_refuses_a_backdated_action_window(self):
        task = propose_task(self.proposal())
        details = network_details('epa-net1')
        backdated = copy.deepcopy(task)
        backdated['action_limits']['earliest_s'] = 3900  # the old assumed +300s
        backdated['constraint_hash'] = task_hash(backdated)
        with self.assertRaisesRegex(ValueError, 'backdates before the real response-latency floor'):
            check_task(backdated, details['catalog'], details['scene'])

    def test_check_task_requires_accounting_from_fault_onset(self):
        task = propose_task(self.proposal())
        details = network_details('epa-net1')
        shifted = copy.deepcopy(task)
        # Move the accounting window a full control step past the fault; it stays on a
        # sample boundary, so the real-time onset check is what must reject it.
        shifted['time_window']['evaluation_start_s'] = shifted['fault']['start_s'] + shifted['time_window']['step_s']
        shifted['constraint_hash'] = task_hash(shifted)
        with self.assertRaisesRegex(ValueError, 'fault onset'):
            check_task(shifted, details['catalog'], details['scene'])

    def test_declared_floor_must_match_the_real_latency(self):
        task = propose_task(self.proposal())
        details = network_details('epa-net1')
        tampered = copy.deepcopy(task)
        tampered['response_latency']['earliest_effective_s'] = 3900
        tampered['constraint_hash'] = task_hash(tampered)
        with self.assertRaisesRegex(ValueError, 'not the real-latency floor'):
            check_task(tampered, details['catalog'], details['scene'])

    def test_check_plan_rejects_an_action_before_the_floor(self):
        task = propose_task(self.proposal())
        details = network_details('epa-net1')
        plan = dict(schema_version=task['schema_version'], candidate_id='backdated-close-11',
            task_ref=task_ref(task), parent_candidate_id=None,
            actions=[dict(kind='link_close', link_id='11', simulation_time_s=3900)],
            source='enumeration', reason='Attempt to act before the real decision was ready.')
        plan['plan_hash'] = plan_hash(plan)
        with self.assertRaisesRegex(ValueError, 'Action outside time window'):
            check_plan(task, plan, details['catalog'])

    def test_server_candidates_never_backdate(self):
        task = propose_task(self.proposal())
        details = network_details('epa-net1')
        for plan in benchmark_plans(task, details['catalog']):
            for action in plan['actions']:
                self.assertGreaterEqual(action['simulation_time_s'], 4500,
                    f"candidate {plan['candidate_id']} backdated an action")

    def test_latency_leaving_no_slot_is_rejected(self):
        # A decision wall-clock so large the floor passes latest_s must be refused.
        with self.assertRaisesRegex(ValueError, 'no action slot'):
            propose_task(self.proposal(decision_wall_clock_s=100000))

    def test_task_without_response_latency_is_unchanged(self):
        # Backward compatibility: the field is optional; omitting it keeps old behavior.
        plain = TaskProposal(network_id='epa-net1', fault_kind='leak', link_id='22',
            fault_parameters={'leak_area_m2': 0.01865}, threshold_mode='manual',
            min_pressure_m=20, min_supply_ratio=0.95, demand_increase_pct=7.5,
            candidate_limit=3, max_actions=2, max_wall_time_s=600)
        task = propose_task(plain)
        self.assertNotIn('response_latency', task)
        self.assertEqual(task['action_limits']['earliest_s'], task['fault']['start_s'])


if __name__ == '__main__':
    unittest.main()
