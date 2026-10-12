"""Actual additional evidence; never amend completed Live records.

Run frozen same-plan stress, paired repeated native AGH/fixed search, and a
zero-revision ablation. All numerical acceptance uses the existing independent
verifier. No startup probes, standalone checksums, or synthetic Live traces.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.storage import write
from services.hydraulics.configuration import ranking_key, stress_multiplier
from services.verifier.service import verify
from waterguard_contracts.core import load, task_hash, require

LIVE_ID = 'run-9823e888d98e43fa972dfaa12bc4d517'
OUT = ROOT / 'artifacts/submission-completion'
TERMINAL = {'COMPLETED', 'BUDGET_EXHAUSTED', 'ERROR', 'FAILED', 'CANCELLED', 'NEEDS_INPUT'}


def api(path, data=None):
    request = Request('http://127.0.0.1:8010' + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Content-Type': 'application/json'},
        method='GET' if data is None else 'POST')
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def metrics(report):
    return dict(validity=report['validity'], violation_count=len(report['violations']),
        remaining_nodes=len({v['node_id'] for v in report['violations']}),
        recommendation_eligible=report['admission']['recommendation_eligible'],
        metrics={k: v for k, v in report['metrics'].items() if k != 'node_availability'})


def physical(task, plans, output, perturbation=None):
    output.mkdir(parents=True, exist_ok=True)
    write(output / 'task.json', task)
    adapter = PhysicalAdapter(output / 'simulations')
    details = network_details(task['network_ref']['network_id'])
    records = []
    started = time.monotonic()
    for plan in plans:
        if time.monotonic() - started >= task['budget']['max_wall_time_s']:
            break
        result = adapter.simulate(task, plan, perturbation=perturbation,
            timeout_s=task['budget']['max_wall_time_s'] - (time.monotonic() - started))
        report = verify(task, plan, result, details['catalog'], details['scene'], perturbation,
            artifact_dir=output / 'simulations' / result['artifact']['simulation_id'])
        record = dict(plan=plan, report=report, result=result)
        records.append(record)
        write(output / 'records.json', records)
    valid = [r for r in records if r['report']['validity'] == 'valid']
    best = min(valid, key=lambda r: ranking_key(task, r['report'])) if valid else None
    summary = dict(strategy='fixed_order', task_ref=plans[0]['task_ref'],
        budget=task['budget'], simulations=len(records), model_calls=0,
        wall_time_s=time.monotonic() - started,
        best=None if best is None else dict(candidate_id=best['plan']['candidate_id'],
            actions=best['plan']['actions'], **metrics(best['report'])))
    write(output / 'summary.json', summary)
    return summary


def stress():
    source = ROOT / 'artifacts' / LIVE_ID
    task = load(source / 'task.json')
    plan = load(source / 'candidates/web-candidate-1/plan.json')
    original = copy.deepcopy([task, plan])
    perturbation = dict(perturbation_id='submission-demand-plus-10pct',
        candidate_id=plan['candidate_id'], parameters={'demand_multiplier': stress_multiplier(task)},
        required_labels=task['perturbation_domain']['required'])
    physical(task, [plan], OUT / 'same-plan-stress', perturbation)
    records = load(OUT / 'same-plan-stress/records.json')
    require([task, plan] == original, 'Original frozen task/plan changed')
    report = records[0]['report']
    require(report['validity'] == 'valid', 'Stress evidence is physically invalid')
    require(all(a['simulation_time_s'] >= 5400 for a in plan['actions']), 'Backdated action')
    summary = dict(source_live_run_id=LIVE_ID, provenance='Additional physical benchmark; not an original native AGH tool call',
        original_live_record_amended=False, task_ref=plan['task_ref'], actions=plan['actions'],
        demand_multiplier=perturbation['parameters']['demand_multiplier'], **metrics(report))
    write(OUT / 'same-plan-stress/result-summary.json', summary)
    print('STRESS', json.dumps(summary, ensure_ascii=False), flush=True)


def native_audit(run, output):
    source = ROOT / 'artifacts' / run['run_id']
    task = load(source / 'task.json')
    details = network_details(task['network_ref']['network_id'])
    events = [json.loads(line) for line in (source / 'agh/native/events.jsonl').read_text(encoding='utf-8').splitlines() if line]
    calls = {e['data']['toolUseId']: e['data']['name'] for e in events if e['type'] == 'tool/call'}
    successful = [calls.get(e['data']['toolUseId']) for e in events if e['type'] == 'tool/result' and not e['data'].get('isError')]
    candidates = []
    audit_failures = []
    for report_path in sorted((source / 'candidates').glob('*/verification.json')):
        folder = report_path.parent
        plan, result, report = [load(folder / name) for name in ('plan.json', 'result.json', 'verification.json')]
        # Recompute from actual child raw internal intervals, not UI report fields.
        child_folders = list((source / 'agh/children').glob('*/simulations/' + result['artifact']['simulation_id']))
        if len(child_folders) != 1 and run['status'] not in {'COMPLETED', 'BUDGET_EXHAUSTED'}:
            audit_failures.append(dict(candidate_id=folder.name, reason='Missing/ambiguous actual child simulation'))
            continue
        require(len(child_folders) == 1, 'Missing/ambiguous actual child simulation')
        perturbation_path = child_folders[0] / 'request.json'
        perturbation = load(perturbation_path).get('perturbation')
        audited = verify(task, plan, result, details['catalog'], details['scene'], perturbation, artifact_dir=child_folders[0])
        require(audited['metrics'] == report['metrics'] and audited['violations'] == report['violations'], 'Independent remeasurement differs')
        candidates.append(dict(candidate_id=plan['candidate_id'], actions=plan['actions'], **metrics(report)))
    start, last = events[0]['ts'], events[-1]['ts']
    measured = (datetime.fromisoformat(last.replace('Z', '+00:00')) - datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds()
    if task.get('response_latency'):
        from waterguard_contracts.core import latency_floor_s
        latency = task['response_latency']
        measured_floor = latency_floor_s(task['fault']['start_s'], task['time_window']['step_s'],
            latency['detection_s'], measured, latency['actuation_s'])
        require(task['action_limits']['earliest_s'] >= measured_floor, 'Frozen floor backdates full native session')
    evidence = api('/api/runs/' + run['run_id'] + '/evidence')
    if run['status'] in {'COMPLETED', 'BUDGET_EXHAUSTED'}:
        require(evidence['live_export_allowed'], 'Actual native provenance rejected')
    valid = [c for c in candidates if c['validity'] == 'valid']
    best = min(valid, key=lambda c: ranking_key(task, c)) if valid else None
    summary = dict(run_id=run['run_id'], task_ref=run['task_ref'], status=run['status'], conclusion=run['conclusion'],
        selected_candidate_id=run.get('selected_candidate_id'), mitigation_candidate_id=run.get('mitigation_candidate_id'),
        budget=task['budget'], budget_usage=run['budget_usage'], native_session_duration_s=measured,
        successful_native_tools=successful, native_export_allowed=evidence['live_export_allowed'],
        missing_items=evidence.get('missing_items', []), independently_remeasured_candidates=len(candidates),
        candidate_audit_failures=audit_failures, model=load(source / 'agh/model.json'), best=best)
    write(output / 'audit.json', summary)
    if evidence['live_export_allowed']:
        with urlopen('http://127.0.0.1:8010/api/runs/' + run['run_id'] + '/evidence/download', timeout=120) as response:
            (output / 'native-evidence.zip').write_bytes(response.read())
    return summary


def live(task, output):
    output.mkdir(parents=True, exist_ok=True)
    started_path = output / 'started.json'
    if started_path.exists():
        run = api('/api/runs/' + load(started_path)['run_id'])
    else:
        saved = api('/api/tasks', task)
        write(output / 'task.json', saved)
        run = api('/api/tasks/' + task['task_id'] + '/runs', dict(mode='live', idempotency_key=output.name + task['task_id']))
        write(started_path, run)
    print('LIVE', output.name, run['run_id'], flush=True)
    while run['status'] not in TERMINAL:
        time.sleep(15)
        run = api('/api/runs/' + run['run_id'])
        write(output / 'progress.json', run)
    print('TERMINAL', output.name, run['status'], run['conclusion'], run['budget_usage'], flush=True)
    return native_audit(run, output)


def matrix():
    # Freeze before model use. Budget caps match; revisions are the ablated factor.
    spec = dict(repeats=3, scenarios=['net3-closure-123', 'net1-leak-22'],
        simulation_cap=4, wall_time_cap_s=600, max_actions=4, declared_decision_s=1200,
        strategies=['fixed_order', 'agh_feedback', 'agh_zero_revision'],
        ablation='Only max_revisions changes from 4 to 0; no production tool/prompt change',
        execution='Sequential, shared local host/provider; no significance claim for n=3',
        metrics=['min_node_time_pass_ratio', 'mean_node_time_pass_ratio', 'emergency_mean_node_time_pass_ratio',
            'fully_compliant_node_count', 'nodes_reaching_emergency_service', 'shortage_m3', 'simulations', 'model_calls', 'wall_time_s'])
    write(OUT / 'matrix/protocol.json', spec)
    result_path = OUT / 'matrix/results.json'
    result = load(result_path) if result_path.exists() else dict(protocol=spec, trials=[])
    require(result['protocol'] == spec, 'Cannot resume a different frozen protocol')
    for scenario in spec['scenarios']:
        proposal = load(ROOT / 'artifacts/live-mitigation-acceptance/proposal-delivery.json') if scenario.startswith('net3') else dict(
            network_id='epa-net1', fault_kind='leak', link_id='22', threshold_mode='manual', min_pressure_m=20,
            min_supply_ratio=.95, fault_parameters={'leak_area_m2': .01865}, demand_increase_pct=10,
            response_latency={'decision_wall_clock_s': 1200, 'measurement': 'declared', 'source': 'Predeclared latency allowance exceeds native wall-time cap'})
        proposal.update(candidate_limit=4, max_wall_time_s=600, max_actions=4)
        for repeat in range(1, 4):
            if any(t['scenario'] == scenario and t['repeat'] == repeat for t in result['trials']):
                continue
            name = f'{scenario}-r{repeat}'
            folder = OUT / 'matrix' / name
            saved_task = folder / 'agh-feedback/task.json'
            task = load(saved_task) if saved_task.exists() else propose_task(TaskProposal(**proposal))
            fixed_path = folder / 'fixed/summary.json'
            fixed = load(fixed_path) if fixed_path.exists() else physical(task,
                benchmark_plans(task, network_details(proposal['network_id'])['catalog']), folder / 'fixed')
            agh = live(task, folder / 'agh-feedback')
            ablated = copy.deepcopy(task)
            ablated['task_id'] += '-zero-revision'
            ablated['budget']['max_revisions'] = 0
            ablated['constraint_hash'] = task_hash(ablated)
            ablation = live(ablated, folder / 'agh-zero-revision')
            result['trials'].append(dict(scenario=scenario, repeat=repeat, fixed=fixed, agh=agh, ablation=ablation))
            write(OUT / 'matrix/results.json', result)
            print('TRIAL_DONE', name, flush=True)


def budget():
    proposal = load(ROOT / 'artifacts/live-mitigation-acceptance/proposal-delivery.json')
    proposal.update(max_wall_time_s=240, candidate_limit=24)
    task = propose_task(TaskProposal(**proposal))
    record = live(task, OUT / 'budget-live')
    require(record['status'] == 'BUDGET_EXHAUSTED', 'Budget branch was not exercised')
    require(record['conclusion'] == 'BEST_EFFORT_MITIGATION' and record['mitigation_candidate_id'], 'Verified mitigation not retained')
    require(record['selected_candidate_id'] is None and record['best']['violation_count'] > 0, 'False recommendation')
    print('BUDGET_ACCEPTED', record['run_id'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['stress', 'matrix', 'budget'])
    args = parser.parse_args()
    {'stress': stress, 'matrix': matrix, 'budget': budget}[args.action]()
