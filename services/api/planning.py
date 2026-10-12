"""Evidence-backed diagnosis and bounded AGH plan revisions.

The model supplies controls, never metrics or admission. Every revision names a
completed, independently revalidated failure from the same live task/session.
"""
from collections import defaultdict, deque
from waterguard_contracts.core import digest, file_hash, plan_hash, require, task_ref, validate
from .runtime import ConflictError

VERSION = 'waterguard-feedback-planning/1.0.0'


def reviewed_failure(store, run_id, *, task=None, parent=None):
    from .workbench import network_details
    from services.verifier.service import verify
    source = store.get_run(run_id)
    root = store._run_directory(run_id)
    require(source['status'] == 'COMPLETED', 'Revision requires a completed source calculation')
    require(not (root / 'perturbation.json').exists(), 'Revision source must be a nominal calculation')
    if parent is not None:
        require(store.live_parent_for_child(run_id) is not None and
                store.live_parent_for_child(run_id)['run_id'] == parent['run_id'],
                'Revision source belongs to another AGH session')
    source_task = store.get_task(source['task_ref']['task_id'], source['task_ref']['revision'])
    require(task is None or task == source_task, 'Revision source uses a different frozen task')
    plan = store._read_json(root / 'submitted-plan.json')
    folder = root / 'candidates' / plan['candidate_id']
    report = store._read_json(folder / 'verification.json')
    result = store._read_json(folder / 'result.json')
    details = network_details(source_task['network_ref']['network_id'])
    reproduced = verify(source_task, plan, result, details['catalog'], details['scene'],
                        artifact_dir=root / 'simulations' / result['artifact']['simulation_id'])
    require(reproduced == report, 'Revision source report cannot be independently reproduced')
    require(report['validity'] == 'valid' and report['violations'],
            'Revision requires an actual valid nominal constraint failure')
    return source_task, plan, report, folder / 'verification.json'


def summarize_failure(task, plan, report):
    """Count original intervals; union overlaps before counting node-time burden."""
    from services.hydraulics.compiler import assets
    by_node = defaultdict(list)
    by_constraint = defaultdict(float)
    for violation in report['violations']:
        node = violation['node_id']
        by_node[node].append(violation)
        by_constraint[violation['code']] += violation['end_s'] - violation['start_s']
    nodes = []
    for node, violations in by_node.items():
        merged = []
        for start, end in sorted((v['start_s'], v['end_s']) for v in violations):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        pressure = [v['observed'] for v in violations if v['code'] == 'MIN_PRESSURE']
        supply = [v['observed'] for v in violations if v['code'] == 'MIN_SUPPLY_RATIO']
        nodes.append(dict(node_id=node, failed_seconds=sum(b-a for a, b in merged),
            failed_windows=merged, min_pressure_m=min(pressure) if pressure else None,
            min_supply_ratio=min(supply) if supply else None,
            violation_count=len(violations)))
    nodes.sort(key=lambda n: (-n['failed_seconds'], n['min_pressure_m'] if n['min_pressure_m'] is not None else 1e9, n['node_id']))
    wn, _, _, catalog = assets(task['network_ref']['network_id'])
    adjacency = defaultdict(list)
    for _, link in wn.links():
        adjacency[link.start_node_name].append(link.end_node_name)
        adjacency[link.end_node_name].append(link.start_node_name)
    distances = {n['node_id']: 0 for n in nodes}
    pending = deque(distances)
    while pending:
        node = pending.popleft()
        for neighbor in adjacency[node]:
            if neighbor not in distances:
                distances[neighbor] = distances[node] + 1
                pending.append(neighbor)
    fault = task.get('fault') or {}
    excluded = {fault.get('link_id'), *fault.get('boundary_link_ids', [])}
    overrides = {o['link_id'] for o in task.get('control_policy', {}).get('overrides', [])}
    facilities = []
    for facility in catalog['facilities']:
        link_id = facility['link_id']
        if (link_id not in task['action_limits']['allowed_link_ids'] or link_id in excluded or
                facility['requires_explicit_override'] and link_id not in overrides):
            continue
        kinds = sorted(set(facility['allowed_actions']) & set(task['action_limits']['allowed_kinds']))
        if not kinds:
            continue
        link = wn.get_link(link_id)
        facilities.append(dict(link_id=link_id, kind=facility['kind'], initial_status=facility['initial_status'],
            allowed_kinds=kinds, endpoints=[link.start_node_name, link.end_node_name],
            distance_to_failed_node=min(distances.get(link.start_node_name, 10**9), distances.get(link.end_node_name, 10**9))))
    facilities.sort(key=lambda f: (0 if f['kind'] == 'pump' and f['initial_status'] == 'closed' else 1,
                                   f['distance_to_failed_node'], f['link_id']))
    limits, window = task['action_limits'], task['time_window']
    return dict(version=VERSION, task_ref=task_ref(task), source_candidate_id=plan['candidate_id'],
        source_plan_hash=plan['plan_hash'], simulation_id=report['simulation_id'],
        metrics=report['metrics'], violation_count=len(report['violations']),
        affected_node_count=len(nodes), failed_node_seconds=sum(n['failed_seconds'] for n in nodes),
        constraint_node_seconds=dict(by_constraint), worst_nodes=nodes[:8], nodes_truncated=len(nodes) > 8,
        permitted_facilities=facilities[:12], permitted_facility_count=len(facilities), facilities_truncated=len(facilities) > 12,
        allowed_times_s=list(range(limits['earliest_s'], limits['latest_s']+1, window['step_s'])),
        max_actions=limits['max_actions'], min_switch_interval_s=limits['min_switch_interval_s'],
        interpretation='Original interval/node burden and permitted topology proximity; neither a prediction nor an optimal control proof.')


def diagnose_failure(store, run_id):
    task, plan, report, path = reviewed_failure(store, run_id)
    return dict(summarize_failure(task, plan, report), source_run_id=run_id,
                source_report_sha256=file_hash(path), full_report_ref=f'{run_id}/candidates/{plan["candidate_id"]}/verification.json')


def revision_records(store, parent):
    return [store._read_json(p) for p in sorted((store._run_directory(parent['run_id']) / 'planning/revisions').glob('*.json'))]


def create_revision(store, parent, task, source_run_id, actions, reason):
    from services.hydraulics.compiler import compile_task
    if store.get_run(parent['run_id'])['status'] != 'RUNNING':
        raise ConflictError('AGH run is no longer active')
    if parent['budget_usage']['simulations'] >= task['budget']['max_simulations']:
        raise ConflictError('BUDGET_EXHAUSTED: no calculation slot remains for a revision')
    source_task, source_plan, report, path = reviewed_failure(store, source_run_id, task=task, parent=parent)
    require(isinstance(actions, list) and 0 < len(actions) <= task['action_limits']['max_actions'], 'Invalid revision action count')
    for action in actions:
        validate('Action', action)
        require(action['link_id'] in task['action_limits']['allowed_link_ids'],
                f"Unauthorized revision facility: {action['link_id']}; use the frozen permitted facility IDs")
    actions = sorted(actions, key=lambda a: (a['simulation_time_s'], a['link_id'], a['kind']))
    require(actions != source_plan['actions'], 'Revision must change the source actions')
    excluded = {(task.get('fault') or {}).get('link_id'), *(task.get('fault') or {}).get('boundary_link_ids', [])}
    require(not any(a['link_id'] in excluded for a in actions), 'Revision cannot undo a frozen fault or isolation boundary')
    identity = dict(task_ref=task_ref(task), source_run_id=source_run_id, actions=actions, reason=reason)
    candidate_id = 'agh-revision-' + digest(identity)[7:31]
    existing = revision_records(store, parent)
    matched = next((r for r in existing if r['plan']['candidate_id'] == candidate_id), None)
    if matched:
        return matched
    require(len(existing) < task['budget']['max_revisions'], 'Frozen plan revision budget exhausted')
    require(not any(r['plan']['actions'] == actions for r in existing), 'Equivalent revision already exists; use its candidate ID')
    plan = dict(schema_version=task['schema_version'], candidate_id=candidate_id, task_ref=task_ref(task),
        parent_candidate_id=source_plan['candidate_id'], actions=actions, source='agh', reason=reason)
    plan['plan_hash'] = plan_hash(plan)
    compile_task(source_task, plan)
    require(store.get_run(parent['run_id'])['status'] == 'RUNNING', 'AGH run ended during revision preflight')
    record = dict(version=VERSION, task_ref=task_ref(task), source_run_id=source_run_id,
        source_plan_hash=source_plan['plan_hash'], source_report_sha256=file_hash(path),
        diagnosis=summarize_failure(task, source_plan, report), plan=plan)
    ref = store._write_json(f"{parent['run_id']}/planning/revisions/{candidate_id}.json", record)
    store.emit(store.get_run(parent['run_id']), 'gateway', 'candidate.revised', ref, candidate_id=candidate_id)
    return record


def load_revision_plan(store, parent, task, candidate_id):
    if parent is None:
        return None
    record = next((r for r in revision_records(store, parent) if r['plan']['candidate_id'] == candidate_id), None)
    if record is None:
        return None
    source_task, source_plan, report, path = reviewed_failure(store, record['source_run_id'], task=task, parent=parent)
    require(record['task_ref'] == task_ref(source_task) and record['source_plan_hash'] == source_plan['plan_hash']
            and record['source_report_sha256'] == file_hash(path), 'Revision witness binding changed')
    require(record['diagnosis'] == summarize_failure(task, source_plan, report), 'Revision diagnosis was changed')
    plan = record['plan']
    identity = dict(task_ref=task_ref(task), source_run_id=record['source_run_id'], actions=plan['actions'], reason=plan['reason'])
    require(plan['candidate_id'] == 'agh-revision-' + digest(identity)[7:31], 'Revision identity changed')
    require(plan['parent_candidate_id'] == source_plan['candidate_id'] and plan['source'] == 'agh'
            and plan['plan_hash'] == plan_hash(plan), 'Revision plan binding changed')
    from services.hydraulics.compiler import compile_task
    compile_task(task, plan)
    return plan
