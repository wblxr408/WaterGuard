"""AGH SDK process boundary; model credentials never enter application artifacts."""
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.start_agh import node_runtime
from .runtime import ROOT
from .runtime import ConflictError, TERMINAL


def command():
    return [node_runtime(), '--import', (ROOT / 'vendor/agnes-harness/node_modules/tsx/dist/loader.mjs').as_uri(),
            str(ROOT / 'scripts/agh_bridge.mjs')]


def bridge(request):
    completed = subprocess.run(command(), input=json.dumps(request), text=True, encoding='utf-8',
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, timeout=100)
    rows = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith('{')]
    if not rows:
        raise RuntimeError('AGH SDK bridge is unavailable')
    result = rows[-1]
    if result.get('kind') == 'error':
        raise RuntimeError(result['message'])
    return result


class LiveExecutor:
    """Lifecycle supervisor only. AGH performs all model and tool decisions."""
    def __init__(self, store, benchmark):
        self.store, self.benchmark = store, benchmark
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='agh')
        self.lock = threading.Lock()
        self.active = {}

    def dispatch(self, run, model):
        with self.lock:
            if run['status'] in TERMINAL or run['run_id'] in self.active:
                return
            event = threading.Event()
            self.active[run['run_id']] = event
            self.pool.submit(self.execute, run['run_id'], model, event)

    def recover(self):
        for run in self.store.active_lives():
            # Revoke local tool admission first; an old model session cannot resume a frozen job.
            self.store.transition(run['run_id'], 'ERROR')
            self.store._write_json(f"{run['run_id']}/error.json", {
                'code': 'SERVER_RESTARTED', 'message': 'AGH run interrupted by server restart; create a new run.'})
            if run['agh_session_ref']:
                try:
                    bridge({'action': 'cancel', 'session_id': run['agh_session_ref']})
                except Exception:
                    self.store._write_json(f"{run['run_id']}/agh/cancel-warning.json", {'code': 'AGH_CANCEL_NOT_CONFIRMED'})
            self.benchmark.package(run['run_id'])

    def execute(self, run_id, model, cancel):
        started, process = time.monotonic(), None
        try:
            if cancel.is_set():
                return
            run = self.store.transition(run_id, 'RUNNING', 'PLANNING')
            task = self.store.get_task(run['task_ref']['task_id'], run['task_ref']['revision'])
            process = subprocess.Popen(command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, encoding='utf-8', cwd=ROOT,
                                       env={**os.environ, 'WG_ARTIFACT_ROOT': str(self.store.artifacts.resolve())})
            request = dict(action='run', run_id=run_id, task_id=task['task_id'], model=model,
                           max_wall_time_s=task['budget']['max_wall_time_s'], max_simulations=task['budget']['max_simulations'])
            process.stdin.write(json.dumps(request) + '\n')
            process.stdin.flush()
            deadline = started + task['budget']['max_wall_time_s'] + 20
            # Drain stdout independently so cancellation can stop a stalled SDK process.
            import queue
            output = queue.Queue()
            def drain():
                for line in process.stdout:
                    output.put(line)
                output.put(None)
            threading.Thread(target=drain, daemon=True).start()
            outcome, index, calls = None, 0, {}
            while True:
                if cancel.is_set() or time.monotonic() > deadline:
                    current = self.store.get_run(run_id)
                    if current['agh_session_ref']:
                        try:
                            bridge({'action': 'cancel', 'session_id': current['agh_session_ref']})
                        except Exception:
                            self.store._write_json(f'{run_id}/agh/cancel-warning.json', {'code': 'AGH_CANCEL_NOT_CONFIRMED'})
                    for child in self.store.live_children(run_id):
                        if child['status'] not in TERMINAL:
                            self.benchmark.cancel(child['run_id'])
                    process.terminate()
                    if not cancel.is_set():
                        self.store.transition(run_id, 'BUDGET_EXHAUSTED')
                    break
                try:
                    line = output.get(timeout=.25)
                except queue.Empty:
                    continue
                if line is None:
                    break
                if not line.startswith('{'):
                    continue
                row = json.loads(line)
                if row['kind'] == 'error':
                    raise RuntimeError(row['message'])
                if row['kind'] == 'session':
                    run = self.store.bind_agh(run_id, row['session_id'], row['model'])
                    process.stdin.write(json.dumps({'bound_session_id': row['session_id']}) + '\n')
                    process.stdin.close()
                    self.store.log(run_id, 'AGH 原生会话已绑定冻结任务', event_type='session.bound')
                elif row['kind'] == 'event':
                    index += 1
                    event = row['event']
                    ref = self.store._write_json(f'{run_id}/agh/projection/{index}.json', event)
                    native_id = event.get('data', {}).get('toolUseId') if isinstance(event.get('data'), dict) else None
                    native_ref = f"agh:{run['agh_session_ref']}:tool:{native_id}" if native_id else None
                    if event['type'] == 'tool/call':
                        calls[native_id] = event['data'].get('name')
                    tool_name = calls.get(native_id)
                    if tool_name and tool_name.startswith('wg_') and event['type'] in {'tool/call', 'tool/result'}:
                        failed = bool(event['data'].get('isError'))
                        self.store.log(run_id, f"{'开始调用' if event['type'] == 'tool/call' else '调用返回'} {tool_name}",
                                       level='ERROR' if failed else 'INFO', tool_name=tool_name,
                                       event_type='tool.started' if event['type'] == 'tool/call' else 'tool.completed',
                                       native_tool_call_ref=native_ref)
                    event_type = {'wg_submit_plan': 'candidate.submitted', 'wg_simulate_candidate': 'simulation.completed',
                                  'wg_verify_candidate': 'verification.completed'}.get(calls.get(native_id))
                    # Other raw native events stay in the projection files, without invented business labels.
                    if event['type'] == 'tool/result' and not event['data'].get('isError') and event_type:
                        structured = event['data'].get('structured')
                        candidate_id = simulation_id = None
                        if tool_name == 'wg_submit_plan' and isinstance(structured, dict) and structured.get('run_id'):
                            plans = self.store.list_candidates(structured['run_id'])
                            candidate_id = plans[0]['candidate_id'] if plans else None
                        elif tool_name == 'wg_simulate_candidate' and isinstance(structured, dict):
                            results = structured.get('results') or []
                            if results:
                                candidate_id = results[0]['plan']['candidate_id']
                                simulation_id = (results[0].get('artifact') or {}).get('simulation_id')
                        elif tool_name == 'wg_verify_candidate' and isinstance(structured, list) and structured:
                            candidate_id, simulation_id = structured[0]['candidate_id'], structured[0]['simulation_id']
                        self.store.emit(self.store.get_run(run_id), 'agh', event_type, ref,
                                        correlation_id=str(event['id']), native_tool_call_ref=native_ref,
                                        candidate_id=candidate_id, simulation_id=simulation_id)
                elif row['kind'] == 'completed':
                    outcome = row
            if process.wait(timeout=10) != 0 and not cancel.is_set():
                raise RuntimeError('AGH session failed')
            if cancel.is_set() or self.store.get_run(run_id)['status'] in TERMINAL:
                return
            if not outcome or outcome['result']['reason'] != 'completed':
                raise RuntimeError('AGH did not complete the task turn')
            associations = []
            # Only run IDs from recorded native wg_submit_plan results are considered.
            for child_id in outcome['child_runs']:
                child = self.store.get_run(child_id)
                if child['task_ref'] != run['task_ref']:
                    raise RuntimeError('AGH child task binding mismatch')
                if child['status'] not in TERMINAL:
                    raise RuntimeError('AGH finished while a submitted calculation is still active')
                for plan in self.store.list_candidates(child_id):
                    cid = plan['candidate_id']
                    child_root = self.store._run_directory(child_id) / 'candidates' / cid
                    for filename in ('plan.json', 'result.json', 'verification.json'):
                        path = child_root / filename
                        if path.is_file():
                            self.store._write_json(f'{run_id}/candidates/{cid}/{filename}', self.store._read_json(path))
                associations.append(dict(child_run_id=child_id, task_ref=child['task_ref']))
                import shutil
                with self.benchmark.evidence_lock:
                    shutil.copytree(self.store._run_directory(child_id), self.store._run_directory(run_id) / 'agh' / 'children' / child_id,
                                    dirs_exist_ok=True)
            self.store._write_json(f'{run_id}/agh/association.json', dict(session_id=run['agh_session_ref'], children=associations))
            if not associations:
                raise RuntimeError('AGH completed without a recorded WaterGuard candidate')
            if not any((self.store._run_directory(run_id) / 'candidates' / plan['candidate_id'] / 'verification.json').is_file()
                       for plan in self.store.list_candidates(run_id)):
                raise RuntimeError('AGH finished without independent candidate verification')
            self.store.transition(run_id, 'COMPLETED', 'PACKAGING', wall_time_s=time.monotonic()-started)
        except Exception as error:
            self.store._write_json(f'{run_id}/error.json', dict(code='AGH_FAILED', message=str(error)))
            if self.store.get_run(run_id)['status'] not in TERMINAL:
                self.store.transition(run_id, 'ERROR', wall_time_s=time.monotonic()-started)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            try:
                self.benchmark.package(run_id)
            finally:
                with self.lock:
                    self.active.pop(run_id, None)

    def cancel(self, run_id):
        with self.lock:
            run = self.store.get_run(run_id)
            if run['status'] in TERMINAL:
                raise ConflictError('Terminal run cannot be cancelled')
            result = self.store.transition(run_id, 'CANCELLED')
            if run_id in self.active:
                self.active[run_id].set()
            return result

    def close(self):
        with self.lock:
            for event in self.active.values():
                event.set()
            for run_id in self.active:
                if self.store.get_run(run_id)['status'] not in TERMINAL:
                    self.store.transition(run_id, 'CANCELLED')
        self.pool.shutdown(wait=True)
