"""Offline-reproducible Chinese network scenarios for the default workbench."""
import argparse
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .adapter import write
from .compiler import assets
from .datasets import CHINA_NETWORK, ROOT, manifest
from .runtime import PhysicalAdapter, engine_identity, seal
from .integrity import verify_inventory, validate_output
from .thresholds import adaptive_case, PREFIX
from waterguard_contracts.core import load, require, verify_benchmark, file_hash
from services.verifier.service import verify

OUTPUT = ROOT / 'artifacts/china-zhijiang'


def current(output):
    """Reuse only complete evidence produced by the current source and engine code."""
    try:
        index = load(Path(output) / 'index.json')
        source = manifest(CHINA_NETWORK)
        expected = [(engine, kind) for engine, kinds in
                    [('wntr', ('normal', 'closure', 'leak', 'rupture', 'isolation')),
                     ('epanet', ('normal', 'closure'))] for kind in kinds]
        if [(c['engine'], c['case']) for c in index['cases']] != expected:
            return False
        identities = {}
        for row in index['cases']:
            folder = Path(output) / row['simulation_id']
            task = load(folder / 'task.json')
            if not task['hard_constraints']['source'].startswith(PREFIX):
                return False
            expected_task, _ = adaptive_case(CHINA_NETWORK, row['case'], row['engine'])
            if task['constraint_hash'] != expected_task['constraint_hash']:
                return False
            if row['network_id'] != CHINA_NETWORK or task['network_ref']['sha256'] != source['sha256']:
                return False
            if task['network_ref']['overlay_hash'] != source['overlays'][0]['sha256']:
                return False
            if row['engine'] not in identities:
                identities[row['engine']] = engine_identity(task)
            verify_inventory(folder)
            result = validate_output(folder)
            if result['artifact']['engine_version'] != identities[row['engine']]:
                return False
            if result['artifact']['status'] != 'SUCCEEDED':
                return False
            if not load(folder / 'verification.json')['verifier_version'].startswith('waterguard-dense-verifier/'):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def run(output=OUTPUT):
    output = Path(output)
    _, _, scene, catalog = assets(CHINA_NETWORK)
    adapter = PhysicalAdapter(output)
    rows = []
    for engine, kinds in [('wntr', ('normal', 'closure', 'leak', 'rupture', 'isolation')),
                          ('epanet', ('normal', 'closure'))]:
        for kind in kinds:
            task, plan = adaptive_case(CHINA_NETWORK, kind, engine)
            result = adapter.simulate(task, plan)
            require(result['artifact']['status'] == 'SUCCEEDED', str(result['diagnostics']['errors']))
            report = verify(task, plan, result, catalog, scene, artifact_dir=output / result['artifact']['simulation_id'])
            require(report['validity'] == 'valid', 'Invalid Chinese network simulation')
            sid = result['artifact']['simulation_id']
            write(output / sid / 'verification.json', report)
            seal(output / sid)
            rows.append(dict(network_id=CHINA_NETWORK, case=kind, engine=engine,
                             simulation_id=sid, metrics=report['metrics'], admission=report['admission']))
            print(CHINA_NETWORK, engine, kind, 'SUCCEEDED', flush=True)
    index = dict(schema_version='0.4.0', mode='benchmark', run_id='china-zhijiang-scenarios',
                 created_at=datetime.now(timezone.utc).isoformat(), cases=rows,
                 attribution='Historical Chinese research model; fault scenarios computed by WaterGuard, not city telemetry.')
    write(output / 'index.json', index)
    return index


def export_with_references(output, target):
    """Include the fault-free evidence used to derive the frozen thresholds."""
    from .acceptance import export
    export(output, target)
    references = {}
    for row in load(Path(output) / 'index.json')['cases']:
        task = load(Path(output) / row['simulation_id'] / 'task.json')
        policy = json.loads(task['hard_constraints']['source'][len(PREFIX):])
        sid = policy['reference_simulation_id']
        if sid not in references:
            matches = list((ROOT / 'artifacts/threshold-baselines').glob('*/' + sid))
            require(len(matches) == 1, 'Threshold reference evidence missing')
            verify_inventory(matches[0])
            result = validate_output(matches[0])
            require(result['artifact']['input_hash'] == policy['reference_input_hash'], 'Threshold reference mismatch')
            references[sid] = (matches[0], policy)
    with zipfile.ZipFile(target, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
        for sid, (folder, _) in references.items():
            for path in sorted(folder.iterdir()):
                if path.is_file():
                    archive.write(path, f'threshold-references/{sid}/{path.name}')
        archive.writestr('threshold-references.json', json.dumps({sid: policy for sid, (_, policy) in references.items()}, ensure_ascii=False, indent=2))
    target = Path(target)
    write(target.with_suffix('.zip.sha256.json'), dict(file=target.name, sha256=file_hash(target), mode='benchmark'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--ensure', action='store_true')
    parser.add_argument('--export', type=Path)
    args = parser.parse_args()
    if args.ensure and current(args.output):
        print('Current Chinese network evidence verified; reusing it.')
    else:
        run(args.output)
    if args.export:
        export_with_references(args.output, args.export)
