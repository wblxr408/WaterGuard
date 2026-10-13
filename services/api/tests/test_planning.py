"""Physical source validation and API admission; these are not native AGH logs."""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from services.api.main import create_app
from services.api.runtime import RunStore
from services.api.workbench import TaskProposal, propose_task, network_details, benchmark_plans
from services.api.planning import create_revision, load_revision_plan, diagnose_failure, summarize_failure, revision_records
from services.hydraulics.runtime import PhysicalAdapter
from services.verifier.service import verify
from waterguard_contracts.core import task_hash, plan_hash


class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.task = propose_task(TaskProposal(network_id='epa-net3', fault_kind='closure',
            threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95, candidate_limit=3, max_actions=2))
        cls.details = network_details('epa-net3')
        pump = next(f for f in cls.details['catalog']['facilities'] if f['link_id'] == '10')
        cls.task['control_policy']['overrides'] = [dict(link_id='10', control_names=pump['native_control_names'], reason='Shared physical test of permitted existing backup pump')]
        cls.task['action_limits']['allowed_link_ids'].append('10')
        cls.task['budget']['max_revisions'] = 1
        cls.task['constraint_hash'] = task_hash(cls.task)
        cls.plan = benchmark_plans(cls.task, cls.details['catalog'])[0]
        output = Path(cls.shared.name) / 'simulations'
        cls.result = PhysicalAdapter(output).simulate(cls.task, cls.plan)
        cls.simulation = output / cls.result['artifact']['simulation_id']
        cls.report = verify(cls.task, cls.plan, cls.result, cls.details['catalog'], cls.details['scene'], artifact_dir=cls.simulation)
        assert cls.report['validity'] == 'valid' and cls.report['violations']

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = RunStore(self.root / 'state.sqlite3', self.root / 'artifacts')
        self.store.save_task(self.task)
        self.parent = self.store.create_run(self.task, 'live')
        self.store.transition(self.parent['run_id'], 'RUNNING', 'PLANNING')
        self.store.bind_agh(self.parent['run_id'], 'isolated-planning-session', {'id': 'agnes-ai', 'model': 'test'})
        self.parent = self.store.get_run(self.parent['run_id'])
        self.source = self.store.create_run(self.task, 'benchmark')
        self.store.link_live_child(self.parent['run_id'], self.source['run_id'])
        self.store.transition(self.source['run_id'], 'RUNNING', 'SIMULATING')
        self.store.transition(self.source['run_id'], 'COMPLETED')
        self.store._write_json(f"{self.source['run_id']}/submitted-plan.json", self.plan)
        for filename, value in [('plan.json', self.plan), ('result.json', self.result), ('verification.json', self.report)]:
            self.store._write_json(f"{self.source['run_id']}/candidates/{self.plan['candidate_id']}/{filename}", value)
        shutil.copytree(self.simulation, self.store._run_directory(self.source['run_id']) / 'simulations' / self.result['artifact']['simulation_id'])
        self.action = dict(kind='pump_on', link_id='10', simulation_time_s=3600)

    def tearDown(self):
        self.temp.cleanup()

    def revision(self, actions=None):
        return create_revision(self.store, self.parent, self.task, self.source['run_id'],
                               actions or [self.action], 'Actual pressure failure requires testing the permitted backup pump.')

    def test_real_failure_diagnosis_and_idempotent_revision_preserve_lineage(self):
        diagnosis = diagnose_failure(self.store, self.source['run_id'])
        self.assertEqual(diagnosis['violation_count'], len(self.report['violations']))
        self.assertGreater(diagnosis['failed_node_seconds'], 0)
        self.assertIn('10', [f['link_id'] for f in diagnosis['permitted_facilities']])
        record = self.revision()
        self.assertEqual(record, self.revision())
        self.assertEqual(len(revision_records(self.store, self.parent)), 1)
        self.assertEqual(record['plan']['parent_candidate_id'], self.plan['candidate_id'])
        self.assertEqual(record['plan']['source'], 'agh')
        self.assertEqual(load_revision_plan(self.store, self.parent, self.task, record['plan']['candidate_id']), record['plan'])
        self.assertEqual(self.store.get_run(self.parent['run_id'])['budget_usage']['simulations'], 0)

    def test_rejects_fault_native_control_time_and_switch_violations_without_registering(self):
        cases = [([dict(kind='link_open', link_id='123', simulation_time_s=3600)], 'frozen fault'),
                 ([dict(kind='pump_off', link_id='335', simulation_time_s=3600)], 'Native control conflict|[Uu]nauthorized'),
                 ([dict(self.action, simulation_time_s=4501)], 'sample boundary'),
                 ([self.action, dict(kind='pump_off', link_id='10', simulation_time_s=3600)], 'frequent controls')]
        for actions, message in cases:
            with self.subTest(actions=actions), self.assertRaisesRegex(ValueError, message):
                self.revision(actions)
        self.assertEqual(revision_records(self.store, self.parent), [])

    def test_revision_budget_cannot_be_bypassed_by_a_new_reason_or_time(self):
        self.revision()
        with self.assertRaisesRegex(ValueError, 'revision budget'):
            self.revision([dict(self.action, simulation_time_s=4500)])
        self.assertEqual(len(revision_records(self.store, self.parent)), 1)

    def test_tampered_raw_data_and_rewritten_plan_are_rejected(self):
        record = self.revision()
        path = self.store._run_directory(self.parent['run_id']) / 'planning/revisions' / (record['plan']['candidate_id'] + '.json')
        altered = copy.deepcopy(record)
        altered['plan']['actions'][0]['simulation_time_s'] = 4500
        altered['plan']['plan_hash'] = plan_hash(altered['plan'])
        self.store._write_json(path.relative_to(self.store.artifacts).as_posix(), altered)
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            load_revision_plan(self.store, self.parent, self.task, record['plan']['candidate_id'])
        raw = self.store._run_directory(self.source['run_id']) / 'simulations' / self.result['artifact']['simulation_id'] / 'raw-node-pressure.csv'
        raw.write_bytes(raw.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash|Hash'):
            diagnose_failure(self.store, self.source['run_id'])

    def test_source_from_another_session_or_stress_cannot_be_used(self):
        other = self.store.create_run(self.task, 'live')
        self.store.transition(other['run_id'], 'RUNNING', 'PLANNING')
        with self.assertRaisesRegex(ValueError, 'another AGH session'):
            create_revision(self.store, self.store.get_run(other['run_id']), self.task, self.source['run_id'], [self.action], 'A cross-session revision must fail.')
        self.store._write_json(f"{self.source['run_id']}/perturbation.json", {'test': True})
        with self.assertRaisesRegex(ValueError, 'nominal calculation'):
            self.revision()

    def test_api_requires_bound_session_and_never_accepts_model_metrics(self):
        app = create_app(self.store)
        client = TestClient(app)  # no recovery lifecycle; this is an isolated active-session fixture
        path = f"/api/tasks/{self.task['task_id']}/candidate-revisions"
        body = dict(source_run_id=self.source['run_id'], actions=[self.action], reason='Actual source pressure failure merits a backup pump test.')
        self.assertEqual(client.post(path, json=body).status_code, 409)
        headers = {'X-WG-AGH-Session': 'isolated-planning-session'}
        self.assertEqual(client.post(path, json=dict(body, metrics={'min_pressure_m': 99}), headers=headers).status_code, 422)
        self.assertEqual(client.post(path, json=dict(body, actions=[dict(self.action, simulation_time_s=True)]), headers=headers).status_code, 422)
        response = client.post(path, json=body, headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        candidate_id = response.json()['plan']['candidate_id']
        submission = client.post(f"/api/tasks/{self.task['task_id']}/candidate-runs/{candidate_id}", headers=headers)
        self.assertEqual(submission.status_code, 201, submission.text)
        self.assertEqual(self.store.list_candidates(submission.json()['run_id'])[0], response.json()['plan'])
        self.assertEqual(client.get(f"/api/tasks/{self.task['task_id']}/candidate-plans", headers=headers).json()[-1]['candidate_id'], candidate_id)
        client.close()
        app.state.benchmark_executor.close()

    def test_diagnosis_reports_observed_facility_effects_for_permitted_facilities_only(self):
        diagnosis = diagnose_failure(self.store, self.source['run_id'])
        effects = diagnosis['facility_effects']
        permitted = set(self.task['action_limits']['allowed_link_ids']) - {self.task['fault']['link_id']}
        self.assertTrue(effects)
        self.assertTrue({row['link_id'] for row in effects} <= permitted)
        failed = {v['node_id'] for v in self.report['violations']}
        for row in effects:
            self.assertIn('observed', row['basis'])
            self.assertTrue(set(row['downstream_failed_nodes']) <= failed)
        self.assertGreater(max(row['downstream_failed_demand_share'] for row in effects), 0)
        self.assertEqual(effects[-1]['direction'], 'idle')
        self.assertEqual(next(row for row in effects if row['link_id'] == '10')['direction'], 'idle')

    def test_facility_effects_are_deterministic_and_declared_as_observation(self):
        first = diagnose_failure(self.store, self.source['run_id'])
        second = diagnose_failure(self.store, self.source['run_id'])
        self.assertEqual(first['facility_effects'], second['facility_effects'])
        self.assertIn('not a predicted', first['effect_interpretation'])

    def test_revision_records_do_not_depend_on_observed_effects(self):
        record = self.revision()
        self.assertNotIn('facility_effects', record['diagnosis'])
        self.assertEqual(load_revision_plan(self.store, self.parent, self.task, record['plan']['candidate_id']), record['plan'])

    def test_failure_duration_unions_pressure_and_supply_on_the_same_node(self):
        report = copy.deepcopy(self.report)
        report['violations'] = [dict(code='MIN_PRESSURE', node_id='20', start_s=3600, end_s=3900, observed=1., threshold=20., unit='m'),
                                dict(code='MIN_SUPPLY_RATIO', node_id='20', start_s=3600, end_s=3900, observed=.5, threshold=.95, unit='ratio')]
        result = summarize_failure(self.task, self.plan, report)
        self.assertEqual(result['failed_node_seconds'], 300)
        self.assertEqual(sum(result['constraint_node_seconds'].values()), 600)
        self.assertEqual(result['worst_nodes'][0]['failed_windows'], [[3600, 3900]])


if __name__ == '__main__':
    unittest.main()
