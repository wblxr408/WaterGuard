"""Public execution summaries: isolated SDK records, not live-run evidence."""
import tempfile
import json
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from services.api.execution import execution_trace, project_tool_event
from services.api.main import create_app
from services.api.runtime import RunStore
from services.api.workbench import TaskProposal, propose_task


class ExecutionTests(unittest.TestCase):
    def test_revision_refusal_is_failed_even_when_the_native_http_call_returned(self):
        calls = {}
        project_tool_event({'type': 'tool/call', 'data': {'toolUseId': 'revision-refused', 'name': 'wg_revise_plan', 'args': {}}},
                           1, 'isolated-session', calls)
        result = project_tool_event({'type': 'tool/result', 'data': {'toolUseId': 'revision-refused', 'isError': False,
            'structured': {'accepted': False, 'code': 'INVALID_REVISION', 'message': 'Unauthorized revision facility: 330'}}},
            2, 'isolated-session', calls)
        self.assertTrue(result['failed'])
        self.assertEqual(result['summary'], '方案修订被拒绝 · INVALID_REVISION')
        self.assertNotIn('candidate_plan', result)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = RunStore(root / 'state.sqlite3', root / 'artifacts')
        task = self.store.save_task(propose_task(TaskProposal(fault_kind='normal', candidate_limit=1)))
        self.run = self.store.create_run(task, 'live')
        self.store.bind_agh(self.run['run_id'], 'isolated-session', {'id': 'agnes-ai', 'model': 'test'})

    def tearDown(self):
        self.temp.cleanup()

    def test_old_projection_recovers_order_and_only_exposes_safe_fields(self):
        call = {'type': 'tool/call', 'ts': '2026-10-06T11:00:00Z', 'data': {
            'toolUseId': 'native-call', 'name': 'wg_submit_plan', 'args': {
                'plan_json': json.dumps({'candidate_id': 'candidate-a', 'parent_candidate_id': 'candidate-parent', 'reason': 'private-model-text'}),
                'apiKey': 'never-export-this-secret'}}}
        result = {'type': 'tool/result', 'ts': '2026-10-06T11:00:01Z', 'data': {
            'toolUseId': 'native-call', 'isError': False, 'structured': {'run_id': 'child-a', 'apiKey': 'never-export-this-secret'}}}
        for sequence, value in [(2, call), (10, result)]:
            self.store._write_json(f"{self.run['run_id']}/agh/projection/{sequence}.json", value)
        client = TestClient(create_app(self.store))
        response = client.get(f"/api/runs/{self.run['run_id']}/execution")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('never-export-this-secret', response.text)
        self.assertNotIn('private-model-text', response.text)
        rows = response.json()['events']
        self.assertEqual([r['sequence'] for r in rows], [2, 10])
        self.assertEqual(rows[1]['candidate_id'], 'candidate-a')
        self.assertEqual(rows[0]['parent_candidate_id'], 'candidate-parent')
        self.assertEqual(rows[1]['target_run_id'], 'child-a')
        self.assertEqual(rows[0]['native_tool_call_ref'], rows[1]['native_tool_call_ref'])
        self.assertIsNone(response.json()['evidence'])
        self.assertEqual(client.get('/api/runs/run-does-not-exist/execution').status_code, 404)
        client.app.state.benchmark_executor.close()

    def test_orphan_results_and_non_business_calls_are_excluded(self):
        self.assertIsNone(project_tool_event({'type': 'tool/result', 'data': {'toolUseId': 'orphan'}}, 1, 'session', {}))
        self.assertIsNone(project_tool_event({'type': 'tool/call', 'data': {'toolUseId': 'foreign', 'name': 'shell'}}, 1, 'session', {}))
        # Native-looking files in a benchmark run cannot establish Agnes provenance.
        task = self.store.get_task(self.run['task_ref']['task_id'], 1)
        benchmark = self.store.create_run(task, 'benchmark')
        self.store._write_json(f"{benchmark['run_id']}/agh/projection/1.json", {'type': 'tool/call', 'data': {'toolUseId': 'fake', 'name': 'wg_get_task'}})
        self.assertEqual(execution_trace(self.store, benchmark['run_id'])['events'], [])

    def test_jev_unconfigured_and_tool_failure_are_distinct(self):
        calls = {}
        project_tool_event({'type': 'tool/call', 'data': {'toolUseId': 'jev', 'name': 'wg_jev_decide'}}, 1, 's', calls)
        result = project_tool_event({'type': 'tool/result', 'data': {'toolUseId': 'jev', 'isError': False, 'structured': {'configured': False}}}, 2, 's', calls)
        self.assertFalse(result['jev_configured'])
        self.assertFalse(result['failed'])
        self.assertEqual(result['summary'], '未连接 · Agnes 继续规划')
        result = project_tool_event({'type': 'tool/result', 'data': {'toolUseId': 'jev', 'isError': True, 'text': 'private-error-secret'}}, 3, 's', calls)
        self.assertTrue(result['failed'])
        self.assertNotIn('private-error-secret', str(result))

    def test_candidate_and_assessment_projection_is_whitelisted_and_replayable(self):
        calls = {}
        project_tool_event({'type': 'tool/call', 'data': {'toolUseId': 'plans', 'name': 'wg_generate_candidates'}}, 1, 's', calls)
        result = project_tool_event({'type': 'tool/result', 'data': {'toolUseId': 'plans', 'structured': [
            {'candidate_id': 'a', 'parent_candidate_id': 'p', 'actions': [{'kind': 'link_close', 'link_id': '10', 'simulation_time_s': 3600, 'secret': 'private'}], 'reason': 'private'},
            {'candidate_id': 'b', 'actions': []}]}}, 2, 's', calls)
        self.assertEqual(result['candidates'][0]['actions'][0]['link_id'], '10')
        self.assertEqual(result['candidates'][1]['actions'], [])
        self.assertNotIn('private', str(result))
        project_tool_event({'type': 'tool/call', 'data': {'toolUseId': 'verify', 'name': 'wg_verify_candidate'}}, 3, 's', calls)
        result = project_tool_event({'type': 'tool/result', 'data': {'toolUseId': 'verify', 'structured': [{
            'candidate_id': 'a', 'validity': 'valid', 'admission': {'nominal_constraints_pass': 'pass', 'required_stress_tests_pass': 'pending', 'secret': 'private'},
            'metrics': {'min_pressure_m': 13.5, 'supply_ratio': .862, 'secret': 'private'}, 'violations': [], 'reason': 'private'}]}}, 4, 's', calls)
        self.assertEqual(result['assessment']['admission']['required_stress_tests_pass'], 'pending')
        self.assertEqual(result['assessment']['metrics']['min_pressure_m'], 13.5)
        self.assertFalse(result['failed'])
        self.assertNotIn('private', str(result))

    def test_jev_choice_requires_a_configured_provider(self):
        calls = {}
        project_tool_event({'type': 'tool/call', 'data': {'toolUseId': 'jev', 'name': 'wg_jev_decide'}}, 1, 's', calls)
        for configured in [False, True]:
            row = project_tool_event({'type': 'tool/result', 'data': {'toolUseId': 'jev', 'structured': {
                'configured': configured, 'answers': {'next': {'choice': 'a'}}}}}, 2, 's', calls)
            self.assertEqual(row.get('jev_choice'), 'a' if configured else None)

    def test_bounded_feedback_preserves_full_violation_count(self):
        from services.api.execution import assessment_summary
        summary = assessment_summary({'validity': 'valid', 'violations': [{}]*12, 'violation_count': 4217})
        self.assertEqual(summary['violation_count'], 4217)
        for invalid_count in (True, -1, 3, '4217'):
            summary = assessment_summary({'violations': [{}]*12, 'violation_count': invalid_count})
            self.assertEqual(summary['violation_count'], 12)

    def test_business_rejection_stays_failed_even_when_transport_returned_successfully(self):
        calls = {}
        project_tool_event({'type': 'tool/call', 'data': {'toolUseId': 'bad', 'name': 'wg_submit_plan'}}, 1, 's', calls)
        row = project_tool_event({'type': 'tool/result', 'data': {'toolUseId': 'bad', 'isError': False,
            'structured': {'accepted': False, 'code': 'INVALID_ACTION'}}}, 2, 's', calls)
        self.assertTrue(row['failed'])
        self.assertEqual(row['summary'], '动作被拒绝 · INVALID_ACTION')


if __name__ == '__main__':
    unittest.main()
