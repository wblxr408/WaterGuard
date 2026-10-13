"""Delivery provenance and refinement gain are derived from independently verified reports."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services.api.runtime  # noqa: F401  (puts the contracts package on sys.path)
from services.api.agh import _finalize_recommendation
from services.api.runtime import RunStore
from waterguard_contracts.core import FIXTURES, load, plan_hash, task_hash, task_ref

OBJECTIVES = ['maximize_emergency_node_coverage', 'maximize_emergency_mean_availability',
              'maximize_min_node_availability', 'maximize_mean_node_availability', 'minimize_shortage']


def values(served, *, shortage=10., normal_mean=.5):
    """Two assessed nodes: served of them keep basic service."""
    return dict(served=served, shortage=shortage, normal_mean=normal_mean)


class DeliveryProvenanceTests(unittest.TestCase):
    def deliver(self, candidates):
        """candidates: list of (candidate_id, plan source or None for no-action, values)."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = RunStore(Path(temp.name) / 'db', Path(temp.name) / 'artifacts')
        task = load(FIXTURES / 'task.json')
        task['objectives'] = list(OBJECTIVES)
        task['emergency_floor'] = dict(min_pressure_m=15, min_supply_ratio=.75, source='test')
        task['constraint_hash'] = task_hash(task)
        task = store.save_task(task)
        parent = store.create_run(task, 'live')
        store.transition(parent['run_id'], 'RUNNING')
        for cid, source, v in candidates:
            child = store.create_run(task, 'benchmark')
            store.link_live_child(parent['run_id'], child['run_id'])
            plan = load(FIXTURES / 'candidate-b.json')
            plan.update(candidate_id=cid, task_ref=task_ref(task), source=source or 'enumeration')
            if source is None:
                plan['actions'] = []
            plan['plan_hash'] = plan_hash(plan)
            result = load(FIXTURES / 'result-b.json')
            result['artifact'].update(task_ref=task_ref(task), simulation_id='sim-' + cid)
            report = load(FIXTURES / 'verification-a.json')
            report.update(task_ref=task_ref(task), candidate_id=cid, simulation_id='sim-' + cid)
            served = v['served']
            report['metrics'].update(min_node_time_pass_ratio=.0, mean_node_time_pass_ratio=v['normal_mean'], shortage_m3=v['shortage'],
                assessed_node_count=2, nodes_reaching_emergency_service=served, emergency_mean_node_time_pass_ratio=served / 2,
                node_availability=[dict(node_id=n, assessed_duration_s=100, normal_supply_duration_s=int(100 * v['normal_mean']),
                    normal_supply_time_ratio=v['normal_mean'], emergency_supply_duration_s=100 if i < served else 20,
                    emergency_supply_time_ratio=1. if i < served else .2) for i, n in enumerate(['J1', 'J2'])])
            for name, value in [('plan.json', plan), ('result.json', result), ('verification.json', report)]:
                store._write_json(f"{child['run_id']}/candidates/{cid}/{name}", value)
        with patch('services.evidence.service._validated_agh_association', return_value=True):
            return _finalize_recommendation(store, parent['run_id'], task, store.live_children(parent['run_id']))

    BASE = ('baseline', None, values(0, shortage=30, normal_mean=.1))

    def test_refinement_that_protects_more_users_becomes_the_delivery_and_reports_its_gain(self):
        summary = self.deliver([self.BASE, ('first-aid', 'enumeration', values(1, shortage=20)),
                                ('agh-revision-1', 'agh', values(2, shortage=12))])
        self.assertEqual(summary['mitigation_candidate_id'], 'agh-revision-1')
        self.assertEqual(summary['delivery_source'], 'refinement')
        gain = summary['refinement_gain']
        self.assertTrue(gain['improved'])
        self.assertEqual((gain['first_aid_candidate_id'], gain['refinement_candidate_id']), ('first-aid', 'agh-revision-1'))
        self.assertEqual(gain['nodes_reaching_emergency_service'], 1)
        self.assertAlmostEqual(gain['shortage_reduction_m3'], 8)

    def test_refinement_that_is_not_strictly_better_leaves_the_first_aid_delivery_with_zero_gain(self):
        summary = self.deliver([self.BASE, ('first-aid', 'enumeration', values(1, shortage=20)),
                                ('agh-revision-1', 'agh', values(1, shortage=20))])
        self.assertEqual(summary['mitigation_candidate_id'], 'first-aid')
        self.assertEqual(summary['delivery_source'], 'first_aid')
        gain = summary['refinement_gain']
        self.assertFalse(gain['improved'])
        self.assertEqual(gain['nodes_reaching_emergency_service'], 0)
        self.assertEqual(gain['shortage_reduction_m3'], 0)

    def test_a_worse_refinement_is_kept_on_record_but_never_claimed_as_gain(self):
        summary = self.deliver([self.BASE, ('first-aid', 'enumeration', values(2, shortage=12)),
                                ('agh-revision-1', 'agh', values(1, shortage=25))])
        self.assertEqual(summary['mitigation_candidate_id'], 'first-aid')
        gain = summary['refinement_gain']
        self.assertFalse(gain['improved'])
        self.assertEqual(gain['nodes_reaching_emergency_service'], 0)
        self.assertEqual(gain['raw_difference']['nodes_reaching_emergency_service'], -1)

    def test_without_any_refinement_there_is_no_gain_claim(self):
        summary = self.deliver([self.BASE, ('first-aid', 'enumeration', values(1, shortage=20))])
        self.assertEqual(summary['delivery_source'], 'first_aid')
        self.assertIsNone(summary['refinement_gain'])

    def test_no_delivery_has_no_source(self):
        summary = self.deliver([self.BASE])
        self.assertIsNone(summary['delivery_source'])
        self.assertIsNone(summary['refinement_gain'])


if __name__ == '__main__':
    unittest.main()
