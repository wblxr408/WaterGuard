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
                               'wg_compare_candidates', 'wg_build_evidence', 'wg_request_clarification',
                               'wg_diagnose_failure', 'wg_revise_plan'],
        'acceptance_scenario': scenario,
    }
    return store._write_json(f'{run_id}/agh/probe.json', snapshot)


def _prefer_first_aid(task, nominal, pool):
    """Best by the frozen order; a model refinement must be strictly better than the best first-aid plan to replace it."""
    from services.hydraulics.configuration import ranking_key
    key = lambda cid: ranking_key(task, nominal[cid][1])
    first_aid = [cid for cid in pool if nominal[cid][2]['source'] != 'agh']
    refined = [cid for cid in pool if nominal[cid][2]['source'] == 'agh']
    best_first_aid = min(first_aid, key=key) if first_aid else None
    best_refined = min(refined, key=key) if refined else None
    if best_refined and (best_first_aid is None or key(best_refined)[:-1] < key(best_first_aid)[:-1]):
        return best_refined
    return best_first_aid


def _refinement_gain(task, nominal, pool):
    """What Agnes refinement added over the best fixed first-aid plan; zero unless strictly better in the frozen order."""
    from services.hydraulics.configuration import ranking_key
    first_aid = [cid for cid in pool if nominal[cid][2]['source'] != 'agh']
    refined = [cid for cid in pool if nominal[cid][2]['source'] == 'agh']
    if not first_aid or not refined:
        return None
    key = lambda cid: ranking_key(task, nominal[cid][1])
    base, tried = min(first_aid, key=key), min(refined, key=key)
    a, b = nominal[base][1]['metrics'], nominal[tried][1]['metrics']
    count = lambda m, name: (m.get(name) or 0)
    raw = dict(nodes_reaching_emergency_service=count(b, 'nodes_reaching_emergency_service') - count(a, 'nodes_reaching_emergency_service'),
               priority_nodes_reaching_emergency_service=count(b, 'priority_nodes_reaching_emergency_service') - count(a, 'priority_nodes_reaching_emergency_service'),
               emergency_mean_node_time_pass_ratio=count(b, 'emergency_mean_node_time_pass_ratio') - count(a, 'emergency_mean_node_time_pass_ratio'),
               mean_node_time_pass_ratio=count(b, 'mean_node_time_pass_ratio') - count(a, 'mean_node_time_pass_ratio'),
               shortage_reduction_m3=count(a, 'shortage_m3') - count(b, 'shortage_m3'))
    improved = key(tried)[:-1] < key(base)[:-1]
    shown = raw if improved else {name: 0 for name in raw}
    return dict(first_aid_candidate_id=base, refinement_candidate_id=tried, improved=improved, raw_difference=raw, **shown,
                scope='Difference between the best verified model refinement and the best fixed first-aid plan in the frozen order; '
                      'zero unless strictly better. Only tested candidates; no significance or optimality claim.')


def _finalize_recommendation(store, run_id, task, child_runs):
    """Aggregate independent nominal and stress reports into a host conclusion."""
    import copy
    from services.evidence.service import _validated_agh_association
    from waterguard_contracts.core import validate
    parent = store.get_run(run_id)
    provenance = _validated_agh_association(store._run_directory(run_id) / 'agh/association.json', parent)
    required = set(task['perturbation_domain']['required'])
    nominal, stress, stress_reports, tested = {}, {}, {}, {}
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
            result_path = report_path.with_name('result.json')
            if not result_path.is_file():
                continue
            artifact = store._read_json(result_path)['artifact']
            if (report['task_ref'] != parent['task_ref'] or artifact['task_ref'] != parent['task_ref']
                    or report['simulation_id'] != artifact['simulation_id']):
                raise RuntimeError('Recommendation report binding mismatch')
            passed = report['validity'] == 'valid' and not report['violations']
            if perturbation is None:
                nominal[candidate_id] = (passed, report, plan, child_id)
            else:
                stress_reports.setdefault(candidate_id, []).append((passed, report, plan, perturbation))
    for cid, records in stress_reports.items():
        if cid not in nominal:
            continue
        for passed, report, plan, perturbation in records:
            from services.hydraulics.compiler import validate_perturbation
            validate_perturbation(task, plan, perturbation)
            if plan['plan_hash'] != nominal[cid][2]['plan_hash']:
                raise RuntimeError('Stress plan differs from nominal candidate')
            if report['validity'] == 'valid':
                tested.setdefault(cid, set()).update(perturbation['required_labels'])
            if passed:
                stress.setdefault(cid, set()).update(perturbation['required_labels'])
    eligible = [cid for cid, (passed, _report, _plan, _child) in nominal.items()
                if provenance and passed and required.issubset(stress.get(cid, set()))]
    from services.hydraulics.configuration import ranking_key
    selected = min(eligible, key=lambda cid: ranking_key(task, nominal[cid][1])) if eligible else None
    valid = [cid for cid, row in nominal.items() if row[1]['validity'] == 'valid']
    best_effort = min(valid, key=lambda cid: ranking_key(task, nominal[cid][1])) if valid else None
    from services.verifier.mitigation import mitigation_record
    baseline_ids = [cid for cid in valid if not nominal[cid][2]['actions']]
    baseline_id = min(baseline_ids) if baseline_ids else None
    rescues = {}
    if baseline_id:
        for cid in valid:
            if not nominal[cid][2]['actions']:
                continue
            rescue = mitigation_record(task, nominal[baseline_id][1], nominal[cid][1])
            if rescue and rescue['improves_baseline']:
                rescues[cid] = rescue
    mitigation_id = None
    if provenance and rescues and not selected:
        mitigation_id = _prefer_first_aid(task, nominal, list(rescues))
    delivered_id = selected or mitigation_id
    delivery_source = None if delivered_id is None else ('refinement' if nominal[delivered_id][2]['source'] == 'agh' else 'first_aid')
    refinement_gain = _refinement_gain(task, nominal, [cid for cid in valid if nominal[cid][2]['actions']])
    if selected:
        conclusion = 'FEASIBLE_FOUND'
    elif mitigation_id:
        conclusion = 'BEST_EFFORT_MITIGATION'
    elif provenance and nominal and all(not row[0] or required.issubset(tested.get(cid, set())) for cid, row in nominal.items()):
        conclusion = 'NO_FEASIBLE_FOUND'
    else:
        conclusion = 'INCOMPLETE'
    for cid, (passed, original, plan, child_id) in nominal.items():
        report = copy.deepcopy(original)
        covered = required.issubset(tested.get(cid, set()))
        stress_passed = required.issubset(stress.get(cid, set()))
        report['coverage']['completed_stress_tests'] = sorted(tested.get(cid, set()))
        report['admission']['required_stress_tests_pass'] = 'pass' if stress_passed else ('fail' if covered else 'pending')
        report['admission']['recommendation_eligible'] = 'pass' if cid in eligible else 'fail'
        report['recommendation_blockers'] = ([] if provenance else ['native_agh_trace_missing'])
        if not passed:
            report['recommendation_blockers'].append('nominal_constraints_failed')
        if not stress_passed:
            report['recommendation_blockers'].append('required_stress_tests_failed' if covered else 'required_stress_tests_pending')
        report['verifier_version'] += '+agh-host/1.0.0'
        if cid == mitigation_id:
            rescues[cid].update(actions=plan['actions'], plan_hash=plan['plan_hash'],
                required_stress_tests_pass=report['admission']['required_stress_tests_pass'],
                completed_stress_tests=report['coverage']['completed_stress_tests'])
        validate('VerificationReport', report)
        for filename, value in [('plan.json', plan), ('result.json', store._read_json(store._run_directory(child_id) / 'candidates' / cid / 'result.json')), ('verification.json', report)]:
            store._write_json(f'{run_id}/candidates/{cid}/{filename}', value)
    summary = {
        'run_id': run_id, 'required_stress_tests': sorted(required),
        'nominal_candidate_ids': sorted(nominal),
        'stress_coverage': {candidate_id: sorted(labels) for candidate_id, labels in sorted(stress.items())},
        'stress_tested': {cid: sorted(labels) for cid, labels in sorted(tested.items())},
        'native_provenance_valid': provenance,
        'scope': 'Only tested candidates in the frozen finite action domain; no claim of global optimality.',
        'eligible_candidate_ids': sorted(eligible), 'selected_candidate_id': selected,
        'best_effort_candidate_id': best_effort, 'objective_order': task['objectives'],
        'best_effort_is_recommendation': best_effort in eligible,
        'mitigation_candidate_id': mitigation_id,
        'mitigation': rescues.get(mitigation_id),
        'delivery_source': delivery_source,
        'refinement_gain': refinement_gain,
        'conclusion': conclusion,
    }
    store._write_json(f'{run_id}/recommendation-summary.json', summary)
    store.set_outcome(run_id, conclusion, selected, mitigation_id)
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
            if (self.store._run_directory(child['run_id']) / 'perturbation.json').is_file():
                continue
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
            self.store._write_json(f'{run_id}/task.json', task)
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
            budget_exhausted = bool(outcome and outcome.get('budget_exhausted')) or self.store.get_run(run_id)['status'] == 'BUDGET_EXHAUSTED'
            if process.wait(timeout=10) != 0 and not cancel.is_set() and not paused and not budget_exhausted:
                raise RuntimeError('AGH session failed')
            if paused:
                return
            if cancel.is_set() or (self.store.get_run(run_id)['status'] in TERMINAL and not budget_exhausted):
                return
            if budget_exhausted:
                self.store._write_json(f'{run_id}/error.json', dict(code='BUDGET_EXHAUSTED', message='AGH exhausted the frozen wall-time budget'))
                self.store.transition(run_id, 'BUDGET_EXHAUSTED', wall_time_s=time.monotonic()-started)
            elif not outcome or outcome['result']['reason'] != 'completed':
                raise RuntimeError('AGH did not complete the task turn')
            associations = []
            # Only run IDs from recorded native wg_submit_plan results are considered.
            if not budget_exhausted and not outcome['child_runs']:
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
                if child['status'] == 'QUEUED' or (budget_exhausted and child['status'] not in TERMINAL):
                    self.benchmark.cancel(child_id)
                    child = self.store.get_run(child_id)
                if child['status'] not in TERMINAL:
                    raise RuntimeError('AGH finished while a submitted calculation is still active')
                for plan in self.store.list_candidates(child_id) if not (self.store._run_directory(child_id) / 'perturbation.json').is_file() else []:
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
                    if reports and all(report['validity'] == 'valid' for report in reports):
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
            if budget_exhausted:
                # The already-bound session is terminal and cannot be reactivated.
                self.store.clear_missing_items(run_id, {'native_agh_trace', 'agh_host_not_configured'})
            else:
                self.store.bind_agh_session(run_id, current['agh_session_ref'], current['model_config_ref'])
            _finalize_recommendation(self.store, run_id, task, child_runs)
            self.store.transition(run_id, 'BUDGET_EXHAUSTED' if budget_exhausted else 'COMPLETED',
                                  'PACKAGING', wall_time_s=time.monotonic()-started)
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
