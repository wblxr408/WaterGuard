"""Reproducible competition evidence: real AGH, raw verification and equal-budget baseline.

Run against a running official AGH/API. No SDK or simulator mocks are permitted.
Judging is external: passing these gates is evidence completeness, not a jury score.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.storage import write
from services.verifier.service import verify
from waterguard_contracts.core import digest_bytes, file_hash, load, require, task_hash

CASES = {
    'normal': ('epa-net1', 'normal'),
    'boundary': ('china-zhijiang', 'rupture'),
    'hydraulic_failure': ('epa-net3', 'closure'),
    'invalid_action': ('epa-net1', 'normal'),
    'holdout_leak': ('epa-net1', 'leak'),
    'holdout_isolation': ('china-zhijiang', 'isolation'),
}


def source_hashes():
    paths = [p for directory in ('services', 'scripts', 'config', 'experiments', 'packages/contracts', 'packages/waterguard-agh', 'apps/web-gpt/src')
             for p in (ROOT / directory).rglob('*') if p.suffix in ('.py', '.mjs', '.tsx', '.ts', '.css', '.json')]
    paths.extend(ROOT / name for name in ('package.json', 'package-lock.json', 'requirements.txt',
                                         'apps/web-gpt/package.json', 'apps/web-gpt/package-lock.json'))
    return {p.relative_to(ROOT).as_posix(): digest_bytes(p.read_bytes()) for p in sorted(set(paths)) if p.is_file()}


def api(base, path, body=None):
    request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def task_for(name, candidate_limit=3, max_actions=2, demand_increase_pct=None):
    network, fault = CASES[name]
    task = propose_task(TaskProposal(network_id=network, fault_kind=fault,
        threshold_mode='manual', min_pressure_m=20, min_supply_ratio=.95, candidate_limit=candidate_limit,
        max_actions=max_actions, max_wall_time_s=600, demand_increase_pct=demand_increase_pct))
    task['task_id'] = 'contest-' + name + '-' + task['task_id'][4:]
    if name == 'hydraulic_failure':
        # Declared before either strategy runs. Exact source-control acknowledgement
        # permits operation of the existing alternate Lake supply pump.
        facility = next(f for f in network_details(network)['catalog']['facilities'] if f['link_id'] == '10')
        task['control_policy']['overrides'] = [dict(link_id='10', control_names=facility['native_control_names'],
            reason='Frozen emergency alternate-source scenario; pump 10 manual operation is permitted for both strategies.')]
        task['action_limits']['allowed_link_ids'].append('10')
    task['constraint_hash'] = task_hash(task)
    return task


def fixed_baseline(task, output):
    """Fixed order search, same catalogue/task/budget, reserve stress for any pass."""
    plans = benchmark_plans(task, network_details(task['network_ref']['network_id'])['catalog'])
    details = network_details(task['network_ref']['network_id'])
    adapter = PhysicalAdapter(output)
    records, used, selected = [], 0, None
    started = time.monotonic()
    for plan in plans:
        if used >= task['budget']['max_simulations']:
            break
        remaining = task['budget']['max_wall_time_s'] - (time.monotonic() - started)
        require(remaining > 0, 'Fixed search exhausted wall-time')
        result = adapter.simulate(task, plan, timeout_s=remaining)
        used += 1
        report = verify(task, plan, result, details['catalog'], details['scene'],
                        artifact_dir=output / result['artifact']['simulation_id'])
        records.append(dict(candidate_id=plan['candidate_id'], perturbation=None,
                            simulation_id=result['artifact']['simulation_id'], report=report))
        if report['validity'] == 'valid' and not report['violations'] and used < task['budget']['max_simulations']:
            from services.hydraulics.configuration import stress_multiplier
            multiplier = stress_multiplier(task)
            perturbation = dict(perturbation_id='fixed-demand-stress-' + plan['candidate_id'],
                candidate_id=plan['candidate_id'], parameters={'demand_multiplier': multiplier},
                required_labels=task['perturbation_domain']['required'])
            result = adapter.simulate(task, plan, perturbation=perturbation,
                timeout_s=task['budget']['max_wall_time_s'] - (time.monotonic()-started))
            used += 1
            stress = verify(task, plan, result, details['catalog'], details['scene'], perturbation,
                            artifact_dir=output / result['artifact']['simulation_id'])
            records.append(dict(candidate_id=plan['candidate_id'], perturbation=perturbation,
                                simulation_id=result['artifact']['simulation_id'], report=stress))
            if stress['validity'] == 'valid' and not stress['violations']:
                selected = plan['candidate_id']
                break
    summary = dict(strategy='fixed-order', simulations=used, wall_time_s=time.monotonic()-started,
                   selected_candidate_id=selected, records=records)
    write(output / 'comparison.json', summary)
    return summary


def inspect_run(base, run_id, output):
    """Fail closed if the actual API, original trace and independent reports disagree."""
    run = api(base, '/api/runs/' + run_id)
    root = ROOT / 'artifacts' / run_id
    task = load(root / 'task.json') if (root / 'task.json').is_file() else api(base,
        '/api/tasks/' + run['task_ref']['task_id'] + '?revision=' + str(run['task_ref']['revision']))
    events = [json.loads(line) for line in (root / 'agh/native/events.jsonl').read_text(encoding='utf-8').splitlines()]
    calls = {e['data']['toolUseId']: e['data'] for e in events if e['type'] == 'tool/call'}
    names = [e['data']['name'] for e in events if e['type'] == 'tool/call']
    returned = {e['data']['toolUseId']: e['data'] for e in events if e['type'] == 'tool/result'}
    successful = {call['name'] for use_id, call in calls.items() if use_id in returned and not returned[use_id].get('isError') and
                  not (isinstance(returned[use_id].get('structured'), dict) and returned[use_id]['structured'].get('accepted') is False)}
    require(run['mode'] == 'live' and run['status'] == 'COMPLETED', f'Live run incomplete: {run_id} {run["status"]}')
    require(run['budget_usage']['model_calls'] > 0, 'No actual model requests')
    for name in ['wg_get_task', 'wg_inspect_network', 'wg_generate_candidates', 'wg_submit_plan',
                 'wg_simulate_candidate', 'wg_verify_candidate', 'wg_compare_candidates', 'wg_build_evidence']:
        require(name in successful, 'Missing successful actual native tool: ' + name)
    require(names.index('wg_get_task') < names.index('wg_inspect_network') < names.index('wg_generate_candidates'), 'Task/modeling sequence mismatch')
    recommendation = api(base, '/api/runs/' + run_id + '/recommendation')
    association = load(root / 'agh/association.json')
    application = {e['event_id']: e for e in [json.loads(line) for line in (root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]}
    for joined in association['events']:
        event = application[joined['application_event_id']]
        require(event['agh_session_ref'] == association['agh_session_ref'] == run['agh_session_ref'], 'Native session association mismatch')
        require(event['native_tool_call_ref'] == joined['native_tool_call_ref'], 'Application/native call association mismatch')
        use_id = joined['native_tool_call_ref'].removeprefix('agh:' + run['agh_session_ref'] + ':tool:')
        require(use_id in calls, 'Associated application event has no actual native call')
    workspace = api(base, '/api/runs/' + run_id + '/workspace')
    require(workspace['run']['conclusion'] == run['conclusion'] == recommendation['conclusion'], 'UI/host conclusion mismatch')
    recomputed, full_reports = [], {}
    for child in (root / 'agh/children').iterdir():
        if not child.is_dir():
            continue
        child_task = load(child / 'task.json')
        require(child_task == task, 'Different task used for comparison/stress')
        perturbation = load(child / 'perturbation.json') if (child / 'perturbation.json').is_file() else None
        for folder in (child / 'candidates').glob('*'):
            if not (folder / 'verification.json').is_file():
                continue
            plan, result = load(folder / 'plan.json'), load(folder / 'result.json')
            details = network_details(task['network_ref']['network_id'])
            report = verify(task, plan, result, details['catalog'], details['scene'], perturbation,
                            artifact_dir=child / 'simulations' / result['artifact']['simulation_id'])
            require(report == load(folder / 'verification.json'), 'Saved verifier report cannot be reproduced')
            full_reports[report['simulation_id']] = report
            recomputed.append(dict(candidate_id=plan['candidate_id'], parent_candidate_id=plan['parent_candidate_id'],
                                   source=plan['source'], actions=plan['actions'], perturbation=perturbation,
                                   simulation_id=report['simulation_id'], metrics=report['metrics'],
                                   validity=report['validity'], nominal_pass=not report['violations']))
    revisions = []
    from services.api.planning import summarize_failure
    for position, event in enumerate(events):
        if event['type'] != 'tool/result' or event['data'].get('isError'):
            continue
        use_id = event['data']['toolUseId']
        if calls.get(use_id, {}).get('name') != 'wg_revise_plan':
            continue
        record = event['data']['structured']
        if record.get('accepted') is False:
            continue
        args = calls[use_id]['args']
        plan = record['plan']
        stored = load(root / 'planning/revisions' / (plan['candidate_id'] + '.json'))
        require(stored == record, 'Revision differs from the actual native tool result')
        source = root / 'agh/children' / record['source_run_id']
        source_plan = load(source / 'submitted-plan.json')
        report_path = source / 'candidates' / source_plan['candidate_id'] / 'verification.json'
        source_report = load(report_path)
        require(file_hash(report_path) == record['source_report_sha256'], 'Revision failure witness hash changed')
        require(summarize_failure(task, source_plan, source_report) == record['diagnosis'], 'Revision diagnosis cannot be reproduced')
        require(plan['parent_candidate_id'] == source_plan['candidate_id'] and record['source_plan_hash'] == source_plan['plan_hash'], 'Revision lineage mismatch')
        requested_actions = sorted(json.loads(args['actions_json']), key=lambda a: (a['simulation_time_s'], a['link_id'], a['kind']))
        require(plan['actions'] == requested_actions and args['reason'] == plan['reason'], 'Model revision request differs from compiled controls')
        earlier = events[:position]
        require(any(e['type'] == 'tool/result' and not e['data'].get('isError') and
                    calls.get(e['data']['toolUseId'], {}).get('name') == 'wg_verify_candidate' and
                    calls[e['data']['toolUseId']]['args']['run_id'] == record['source_run_id'] for e in earlier),
                'Revision has no earlier successful actual independent verification')
        revisions.append(dict(candidate_id=plan['candidate_id'], source_run_id=record['source_run_id'],
            parent_candidate_id=plan['parent_candidate_id'], actions=plan['actions'],
            source_report_sha256=record['source_report_sha256'], diagnosis_reproduced=True,
            simulated=any(row['candidate_id'] == plan['candidate_id'] and row['perturbation'] is None for row in recomputed)))
    feedback_sizes = []
    for use_id, returned_value in returned.items():
        if returned_value.get('isError') or calls.get(use_id, {}).get('name') != 'wg_verify_candidate':
            continue
        for summary in returned_value.get('structured') or []:
            full = full_reports[summary['simulation_id']]
            require(summary['metrics'] == full['metrics'] and summary['admission'] == full['admission'], 'Model feedback differs from independent report')
            require(summary.get('violation_count', len(summary['violations'])) == len(full['violations']), 'Feedback hides total violation count')
            require(all(excerpt in full['violations'] for excerpt in summary['violations']), 'Feedback contains invented violation witness')
            feedback_sizes.append(dict(simulation_id=summary['simulation_id'],
                full_report_bytes=len(json.dumps(full, ensure_ascii=False).encode()),
                native_feedback_bytes=len(json.dumps(summary, ensure_ascii=False).encode()),
                actual_violation_count=len(full['violations']), returned_violation_count=len(summary['violations'])))
    for candidate in workspace['candidates']:
        cid = candidate['id']
        nominal = next(row for row in recomputed if row['candidate_id'] == cid and row['perturbation'] is None)
        require(candidate['simulationId'] == nominal['simulation_id'], 'Stress artifact replaced nominal UI candidate')
        require(candidate['verification']['metrics'] == nominal['metrics'], 'UI metric mismatch')
        eligible = cid in recommendation['eligible_candidate_ids']
        require((candidate['verification']['admission']['recommendation_eligible'] == 'pass') == eligible, 'Candidate/host admission mismatch')
    manifest = api(base, '/api/runs/' + run_id + '/evidence')
    require(manifest['live_export_allowed'], 'No native AGH provenance')
    require(not manifest['missing_items'] or run['conclusion'] != 'FEASIBLE_FOUND', 'Recommended run has missing evidence')
    for entry in manifest['artifacts']:
        data = (root / entry['ref'].removeprefix('artifact:')).read_bytes()
        require(digest_bytes(data) == entry['sha256'] == manifest['hashes'][entry['ref']], 'Evidence hash mismatch')
    with urlopen(base + '/api/runs/' + run_id + '/evidence/download', timeout=90) as response:
        payload = response.read()
    target = output / (run_id + '.zip')
    target.write_bytes(payload)
    with zipfile.ZipFile(target) as archive:
        require(archive.testzip() is None, 'Evidence ZIP CRC failed')
    rejected = [e['data'].get('structured') for e in events if e['type'] == 'tool/result' and
                isinstance(e['data'].get('structured'), dict) and e['data']['structured'].get('code') == 'INVALID_ACTION']
    result = dict(run_id=run_id, conclusion=run['conclusion'], selected_candidate_id=run['selected_candidate_id'],
                  budget_usage=run['budget_usage'], model=load(root / 'agh/probe.json')['provider'],
                  native_tools=names, successful_native_tools=sorted(successful), raw_reports_recomputed=len(recomputed), candidates=recomputed,
                  bounded_feedback=feedback_sizes, plan_revisions=revisions,
                  structured_rejections=rejected, evidence_files=len(manifest['artifacts']),
                  revision_rejections=[e['data']['structured'] for e in events if e['type'] == 'tool/result' and
                      isinstance(e['data'].get('structured'), dict) and e['data']['structured'].get('code') == 'INVALID_REVISION'],
                  workflow_refusals=[e['data']['structured'] for e in events if e['type'] == 'tool/result' and
                      isinstance(e['data'].get('structured'), dict) and e['data']['structured'].get('code') == 'REVISION_REQUIRED'],
                  evidence_sha256=digest_bytes(payload), missing_items=manifest['missing_items'])
    write(output / (run_id + '-audit.json'), result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8010')
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/contest-acceptance')
    parser.add_argument('--inspect-run')
    parser.add_argument('--reuse-normal', help='Audit an already completed real normal run without another model call')
    parser.add_argument('--max-simulations', type=int, choices=range(2, 101), default=3)
    parser.add_argument('--max-actions', type=int, choices=range(1, 101), default=2)
    parser.add_argument('--demand-increase-pct', type=float)
    parser.add_argument('--require-feedback-revision', action='store_true', help='Net3 must actually simulate an evidence-backed AGH revision')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.inspect_run:
        print(json.dumps(inspect_run(args.base, args.inspect_run, args.output), ensure_ascii=False))
        return
    report = dict(created_at=datetime.now(timezone.utc).isoformat(),
        git_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source_hashes=source_hashes(),
        interpretation='Evidence gates only; judging score and member declarations require external confirmation.', cases=[])
    for name in args.cases:
        if name == 'normal' and args.reuse_normal:
            run = api(args.base, '/api/runs/' + args.reuse_normal)
            task = api(args.base, '/api/tasks/' + run['task_ref']['task_id'] + '?revision=' + str(run['task_ref']['revision']))
        else:
            task = task_for(name, args.max_simulations, args.max_actions, args.demand_increase_pct)
            task = api(args.base, '/api/tasks', task)
        write(args.output / (name + '-task.json'), task)
        request = {'mode': 'live', 'idempotency_key': task['task_id']}
        if name == 'invalid_action':
            request['acceptance_scenario'] = 'repair_demo'
        if not (name == 'normal' and args.reuse_normal):
            run = api(args.base, '/api/tasks/' + task['task_id'] + '/runs', request)
        print(name, 'LIVE', run['run_id'], flush=True)
        deadline = time.monotonic() + task['budget']['max_wall_time_s'] + 60
        while time.monotonic() < deadline:
            run = api(args.base, '/api/runs/' + run['run_id'])
            if run['status'] in {'COMPLETED', 'ERROR', 'CANCELLED', 'BUDGET_EXHAUSTED'}:
                break
            time.sleep(2)
        # Wait only for publication after terminal status, never invent a finish.
        for attempt in range(10):
            try:
                live = inspect_run(args.base, run['run_id'], args.output)
                break
            except (FileNotFoundError, KeyError):
                if attempt == 9:
                    raise
                time.sleep(.5)
        fixed = fixed_baseline(task, args.output / (name + '-fixed'))
        require(source_hashes() == report['source_hashes'], 'Executable source changed during frozen acceptance')
        if name in {'normal', 'invalid_action'}:
            require(live['conclusion'] == 'FEASIBLE_FOUND', name + ' lacks a verified recommendation')
        if name == 'invalid_action':
            require(live['structured_rejections'], 'No recorded INVALID_ACTION feedback')
        if name == 'hydraulic_failure':
            nominal = [row for row in live['candidates'] if row['perturbation'] is None]
            baseline = next(row for row in nominal if row['candidate_id'] == 'web-candidate-0')
            require(not baseline['nominal_pass'], 'No real hydraulic failure baseline')
            require(any(row['actions'] and row['metrics']['shortage_m3'] < baseline['metrics']['shortage_m3'] for row in nominal), 'No actual improving action after failure')
            if args.require_feedback_revision:
                require(any(row['simulated'] for row in live['plan_revisions']), 'No actual evidence-backed AGH revision was simulated')
        report['cases'].append(dict(name=name, task_ref=task['constraint_hash'], live=live,
            fixed=dict(simulations=fixed['simulations'], wall_time_s=fixed['wall_time_s'],
                       selected_candidate_id=fixed['selected_candidate_id'],
                       best_shortage_m3=min(r['report']['metrics']['shortage_m3'] for r in fixed['records'] if r['perturbation'] is None))))
        write(args.output / 'summary.json', report)
        print(name, 'VERIFIED', live['conclusion'], 'raw_reports', live['raw_reports_recomputed'], flush=True)


if __name__ == '__main__':
    main()
