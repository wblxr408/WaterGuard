"""A failed actual trial remains in the experiment rather than disappearing."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments import submission_completion as delivery


class TrialFailureTests(unittest.TestCase):
    def test_resuming_a_terminal_trial_does_not_launch_another_model_session(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            run = dict(run_id='run-original', status='ERROR', conclusion='INCOMPLETE', budget_usage={})
            (output / 'started.json').write_text(json.dumps(run))
            def existing_only(path, data=None):
                if path == '/api/runs/run-original' and data is None:
                    return run
                self.fail('Resuming a saved trial must not launch another model session')
            with patch.object(delivery, 'api', side_effect=existing_only), patch.object(delivery, 'native_audit', return_value=run):
                self.assertEqual(delivery.live({'task_id': 'original-task'}, output), run)

    def test_failed_native_trial_reports_incomplete_provenance_without_erasing_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'artifacts/run-real-failed'
            (source / 'agh/native').mkdir(parents=True)
            (source / 'candidates').mkdir()
            candidate = source / 'candidates/baseline'
            candidate.mkdir()
            for name, value in {'plan': {}, 'result': {'artifact': {'simulation_id': 'sim-unexported'}}, 'verification': {}}.items():
                (candidate / (name + '.json')).write_text(json.dumps(value))
            task = dict(network_ref={'network_id': 'test-network'}, budget={'max_simulations': 4})
            (source / 'task.json').write_text(json.dumps(task))
            (source / 'agh/model.json').write_text(json.dumps({'model': 'actual-model'}))
            (source / 'agh/native/events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
                {'type': 'session/start', 'ts': '2026-10-12T00:00:00Z'},
                {'type': 'session/error', 'ts': '2026-10-12T00:01:00Z', 'data': {'code': 'INTERNAL_ERROR'}}]))
            output = root / 'output'
            run = dict(run_id='run-real-failed', task_ref={'task_id': 'task-real'}, status='ERROR',
                conclusion='INCOMPLETE', budget_usage={'simulations': 1, 'model_calls': 8, 'wall_time_s': 69.7})
            with patch.object(delivery, 'ROOT', root), patch.object(delivery, 'network_details', return_value={}), \
                 patch.object(delivery, 'api', return_value={'live_export_allowed': False, 'missing_items': ['native_agh_trace']}):
                result = delivery.native_audit(run, output)
            self.assertEqual(result['status'], 'ERROR')
            self.assertEqual(result['conclusion'], 'INCOMPLETE')
            self.assertFalse(result['native_export_allowed'])
            self.assertEqual(result['budget_usage']['model_calls'], 8)
            self.assertIsNone(result['best'])
            self.assertEqual(result['missing_items'], ['native_agh_trace'])
            self.assertEqual(result['candidate_audit_failures'][0]['candidate_id'], 'baseline')
            self.assertEqual(result['independently_remeasured_candidates'], 0)


if __name__ == '__main__':
    unittest.main()
