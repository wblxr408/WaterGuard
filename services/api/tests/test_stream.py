"""Durable SSE resume and delivery of events emitted after a subscription starts."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from services.api.main import create_app
from services.api.runtime import RunStore
from waterguard_contracts.core import FIXTURES, load


def frames(text):
    rows = []
    for block in text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line)
        if 'data' in fields:
            rows.append(dict(id=int(fields['id']) if 'id' in fields else None,
                             kind=fields.get('event'), data=json.loads(fields['data'])))
    return rows


class RunStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = RunStore(root / 'state.sqlite3', root / 'artifacts')
        self.task = self.store.save_task(load(FIXTURES / 'task.json'))
        self.client = TestClient(create_app(self.store))
        self.client.__enter__()
        self.run = self.store.create_run(self.task, 'fixture')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def url(self):
        return f"/api/runs/{self.run['run_id']}/stream"

    def test_snapshot_and_durable_workflow_logs_resume_without_duplicates(self):
        self.store.log(self.run['run_id'], '只包含操作摘要', origin='gateway')
        response = self.client.get(self.url() + '?follow=false')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['x-accel-buffering'], 'no')
        rows = frames(response.text)
        self.assertEqual(rows[0]['kind'], 'run')
        self.assertEqual(rows[-1]['kind'], 'stream.complete')
        durable = [row for row in rows if row['id'] is not None]
        self.assertEqual([row['id'] for row in durable], list(range(1, len(durable) + 1)))
        cutoff = durable[1]['id']
        resumed = frames(self.client.get(self.url() + '?follow=false', headers={'Last-Event-ID': str(cutoff)}).text)
        self.assertEqual([row for row in resumed if row['id'] is not None], [row for row in durable if row['id'] > cutoff])
        self.assertEqual(durable[0]['data']['mode'], 'fixture')
        self.assertEqual(durable[1]['data']['level'], 'INFO')

    def test_subscription_receives_later_log_and_status_then_closes_at_terminal(self):
        subscribed = threading.Event()
        original = self.store.stream_after
        def observed(*args):
            rows = original(*args)
            subscribed.set()
            return rows
        def publish():
            if subscribed.wait(5):
                self.store.log(self.run['run_id'], '连接后新增的日志', tool_name='wg_get_task', event_type='tool.started')
                self.store.transition(self.run['run_id'], 'CANCELLED')
        publisher = threading.Thread(target=publish)
        publisher.start()
        try:
            with patch.object(self.store, 'stream_after', side_effect=observed):
                rows = frames(self.client.get(self.url()).text)
            self.assertTrue(any(row['kind'] == 'info' and row['data']['message'] == '连接后新增的日志' for row in rows))
            self.assertEqual(rows[-1]['kind'], 'stream.complete')
            self.assertEqual(rows[-1]['data']['status'], 'CANCELLED')
        finally:
            publisher.join(5)
        self.assertFalse(publisher.is_alive())

    def test_legacy_history_is_backfilled_once_and_survives_new_store(self):
        with self.store._db() as db:
            db.execute('DELETE FROM run_stream')
        first = self.store.stream_after(self.run['run_id'])
        again = RunStore(self.store.database, self.store.artifacts).stream_after(self.run['run_id'])
        self.assertEqual(first, again)
        self.assertEqual([row['kind'] for row in first], ['workflow', 'info'])
        self.assertEqual(first[0]['data']['sequence'], first[1]['data']['event_sequence'])
        self.assertEqual(first[0]['data']['occurred_at'], first[1]['data']['occurred_at'])

    def test_invalid_cursor_and_unknown_run_are_rejected(self):
        self.assertEqual(self.client.get(self.url(), headers={'Last-Event-ID': '-1'}).status_code, 422)
        self.assertEqual(self.client.get(self.url(), headers={'Last-Event-ID': 'bad'}).status_code, 422)
        self.assertEqual(self.client.get(self.url(), headers={'Last-Event-ID': '9223372036854775808'}).status_code, 422)
        self.assertEqual(self.client.get('/api/runs/run-missing/stream').status_code, 404)

    def test_new_write_preserves_legacy_history_before_first_subscription(self):
        with self.store._db() as db:
            db.execute('DELETE FROM run_stream')
        self.store.log(self.run['run_id'], '恢复运行后的第一条日志')
        self.store.transition(self.run['run_id'], 'ERROR')
        rows = self.store.stream_after(self.run['run_id'])
        self.assertEqual([row['data']['sequence'] for row in rows if row['kind'] == 'workflow'], [1, 2])
        self.assertEqual(rows[0]['kind'], 'workflow')
        self.assertTrue(any(row['kind'] == 'info' and row['data']['level'] == 'ERROR' for row in rows))

    def test_terminal_state_does_not_close_before_its_event_is_persisted(self):
        before_event, release = threading.Event(), threading.Event()
        original = self.store.emit
        def delayed(run, *args, **kwargs):
            if run['status'] == 'CANCELLED':
                before_event.set()
                release.wait(5)
            return original(run, *args, **kwargs)
        publisher = threading.Thread(target=lambda: self.store.transition(self.run['run_id'], 'CANCELLED'))
        with patch.object(self.store, 'emit', side_effect=delayed):
            publisher.start()
            try:
                self.assertTrue(before_event.wait(5))
                rows = self.store.stream_after(self.run['run_id'])
                self.assertEqual(self.store.get_run(self.run['run_id'])['status'], 'CANCELLED')
                self.assertFalse(self.store.stream_finished(self.run['run_id'], rows[-1]['sequence']))
            finally:
                release.set()
                publisher.join(5)
        rows = self.store.stream_after(self.run['run_id'])
        self.assertFalse(self.store.stream_finished(self.run['run_id'], rows[-1]['sequence'] - 1))
        self.assertTrue(self.store.stream_finished(self.run['run_id'], rows[-1]['sequence']))
        resumed = frames(self.client.get(self.url(), headers={'Last-Event-ID': str(rows[-1]['sequence'])}).text)
        self.assertEqual([row['kind'] for row in resumed], ['run', 'stream.complete'])

    def test_live_parent_results_include_completed_child_without_waiting_for_copy(self):
        from services.api.workbench import run_results
        parent = self.store.create_run(self.task, 'live')
        child = self.store.create_run(self.task, 'benchmark')
        self.store.link_live_child(parent['run_id'], child['run_id'])
        plan = load(FIXTURES / 'candidate-b.json')
        report = load(FIXTURES / 'verification-b.json')
        path = f"{child['run_id']}/candidates/{plan['candidate_id']}"
        self.store._write_json(path + '/plan.json', plan)
        self.store._write_json(path + '/verification.json', report)
        rows = run_results(self.store, parent['run_id'])
        self.assertEqual(rows[0]['plan']['candidate_id'], plan['candidate_id'])
        self.assertEqual(rows[0]['verification']['verification_id'], report['verification_id'])

    def test_submitted_simulation_keeps_subscription_until_late_verification_is_delivered(self):
        plan = load(FIXTURES / 'candidate-b.json')
        run_id = self.run['run_id']
        self.store._write_json(f'{run_id}/submitted-plan.json', plan)
        self.store.transition(run_id, 'RUNNING', 'SIMULATING')
        self.store.transition(run_id, 'COMPLETED', 'PACKAGING')
        cursor = self.store.stream_after(run_id)[-1]['sequence']
        self.assertFalse(self.store.stream_finished(run_id, cursor))
        # Even a saved report cannot close the connection before its event is journaled.
        self.store._write_json(f"{run_id}/candidates/{plan['candidate_id']}/verification.json",
                               load(FIXTURES / 'verification-b.json'))
        self.assertFalse(self.store.stream_finished(run_id, cursor))
        subscribed = threading.Event()
        original = self.store.stream_after
        def observed(*args):
            rows = original(*args)
            subscribed.set()
            return rows
        def publish():
            if subscribed.wait(5):
                self.store.emit(self.store.get_run(run_id), 'verifier', 'verification.completed',
                                'artifact:late-verification', candidate_id=plan['candidate_id'])
        publisher = threading.Thread(target=publish)
        publisher.start()
        try:
            with patch.object(self.store, 'stream_after', side_effect=observed):
                rows = frames(self.client.get(self.url(), headers={'Last-Event-ID': str(cursor)}).text)
            self.assertTrue(any(row['kind'] == 'workflow' and row['data']['type'] == 'verification.completed' for row in rows))
            self.assertTrue(any(row['kind'] == 'info' and row['data']['origin'] == 'verifier' for row in rows))
            self.assertEqual(rows[-1]['kind'], 'stream.complete')
            self.assertFalse(self.store.stream_finished(run_id, cursor))
            self.assertTrue(self.store.stream_finished(run_id, self.store.stream_after(run_id)[-1]['sequence']))
        finally:
            publisher.join(5)
        self.assertFalse(publisher.is_alive())

    def test_cancelled_or_failed_submitted_run_does_not_wait_for_verification(self):
        plan = load(FIXTURES / 'candidate-b.json')
        for status in ('CANCELLED', 'ERROR', 'BUDGET_EXHAUSTED'):
            with self.subTest(status=status):
                run = self.store.create_run(self.task, 'fixture')
                run_id = run['run_id']
                self.store._write_json(f'{run_id}/submitted-plan.json', plan)
                self.store.transition(run_id, 'RUNNING', 'SIMULATING')
                self.store.transition(run_id, status)
                cursor = self.store.stream_after(run_id)[-1]['sequence']
                self.assertTrue(self.store.stream_finished(run_id, cursor))


if __name__ == '__main__':
    unittest.main()
