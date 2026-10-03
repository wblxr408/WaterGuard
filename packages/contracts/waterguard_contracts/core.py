from __future__ import annotations

import copy
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Protocol

from jsonschema import Draft7Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures' / 'day1'
SCHEMA = json.loads((ROOT / 'schema/waterguard.schema.json').read_text(encoding='utf-8'))
VERSION = '0.1.0'
VERIFIER = 'fixture-reference/0.1.0'


def load(path: Path):
    for attempt in range(6):
        try:
            text=path.read_text(encoding='utf-8')
            break
        except PermissionError as exc:
            # Windows atomic replacement can briefly make a pathname inaccessible.
            # Retry only this OS access error; malformed JSON is never retried.
            if os.name!='nt' or exc.errno!=errno.EACCES or attempt==5:raise
            time.sleep(.01*2**attempt)
    return json.loads(text, parse_constant=lambda x: fail(f'Non-finite JSON: {x}'))


def fail(message):
    raise ValueError(message)


def require(condition, message):
    if not condition:
        fail(message)


def validate(kind: str, value):
    schema = {'$ref': f'#/definitions/{kind}', 'definitions': SCHEMA['definitions']}
    Draft7Validator(schema, format_checker=FormatChecker()).validate(value)

    def finite(item):
        if isinstance(item, float):
            require(math.isfinite(item), 'Non-finite number')
        elif isinstance(item, dict):
            for v in item.values():
                finite(v)
        elif isinstance(item, list):
            for v in item:
                finite(v)
    finite(value)


def digest_bytes(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def digest(value) -> str:
    """Project encoding v1, not RFC 8785. Python is the authoritative hash writer."""
    return digest_bytes(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8'))


def file_hash(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def task_hash(task) -> str:
    # Bind the entire frozen task, including network, overlays, budgets and objectives.
    return digest({k: v for k, v in task.items() if k != 'constraint_hash'})


def plan_hash(plan) -> str:
    return digest({k: v for k, v in plan.items() if k != 'plan_hash'})


def task_ref(task):
    return dict(task_id=task['task_id'], revision=task['revision'],
                network_hash=task['network_ref']['sha256'], constraint_hash=task['constraint_hash'])


def input_hash(task, plan, engine_version):
    return digest(dict(task=task, plan=plan, engine_version=engine_version, perturbation_id=None))


def resolve(root: Path, reference: str) -> Path:
    path = (root / reference).resolve()
    require(path.is_relative_to(root.resolve()), 'Artifact reference escapes fixture root')
    return path


def check_task(task, catalog, scene):
    validate('TaskSpec', task)
    validate('ActionCatalog', catalog)
    validate('NetworkScene', scene)
    require(task_hash(task) == task['constraint_hash'], 'Task hash mismatch')
    require(task['network_ref']['network_id'] == catalog['network_id'] == scene['network_id'], 'Network ID mismatch')
    w = task['time_window']
    require(w['start_s'] <= w['warmup_end_s'] <= w['evaluation_start_s'] < w['end_s'], 'Invalid warmup/evaluation order')
    if task['fault'] is not None:
        require(w['warmup_end_s'] <= task['fault']['start_s'] <= w['evaluation_start_s'], 'Invalid fault/evaluation order')
        require(w['start_s'] <= task['fault']['start_s'] < task['fault']['end_s'] <= w['end_s'], 'Invalid fault window')
    require((w['end_s'] - w['start_s']) % w['step_s'] == 0, 'Incomplete time steps')
    require((w['evaluation_start_s'] - w['start_s']) % w['step_s'] == 0, 'Evaluation not on sample boundary')
    nodes = [n['id'] for n in scene['nodes']]
    links = [n['id'] for n in scene['links']]
    require(len(set(nodes)) == len(nodes) and len(set(links)) == len(links), 'Duplicate scene IDs')
    require(all(l['from_node'] in nodes and l['to_node'] in nodes for l in scene['links']), 'Unknown link endpoint')
    users = {n['id'] for n in scene['nodes'] if n['kind'] == 'junction'}
    require(set(task['hard_constraints']['node_ids']) == users, 'Day-1 evaluation must include all user nodes')
    if task['fault'] is not None:
        require(task['fault']['link_id'] in links, 'Unknown fault link')
    facilities = [f['link_id'] for f in catalog['facilities']]
    require(len(set(facilities)) == len(facilities), 'Duplicate catalog IDs')
    require(set(facilities) <= set(links), 'Unknown catalog link')
    limits = task['action_limits']
    require(set(limits['allowed_link_ids']) <= set(facilities), 'Unknown allowed facility')
    require(w['evaluation_start_s'] <= limits['earliest_s'] <= limits['latest_s'] < w['end_s'], 'Invalid action window')
    require(task['schema_version'] in ('0.3.0','0.4.0') or task['fault'] is None or task['fault']['kind'] == 'closure', 'Reference verifier supports closure only before 0.3.0')


def check_plan(task, plan, catalog):
    validate('ActionPlan', plan)
    require(plan['task_ref'] == task_ref(task), 'Task revision or constraint mismatch')
    require(plan_hash(plan) == plan['plan_hash'], 'Plan hash mismatch')
    limits = task['action_limits']
    require(len(plan['actions']) <= limits['max_actions'], 'Action budget exceeded')
    facilities = {f['link_id']: f for f in catalog['facilities']}
    previous = {}
    for action in plan['actions']:
        link, kind, time = action['link_id'], action['kind'], action['simulation_time_s']
        require(link in limits['allowed_link_ids'] and link in facilities, 'Unknown or unauthorized facility')
        require(kind in limits['allowed_kinds'] and kind in facilities[link]['allowed_actions'], 'Action not allowed')
        require(task['schema_version'] in ('0.3.0','0.4.0') or kind in ('link_open', 'link_close'), 'Pump actions require a verified real adapter')
        require(limits['earliest_s'] <= time <= limits['latest_s'], 'Action outside time window')
        require((time - task['time_window']['start_s']) % task['time_window']['step_s'] == 0, 'Action not on sample boundary')
        require(task['fault'] is None or link != task['fault']['link_id'], 'Action conflicts with forced fault state')
        if task['fault'] and task['fault']['kind'] == 'isolation':
            require(link not in task['fault']['boundary_link_ids'], 'Action conflicts with isolation boundary')
        if link in previous:
            require(time > previous[link] and time - previous[link] >= limits['min_switch_interval_s'], 'Conflicting or too frequent controls')
        previous[link] = time


def verify_fixture(task, plan, result, catalog, scene):
    """Recompute sample metrics. Does not establish physical truth or live eligibility."""
    return _verify_result(task, plan, result, catalog, scene, 'fixture', VERIFIER)


def verify_benchmark(task, plan, result, catalog, scene):
    """Recompute real engine results; no AGH or recommendation attestation."""
    return _verify_result(task, plan, result, catalog, scene, 'benchmark', 'hydraulic-reference/0.2.0')


def _verify_result(task, plan, result, catalog, scene, mode, verifier_version):
    check_task(task, catalog, scene)
    check_plan(task, plan, catalog)
    validate('SimulationResult', result)
    artifact, series, diag = result['artifact'], result['series'], result['diagnostics']
    require(result['mode'] == artifact['mode'] == diag['mode'] == mode, f'{mode} verifier rejects non-{mode} input')
    require(artifact['candidate_id'] == plan['candidate_id'] and artifact['task_ref'] == task_ref(task), 'Simulation binding mismatch')
    require(artifact['time_axis'] == task['time_window'], 'Time axis mismatch')
    require(artifact['perturbation_id'] is None, 'Nominal reference verifier cannot attest stress tests')
    require(artifact['input_hash'] == input_hash(task, plan, artifact['engine_version']), 'Simulation input hash mismatch')
    require(diag['simulation_id'] == artifact['simulation_id'], 'Diagnostic binding mismatch')
    reasons = []
    if artifact['status'] != 'SUCCEEDED' or diag['errors'] or diag['converged'] is False:
        reasons.append('simulation_failed')
    if mode == 'benchmark' and diag['converged'] is not True:
        reasons.append('convergence_unconfirmed')
    if series is None:
        reasons.append('series_missing')
    else:
        require(series['mode'] == mode and series['simulation_id'] == artifact['simulation_id'], 'Series binding mismatch')
    users = task['hard_constraints']['node_ids']
    links = {l['id'] for l in scene['links']}
    w = task['time_window']
    frames = [] if series is None else series['frames']
    if [f['time_s'] for f in frames] != list(range(w['start_s'], w['end_s'], w['step_s'])):
        reasons.append('missing_duplicate_or_unordered_frames')
    for frame in frames:
        if frame['duration_s'] != w['step_s']:
            reasons.append('incorrect_frame_duration')
        node_ids = [n['node_id'] for n in frame['nodes']]
        link_ids = [l['link_id'] for l in frame['links']]
        if set(node_ids) != set(users) or len(node_ids) != len(users):
            reasons.append('missing_duplicate_or_unknown_nodes')
        if set(link_ids) != links or len(link_ids) != len(links):
            reasons.append('missing_duplicate_or_unknown_links')
    constraints = task['hard_constraints']
    shortage = expected = supplied = 0.0
    minimum = None
    violations = []
    critical = {n['id'] for n in scene['nodes'] if n['critical'] and n['kind'] == 'junction'}
    critical_pass = critical_total = 0
    if not reasons:
        for frame in frames:
            t, dt = frame['time_s'], frame['duration_s']
            if t < w['evaluation_start_s']:
                continue
            for n in frame['nodes']:
                d, q, p = n['expected_demand_m3s'], n['delivered_demand_m3s'], n['pressure_m']
                shortage += max(d - q, 0) * dt
                expected += d * dt
                supplied += min(q, d) * dt
                minimum = p if minimum is None else min(minimum, p)
                pressure_ok = p + constraints['pressure_tolerance_m'] >= constraints['min_pressure_m']
                ratio = None if d == 0 else min(q, d) / d
                ratio_ok = ratio is None or ratio + constraints['ratio_tolerance'] >= constraints['min_supply_ratio']
                for ok, code, value, threshold, unit in [
                    (pressure_ok, 'MIN_PRESSURE', p, constraints['min_pressure_m'], 'm'),
                    (ratio_ok, 'MIN_SUPPLY_RATIO', ratio, constraints['min_supply_ratio'], 'ratio'),
                ]:
                    if not ok:
                        violations.append(dict(code=code, node_id=n['node_id'], start_s=t, end_s=t+dt, observed=value, threshold=threshold, unit=unit))
                if n['node_id'] in critical:
                    critical_total += dt
                    critical_pass += dt if pressure_ok and ratio_ok else 0
    facilities = {f['link_id']: f for f in catalog['facilities']}
    report = dict(
        schema_version=task['schema_version'], verification_id='verify-' + plan['candidate_id'], simulation_id=artifact['simulation_id'],
        candidate_id=plan['candidate_id'], task_ref=task_ref(task), mode=mode,
        validity='invalid' if reasons else 'valid', invalid_reasons=sorted(set(reasons)), violations=violations,
        metrics=dict(shortage_m3=round(shortage, 9), supply_ratio=None if expected == 0 else supplied / expected,
                     min_pressure_m=minimum, critical_node_time_pass_ratio=None if critical_total == 0 else critical_pass / critical_total,
                     action_cost=sum(facilities[a['link_id']]['action_cost'] for a in plan['actions'])),
        coverage=dict(start_s=w['evaluation_start_s'], end_s=w['end_s'], assessed_node_ids=users,
                      required_stress_tests=task['perturbation_domain']['required'], completed_stress_tests=[]),
        admission=dict(action_valid='pass', simulation_valid='fail' if reasons else 'pass',
                       nominal_constraints_pass='pending' if reasons else ('fail' if violations else 'pass'),
                       required_stress_tests_pass='pending', recommendation_eligible='fail'),
        recommendation_blockers=[mode + '_only', 'native_agh_trace_missing', 'required_stress_tests_pending'], verifier_version=verifier_version,
    )
    validate('VerificationReport', report)
    return report


class SimulationAdapter(Protocol):
    def simulate(self, task: dict, plan: dict) -> dict:
        """Return SimulationResult; the job runner handles processes and cancellation."""
        ...

    def get_series(self, simulation_id: str, node_id: str | None = None,
                   start_s: int | None = None, end_s: int | None = None) -> dict:
        ...


class FixtureAdapter:
    """Only serves exact frozen inputs. It never predicts unseen actions."""
    def __init__(self, root=FIXTURES):
        self.root = Path(root)

    def simulate(self, task, plan):
        require(task == load(self.root / 'task.json'), 'Fixture adapter only accepts frozen task')
        for name in ('a', 'b'):
            if plan == load(self.root / f'candidate-{name}.json'):
                return copy.deepcopy(load(self.root / f'result-{name}.json'))
        fail('No fixture for this candidate; use a real simulation adapter')

    def get_series(self, simulation_id, node_id=None, start_s=None, end_s=None):
        for name in ('a', 'b'):
            series = load(self.root / f'series-{name}.json')
            if series['simulation_id'] != simulation_id:
                continue
            known = {n['node_id'] for f in series['frames'] for n in f['nodes']}
            require(node_id is None or node_id in known, 'Unknown node')
            require(start_s is None or end_s is None or start_s < end_s, 'Invalid series range')
            series['frames'] = [f for f in series['frames'] if (start_s is None or f['time_s'] >= start_s) and (end_s is None or f['time_s'] < end_s)]
            if node_id is not None:
                for frame in series['frames']:
                    frame['nodes'] = [n for n in frame['nodes'] if n['node_id'] == node_id]
            # Query responses may have no frames. Full SimulationResult may not.
            return series
        fail('Unknown simulation')


def events_after(events, last_sequence=0):
    require(isinstance(last_sequence, int) and 0 <= last_sequence <= len(events), 'Invalid Last-Event-ID')
    return [event for event in events if event['sequence'] > last_sequence]


def check_bundle(root=FIXTURES):
    root = Path(root)
    Draft7Validator.check_schema(SCHEMA)
    manifest, task, catalog, scene = [load(root / name) for name in ('network-manifest.json', 'task.json', 'action-catalog.json', 'scene.json')]
    validate('NetworkManifest', manifest)
    check_task(task, catalog, scene)
    require(manifest['sha256'] == file_hash(resolve(root, manifest['original_file'])) == task['network_ref']['sha256'], 'Network file hash mismatch')
    require(manifest['network_id'] == task['network_ref']['network_id'] and manifest['revision'] == task['network_ref']['revision'], 'Network revision mismatch')
    require(manifest['overlays'][0]['sha256'] == file_hash(resolve(root, manifest['overlays'][0]['ref'])) == task['network_ref']['overlay_hash'], 'Overlay hash mismatch')
    require(task['action_catalog_hash'] == file_hash(resolve(root, task['action_catalog_ref'])), 'Catalog hash mismatch')
    reports = []
    for name in ('a', 'b'):
        plan, result, report = [load(root / f'{prefix}-{name}.json') for prefix in ('candidate', 'result', 'verification')]
        validate('VerificationReport', report)
        artifact = result['artifact']
        require(result['series'] == load(resolve(root, artifact['series_ref'])), 'Embedded series differs from artifact')
        require(result['diagnostics'] == load(resolve(root, artifact['diagnostics_ref'])), 'Embedded diagnostics differs from artifact')
        require(verify_fixture(task, plan, result, catalog, scene) == report, 'Stored report differs from independent recomputation')
        reports.append(report)
    a, b = [load(root / f'candidate-{name}.json') for name in ('a', 'b')]
    require(a['parent_candidate_id'] is None and b['parent_candidate_id'] == a['candidate_id'], 'Candidate lineage mismatch')
    run = load(root / 'run.json')
    validate('RunRecord', run)
    require(run['task_ref'] == task_ref(task), 'Run task mismatch')
    require(run['mode'] == 'fixture' and run['agh_session_ref'] is None and run['model_config_ref'] is None, 'Fixture cannot claim native AGH execution')
    require(run['status'] == 'COMPLETED' and run['conclusion'] == 'INCOMPLETE' and run['selected_candidate_id'] is None, 'Fixture cannot claim final recommendation')
    initial = load(root / 'run-initial.json')
    validate('RunRecord', initial)
    require(initial == {**run, 'status': 'RUNNING', 'phase': 'BASELINE'}, 'Initial run snapshot mismatch')
    events = [json.loads(line) for line in (root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    require([e['sequence'] for e in events] == list(range(1, len(events)+1)), 'Event sequence gap')
    require(len({e['event_id'] for e in events}) == len(events), 'Duplicate event ID')
    for event in events:
        validate('RunEvent', event)
        require(event['run_id'] == run['run_id'] and event['task_revision'] == task['revision'] and event['constraint_hash'] == task['constraint_hash'], 'Event version mismatch')
        require(event['mode'] == 'fixture' and event['agh_session_ref'] is None and event['native_tool_call_ref'] is None and event['native_reference_missing_reason'], 'Forged fixture provenance')
        payload = load(resolve(root, event['payload_ref']))
        if event['candidate_id'] is not None:
            require(event['candidate_id'] in (a['candidate_id'], b['candidate_id']), 'Unknown event candidate')
            require(event['candidate_id'] == (payload.get('artifact') or payload)['candidate_id'], 'Event candidate binding mismatch')
        if event['simulation_id'] is not None:
            require(event['simulation_id'] == (payload.get('artifact') or payload)['simulation_id'], 'Event simulation mismatch')
    require(events[0]['type'] == 'run.started' and load(resolve(root, events[0]['payload_ref'])) == initial, 'Start event must reference initial snapshot')
    require(events[-1]['type'] == 'run.completed' and load(resolve(root, events[-1]['payload_ref'])) == run, 'End event must reference final snapshot')
    evidence = load(root / 'evidence-manifest.json')
    validate('EvidenceManifest', evidence)
    require(evidence['run_id'] == run['run_id'] and evidence['mode'] == 'fixture' and not evidence['live_export_allowed'], 'Fixture cannot be exported as live evidence')
    require(evidence['native_trace_ref'] is None and evidence['association_ref'] is None and 'native_agh_trace' in evidence['missing_items'], 'Missing native evidence must be explicit')
    indexed = [a['ref'] for a in evidence['artifacts']]
    actual = {p.name for p in root.iterdir() if p.is_file() and p.name != 'evidence-manifest.json'}
    require(set(indexed) == actual and len(indexed) == len(actual), 'Evidence index missing or duplicate artifacts')
    require(set(evidence['hashes']) == actual, 'Evidence hash inventory mismatch')
    for entry in evidence['artifacts']:
        require(entry['sha256'] == evidence['hashes'][entry['ref']] == file_hash(resolve(root, entry['ref'])), 'Evidence hash mismatch: ' + entry['ref'])
    require(reports[0]['admission']['nominal_constraints_pass'] == 'fail', 'Candidate A must fail nominal constraints')
    require(reports[1]['admission']['nominal_constraints_pass'] == 'pass', 'Candidate B must pass nominal fixture constraints')
    require(reports[1]['metrics']['shortage_m3'] < reports[0]['metrics']['shortage_m3'], 'Candidate B must improve supply')
    return reports
