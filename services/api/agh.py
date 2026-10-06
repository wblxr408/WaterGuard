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


def _write_association(store, run_id, children):
    """Join recorded SDK tool results to application events for evidence checks."""
    run = store.get_run(run_id)
    events = [dict(application_event_id=event['event_id'], native_tool_call_ref=event['native_tool_call_ref'])
              for event in store.events_after(run_id)
              if event['agh_session_ref'] == run['agh_session_ref'] and event['native_tool_call_ref']]
    return store._write_json(f'{run_id}/agh/association.json',
                             dict(session_id=run['agh_session_ref'], agh_session_ref=run['agh_session_ref'],
                                  children=children, events=events))


def _write_probe(store, run_id, model, scenario):
    """Persist a redacted, run-local AGH readiness snapshot.

    The SDK configuration service never returns the secret, but this function
    still copies only the fields that are useful for independent evidence
    review.  It deliberately does not call a model or expose account labels.
    """
    status = bridge({'action': 'status'})
    provider = (status.get('snapshot') or {}).get('provider') or {}
    snapshot = {
        'status': 'ready', 'bridge': 'official_sdk',
        'provider': {key: provider.get(key) for key in ('id', 'model', 'route', 'credentialConfigured')},
        'available_provider_ids': [row.get('id') for row in status.get('providers', []) if isinstance(row, dict)],
        'mounted_tool_names': ['wg_inspect_network', 'wg_propose_task', 'wg_get_task', 'wg_generate_candidates',
                               'wg_submit_plan', 'wg_simulate_candidate', 'wg_get_simulation',
                               'wg_verify_candidate', 'wg_create_perturbation',
                               'wg_compare_candidates', 'wg_build_evidence', 'wg_request_clarification'],
        'acceptance_scenario': scenario,
    }
    return store._write_json(f'{run_id}/agh/probe.json', snapshot)


def _finalize_recommendation(store, run_id, task, child_runs):
    """Aggregate independent nominal and stress reports into a host conclusion."""
    required = set(task['perturbation_domain']['required'])
    nominal, stress = {}, {}
    for child in child_runs:
        child_id = child['run_id']
        perturbation_path = store._run_directory(child_id) / 'perturbation.json'
        perturbation = store._read_json(perturbation_path) if perturbation_path.is_file() else None
        for plan in store.list_candidates(child_id):
            candidate_id = plan['candidate_id']
            report_path = store._run_directory(child_id) / 'candidates' / candidate_id / 'verification.json'
            if not report_path.is_file():
                continue
            report = store._read_json(report_path)
            passed = report['validity'] == 'valid' and not report['violations']
            if perturbation is None:
                nominal[candidate_id] = (passed, report)
            elif passed:
                stress.setdefault(candidate_id, set()).update(perturbation['required_labels'])
    eligible = [candidate_id for candidate_id, (passed, _report) in nominal.items()
                if passed and required.issubset(stress.get(candidate_id, set()))]
    selected = min(eligible, key=lambda cid: (nominal[cid][1]['metrics']['shortage_m3'],
                                               nominal[cid][1]['metrics']['action_cost'], cid)) if eligible else None
    if selected:
        conclusion = 'FEASIBLE_FOUND'
    elif any(not passed for passed, _report in nominal.values()) and not required:
        conclusion = 'NO_FEASIBLE_FOUND'
    else:
        conclusion = 'INCOMPLETE'
    summary = {
        'run_id': run_id, 'required_stress_tests': sorted(required),
        'nominal_candidate_ids': sorted(nominal),
        'stress_coverage': {candidate_id: sorted(labels) for candidate_id, labels in sorted(stress.items())},
        'eligible_candidate_ids': sorted(eligible), 'selected_candidate_id': selected,
        'conclusion': conclusion,
    }
    store._write_json(f'{run_id}/recommendation-summary.json', summary)
    store.set_outcome(run_id, conclusion, selected)
    return summary


class LiveExecutor:
    """Lifecycle supervisor only. AGH performs all model and tool decisions."""
    def __init__(self, store, benchmark):
        self.store, self.benchmark = store, benchmark
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='agh')
        self.lock = threading.Lock()
        self.active = {}

    def publish_children(self, run_id):
        """Publish only server-bound, independently verified child candidates."""
        parent = self.store.get_run(run_id)
        for child in self.store.live_children(run_id):
            if child['task_ref'] != parent['task_ref']:
                raise RuntimeError('AGH child task binding mismatch')
            for plan in self.store.list_candidates(child['run_id']):
                folder = self.store._run_directory(child['run_id']) / 'candidates' / plan['candidate_id']
                if not (folder / 'verification.json').is_file() or not (folder / 'result.json').is_file():
                    continue
                result = self.store._read_json(folder / 'result.json')
                report = self.store._read_json(folder / 'verification.json')
                if result['artifact']['task_ref'] != parent['task_ref'] or report['task_ref'] != parent['task_ref'] or report['simulation_id'] != result['artifact']['simulation_id']:
                    raise RuntimeError('Verified child identity mismatch')
                for filename in ('plan.json', 'result.json', 'verification.json'):
                    self.store._write_json(f"{run_id}/candidates/{plan['candidate_id']}/{filename}", self.store._read_json(folder / filename))

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
                'code': 'SERVER_RESTARTED', 'message': 'AGH run interrupted by server restart; create a linked successor run.'})
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
            continuation_path = self.store._run_directory(run_id) / 'continuity.json'
            continuation = self.store._read_json(continuation_path) if continuation_path.is_file() else None
            request = dict(action='run', run_id=run_id, task_id=task['task_id'], model=model,
                           network_id=task['network_ref']['network_id'],
                           max_wall_time_s=task['budget']['max_wall_time_s'], max_simulations=task['budget']['max_simulations'],
                           acceptance_scenario=run.get('acceptance_scenario'), continuation=continuation)
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
            outcome, index, calls, model_calls = None, 0, {}, 0
            from .execution import project_tool_event
            execution_calls = {}
            paused = False
            while True:
                if self.store.get_run(run_id)['status'] == 'NEEDS_INPUT':
                    current = self.store.get_run(run_id)
                    if current['agh_session_ref']:
                        try:
                            bridge({'action': 'cancel', 'session_id': current['agh_session_ref']})
                        except Exception:
                            self.store._write_json(f'{run_id}/agh/cancel-warning.json', {'code': 'AGH_CANCEL_NOT_CONFIRMED'})
                    process.terminate()
                    paused = True
                    break
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
                    _write_probe(self.store, run_id, row['model'], run.get('acceptance_scenario'))
                elif row['kind'] == 'event':
                    index += 1
                    event = row['event']
                    ref = self.store._write_json(f'{run_id}/agh/projection/{index}.json', event)
                    if event['type'] == 'request/sent' and isinstance(event.get('data', {}).get('model'), dict):
                        model_calls += 1
                        self.store.transition(run_id, 'RUNNING', wall_time_s=time.monotonic()-started, model_calls=model_calls, progress_only=True)
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
                                       native_tool_call_ref=native_ref,
                                       execution=project_tool_event(event, index, run['agh_session_ref'], execution_calls))
                        if event['type'] == 'tool/call':
                            phase = {'wg_get_task': 'INTAKE', 'wg_inspect_network': 'MODELING',
                                     'wg_generate_candidates': 'PLANNING', 'wg_jev_decide': 'REVIEWING',
                                     'wg_submit_plan': 'PLANNING', 'wg_simulate_candidate': 'SIMULATING',
                                     'wg_verify_candidate': 'VERIFYING', 'wg_build_evidence': 'PACKAGING'}.get(tool_name)
                            if phase:
                                self.store.transition(run_id, 'RUNNING', phase, wall_time_s=time.monotonic()-started, progress_only=True)
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
                            self.publish_children(run_id)
                        self.store.emit(self.store.get_run(run_id), 'agh', event_type, ref,
                                        correlation_id=str(event['id']), native_tool_call_ref=native_ref,
                                        candidate_id=candidate_id, simulation_id=simulation_id)
                elif row['kind'] == 'completed':
                    outcome = row
            if process.wait(timeout=10) != 0 and not cancel.is_set() and not paused:
                raise RuntimeError('AGH session failed')
            if paused:
                return
            if cancel.is_set() or self.store.get_run(run_id)['status'] in TERMINAL:
                return
            if not outcome or outcome['result']['reason'] != 'completed':
                raise RuntimeError('AGH did not complete the task turn')
            associations = []
            # Only run IDs from recorded native wg_submit_plan results are considered.
            if not outcome['child_runs']:
                raise RuntimeError('AGH completed without a recorded WaterGuard candidate')
            # A stress review is created after the nominal candidate and is not
            # itself a wg_submit_plan result.  Trust only children linked by the
            # host to this already-bound live session, then include all of them
            # in the final provenance and evidence copy.
            child_runs = self.store.live_children(run_id)
            completed_stress_labels = set()
            for child in child_runs:
                child_id = child['run_id']
                child = self.store.get_run(child_id)
                if child['task_ref'] != run['task_ref']:
                    raise RuntimeError('AGH child task binding mismatch')
                # A model may preflight a second candidate after the shared
                # simulation budget has been consumed.  It has not entered a
                # worker yet, so record it as an explicit non-executed child
                # rather than leaving the parent permanently active.
                if child['status'] == 'QUEUED':
                    self.benchmark.cancel(child_id)
                    child = self.store.get_run(child_id)
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
                perturbation_path = self.store._run_directory(child_id) / 'perturbation.json'
                if perturbation_path.is_file():
                    perturbation = self.store._read_json(perturbation_path)
                    reports = []
                    for plan in self.store.list_candidates(child_id):
                        report_path = self.store._run_directory(child_id) / 'candidates' / plan['candidate_id'] / 'verification.json'
                        if report_path.is_file():
                            reports.append(self.store._read_json(report_path))
                    if reports and all(report['validity'] == 'valid' and not report['violations'] for report in reports):
                        completed_stress_labels.update(perturbation['required_labels'])
                import shutil
                with self.benchmark.evidence_lock:
                    shutil.copytree(self.store._run_directory(child_id), self.store._run_directory(run_id) / 'agh' / 'children' / child_id,
                                    dirs_exist_ok=True)
            _write_association(self.store, run_id, associations)
            if set(task['perturbation_domain']['required']).issubset(completed_stress_labels):
                self.store.clear_missing_items(run_id, {'required_stress_tests'})
            if not any((self.store._run_directory(run_id) / 'candidates' / plan['candidate_id'] / 'verification.json').is_file()
                       for plan in self.store.list_candidates(run_id)):
                raise RuntimeError('AGH finished without independent candidate verification')
            from services.evidence.service import _validated_agh_association
            current = self.store.get_run(run_id)
            if not _validated_agh_association(self.store._run_directory(run_id) / 'agh' / 'association.json', current):
                raise RuntimeError('AGH native event association is invalid')
            self.store.bind_agh_session(run_id, current['agh_session_ref'], current['model_config_ref'])
            _finalize_recommendation(self.store, run_id, task, child_runs)
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
