"""Verify a portable Live ZIP and optionally recalculate every frozen hydraulic run.

No API, model key, SQLite state or cached worker metrics are used for admission.
Original AGH execution remains original; this command is an offline verifier.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from waterguard_contracts.core import file_hash, load, require, task_hash
from services.hydraulics.storage import write
from services.hydraulics.runtime import PhysicalAdapter, engine_identity
from services.hydraulics.replay import compare_frames
from services.verifier.service import verify, dense_frames
from services.api.workbench import network_details


def extract_verified(archive_path, destination):
    """Reject traversal, duplicate names and altered manifest entries before use."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'Duplicate ZIP entry')
        require(sum(i.file_size for i in archive.infolist()) <= 1024**3, 'Archive exceeds the 1 GiB review limit')
        for entry in archive.infolist():
            name = entry.filename
            path = PurePosixPath(name)
            require(not path.is_absolute() and '\\' not in name and ':' not in name and
                    '..' not in path.parts and not stat.S_ISLNK(entry.external_attr >> 16), 'Unsafe archive member')
            require((destination / name).resolve().is_relative_to(destination), 'Archive member escapes output')
        require(archive.testzip() is None, 'ZIP CRC failure')
        archive.extractall(destination)
    manifest = load(destination / 'evidence-manifest.json')
    require(manifest['mode'] == 'live' and manifest['live_export_allowed'], 'Archive is not a terminal native Live package')
    refs = set()
    for item in manifest['artifacts']:
        reference = item['ref']
        require(reference.startswith('artifact:'), 'Unsupported manifest reference')
        relative = reference.removeprefix('artifact:')
        path = (destination / relative).resolve()
        require(path.is_relative_to(destination) and path.is_file(), 'Manifest entry is missing or outside archive')
        require(relative not in refs, 'Duplicate manifest reference')
        refs.add(relative)
        require(file_hash(path) == item['sha256'] == manifest['hashes'][reference], 'Archive manifest hash mismatch')
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    require(actual == refs | {'evidence-manifest.json'}, 'Archive contains files outside the evidence inventory')
    return manifest


def validate_native(root, run):
    require(run['mode'] == 'live' and run['status'] == 'COMPLETED' and run['budget_usage']['model_calls'] > 0,
            'No completed actual model run')
    events = [json.loads(l) for l in (root / 'agh/native/events.jsonl').read_text(encoding='utf-8').splitlines()]
    calls = {e['data']['toolUseId']: e['data'] for e in events if e['type'] == 'tool/call'}
    successful = {calls[e['data']['toolUseId']]['name'] for e in events if e['type'] == 'tool/result' and
                  e['data']['toolUseId'] in calls and not e['data'].get('isError') and
                  not (isinstance(e['data'].get('structured'), dict) and e['data']['structured'].get('accepted') is False)}
    require({'wg_get_task', 'wg_inspect_network', 'wg_generate_candidates', 'wg_submit_plan',
             'wg_simulate_candidate', 'wg_verify_candidate', 'wg_compare_candidates', 'wg_build_evidence'} <= successful,
            'Original native record lacks successful core workflow tools')
    association = load(root / 'agh/association.json')
    require(association['agh_session_ref'] == run['agh_session_ref'], 'Native session binding mismatch')
    application = {e['event_id']: e for e in [json.loads(l) for l in (root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]}
    for row in association['events']:
        event = application[row['application_event_id']]
        require(event['agh_session_ref'] == run['agh_session_ref'] and event['native_tool_call_ref'] == row['native_tool_call_ref'],
                'Application/native event join mismatch')
        use_id = row['native_tool_call_ref'].removeprefix('agh:' + run['agh_session_ref'] + ':tool:')
        require(use_id in calls, 'Associated event has no original native call')
    require(association['events'], 'No native event associations')
    return len(calls)


def review(archive_path, output, recompute=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not (output / 'report.json').exists(), 'Use a new output directory; review reports are immutable')
    results = []
    nominal, tested, stress_passed = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix='original-', dir=output) as temporary:
        root = Path(temporary)
        manifest = extract_verified(archive_path, root)
        run, task = load(root / 'run.json'), load(root / 'task.json')
        require(run['run_id'] == manifest['run_id'] and task_hash(task) == task['constraint_hash'], 'Run/task binding mismatch')
        native_calls = validate_native(root, run)
        details = network_details(task['network_ref']['network_id'])
        for child in sorted((root / 'agh/children').iterdir()):
            if not child.is_dir():
                continue
            require(load(child / 'task.json') == task, 'Child task differs from original freeze')
            perturbation = load(child / 'perturbation.json') if (child / 'perturbation.json').is_file() else None
            for path in sorted((child / 'candidates').glob('*/verification.json')):
                plan, previous, saved_report = load(path.with_name('plan.json')), load(path.with_name('result.json')), load(path)
                simulation = child / 'simulations' / previous['artifact']['simulation_id']
                report = verify(task, plan, previous, details['catalog'], details['scene'], perturbation, artifact_dir=simulation)
                require(report == saved_report, 'Original report cannot be independently reproduced')
                passed = report['validity'] == 'valid' and not report['violations']
                if perturbation is None:
                    nominal[plan['candidate_id']] = (plan, report, passed)
                else:
                    key = (plan['candidate_id'], plan['plan_hash'])
                    if report['validity'] == 'valid':
                        tested.setdefault(key, set()).update(perturbation['required_labels'])
                    if passed:
                        stress_passed.setdefault(key, set()).update(perturbation['required_labels'])
                row = dict(child_run_id=child.name, candidate_id=plan['candidate_id'], original_simulation_id=report['simulation_id'],
                    perturbation=perturbation, report_reproduced=True, metrics=report['metrics'],
                    nominal_pass=report['validity'] == 'valid' and not report['violations'])
                if recompute:
                    require(previous['artifact']['engine_version'] == engine_identity(task),
                            'Implementation/runtime differs; exact numerical replay requires the recorded environment and source')
                    target = output / 'recomputed' / child.name
                    actual = PhysicalAdapter(target).simulate(task, plan, perturbation=perturbation, timeout_s=task['budget']['max_wall_time_s'])
                    folder = target / actual['artifact']['simulation_id']
                    fresh = verify(task, plan, actual, details['catalog'], details['scene'], perturbation, artifact_dir=folder)
                    comparison = compare_frames(dense_frames(folder, task), dense_frames(simulation, task))
                    require(comparison['numerically_equivalent'], 'Full internal hydraulic intervals differ')
                    metric_comparison = compare_frames(fresh['metrics'], report['metrics'])
                    require(metric_comparison['numerically_equivalent'] and fresh['violations'] == report['violations'], 'Fresh business result differs')
                    require(actual['artifact']['input_hash'] == previous['artifact']['input_hash'], 'Replay changed a physical input')
                    row.update(recomputed=True, new_simulation_id=fresh['simulation_id'], internal_interval_comparison=comparison,
                               metric_comparison=metric_comparison, input_hash_identical=True)
                results.append(row)
        require(results, 'No independently reviewed original calculations')
        recommendation = load(root / 'recommendation-summary.json')
        required = set(task['perturbation_domain']['required'])
        eligible = [cid for cid, (plan, report, passed) in nominal.items() if passed and
                    required <= stress_passed.get((cid, plan['plan_hash']), set())]
        selected = min(eligible, key=lambda cid: (nominal[cid][1]['metrics']['shortage_m3'], nominal[cid][1]['metrics']['action_cost'], cid)) if eligible else None
        conclusion = 'FEASIBLE_FOUND' if selected else 'NO_FEASIBLE_FOUND' if nominal and all(
            not passed or required <= tested.get((cid, plan['plan_hash']), set()) for cid, (plan, report, passed) in nominal.items()) else 'INCOMPLETE'
        require(recommendation['conclusion'] == run['conclusion'] == conclusion and
                recommendation['selected_candidate_id'] == run['selected_candidate_id'] == selected and
                set(recommendation['eligible_candidate_ids']) == set(eligible), 'Original host conclusion cannot be independently derived')
        result = dict(version='waterguard-portable-evidence-review/1.0.0', created_at=datetime.now(timezone.utc).isoformat(),
            archive=str(Path(archive_path).resolve()), archive_sha256=file_hash(Path(archive_path)), run_id=run['run_id'],
            task_ref=run['task_ref'], mode='offline-evidence-review', agh_reexecuted=False, native_calls=native_calls,
            manifest_files=len(manifest['artifacts']), conclusion=run['conclusion'], selected_candidate_id=run['selected_candidate_id'],
            original_reports_reproduced=len(results), host_conclusion_reproduced=True,
            fresh_simulations=sum(row.get('recomputed', False) for row in results),
            cases=results, passed=True)
    write(output / 'report.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recompute', action='store_true', help='Fresh solver runs, including stress; requires exact frozen implementation/environment')
    args = parser.parse_args()
    result = review(args.archive, args.output, args.recompute)
    print(json.dumps({k: result[k] for k in ('run_id', 'passed', 'original_reports_reproduced', 'fresh_simulations', 'conclusion')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
