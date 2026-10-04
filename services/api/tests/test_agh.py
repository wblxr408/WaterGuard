"""Boundary tests use isolated simulated SDK responses, never Live evidence."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from services.api.main import create_app
from services.api.runtime import RunStore, ConflictError
from services.api.workbench import TaskProposal, propose_task


class AghBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = RunStore(root / 'state.sqlite3', root / 'artifacts')
        self.task = self.store.save_task(propose_task(TaskProposal(fault_kind='normal', candidate_limit=1)))

    def tearDown(self):
        self.temp.cleanup()

    def parent(self):
        run = self.store.create_run(self.task, 'live')
        self.store.transition(run['run_id'], 'RUNNING', 'PLANNING')
        self.store.bind_agh(run['run_id'], 'isolated-sdk-test-session', {'id': 'agnes-ai', 'model': 'test'})
        return self.store.get_run(run['run_id'])

    def test_missing_account_does_not_create_or_dispatch_live(self):
        with TestClient(create_app(self.store)) as client, patch('services.api.agh.bridge', return_value={'snapshot': {'provider': None}}):
            result = client.post(f"/api/tasks/{self.task['task_id']}/runs", json={'mode': 'live'})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()['detail']['code'], 'MODEL_NOT_CONFIGURED')
            self.assertFalse(self.store.recent_runs())

    def test_model_secret_is_submitted_only_and_not_journaled(self):
        with TestClient(create_app(self.store)) as client, patch('services.api.agh.bridge', return_value={'kind': 'test', 'verified': False, 'models': []}) as sdk:
            result = client.post('/api/agh/models/test', json={'providerId': 'deepseek', 'apiKey': 'isolated-secret-marker'})
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('isolated-secret-marker', result.text)
            self.assertEqual(sdk.call_args.args[0]['config']['apiKey'], 'isolated-secret-marker')
            self.assertEqual(list(self.store.artifacts.glob('run-*')), [])
            invalid = client.post('/api/agh/models/test', json={'providerId': 'invalid', 'apiKey': {'value': 'isolated-secret-marker'}})
            self.assertEqual(invalid.status_code, 422)
            self.assertNotIn('isolated-secret-marker', invalid.text)

    def test_parent_budget_is_shared_and_cancel_revokes_tools(self):
        parent = self.parent()
        first = self.store.create_run(self.task, 'benchmark')
        second = self.store.create_run(self.task, 'benchmark')
        self.store.link_live_child(parent['run_id'], first['run_id'])
        self.store.link_live_child(parent['run_id'], second['run_id'])
        self.store.reserve_live_child(first['run_id'])
        self.store.reserve_live_child(first['run_id'])
        with self.assertRaises(ConflictError):
            self.store.reserve_live_child(second['run_id'])
        self.store.settle_live_child(first['run_id'])
        self.assertEqual(self.store.get_run(parent['run_id'])['budget_usage']['simulations'], 1)
        self.store.transition(parent['run_id'], 'CANCELLED')
        with self.assertRaises(ConflictError):
            self.store.live_parent('isolated-sdk-test-session', self.task['task_id'])

    def test_native_event_identity_is_preserved_without_forging_application_events(self):
        parent = self.parent()
        event = self.store.emit(parent, 'agh', 'candidate.submitted', 'artifact:isolated-test', native_tool_call_ref='agh:isolated-sdk-test-session:tool:actual-sdk-field', correlation_id='sdk-event-id')
        self.assertEqual(event['agh_session_ref'], 'isolated-sdk-test-session')
        self.assertIsNone(event['native_reference_missing_reason'])
        plain = self.store.emit(parent, 'user', 'run.started', 'artifact:isolated-test')
        self.assertIsNone(plain['native_tool_call_ref'])
        self.assertTrue(plain['native_reference_missing_reason'])

    def test_restart_revokes_old_session_even_if_native_cancel_fails(self):
        from services.api.agh import LiveExecutor
        parent = self.parent()
        executor = LiveExecutor(self.store, Mock())
        try:
            with patch('services.api.agh.bridge', side_effect=RuntimeError('daemon offline')):
                executor.recover()
            self.assertEqual(self.store.get_run(parent['run_id'])['status'], 'ERROR')
            with self.assertRaises(ConflictError):
                self.store.live_parent('isolated-sdk-test-session', self.task['task_id'])
            warning = self.store._read_json(self.store._run_directory(parent['run_id']) / 'agh/cancel-warning.json')
            self.assertEqual(warning['code'], 'AGH_CANCEL_NOT_CONFIRMED')
        finally:
            executor.close()


if __name__ == '__main__':
    unittest.main()
