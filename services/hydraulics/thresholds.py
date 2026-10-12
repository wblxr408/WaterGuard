"""Freeze relative acceptance thresholds from a separately verified, fault-free model.

PDD parameters stay unchanged: acceptance thresholds must not alter the physics
used to derive their own reference. This is a relative degradation criterion.
"""
import json
import math
import threading
from pathlib import Path

from .compiler import assets
from .datasets import ROOT
from .runtime import PhysicalAdapter, engine_identity, seal
from .integrity import validate_output, verify_inventory
from .scenarios import physical_case, rebind
from .storage import write
from waterguard_contracts.core import digest, load, require, verify_benchmark

PREFIX = 'WaterGuard thresholds v1: '
from .configuration import defaults
_lock = threading.RLock()


def derive(series, window, retention=None):
    """Use every evaluated node/time; never fit to a fault or candidate result."""
    retention = defaults()['baseline_retention'] if retention is None else retention
    require(math.isfinite(retention) and 0 < retention <= 1, 'Invalid baseline retention')
    frames = [f for f in series['frames'] if window['evaluation_start_s'] <= f['time_s'] < window['end_s']]
    require(bool(frames), 'No evaluated baseline frames; select manual thresholds')
    pressures, ratios = [], []
    for frame in frames:
        for node in frame['nodes']:
            p, d, q = node['pressure_m'], node['expected_demand_m3s'], node['delivered_demand_m3s']
            require(all(math.isfinite(x) for x in (p, d, q)), 'Non-finite baseline; select manual thresholds')
            pressures.append(p)
            if d > 0:
                ratios.append(max(q, 0) / d)
    require(pressures and ratios, 'Baseline has no evaluated demand; select manual thresholds')
    p, r = min(pressures), min(ratios)
    require(p > .02 and r > .001, 'Baseline has nonpositive pressure or effectively no supply; select manual thresholds')
    return dict(min_pressure_m=math.floor(p * retention * 100) / 100,
                min_supply_ratio=math.floor(min(r, 1) * retention * 10000) / 10000,
                baseline_min_pressure_m=p, baseline_min_supply_ratio=r,
                retention=retention)


def recommend(network_id, engine='wntr', root=None, retention=None):
    task, plan = physical_case(network_id, 'normal', engine)
    identity = engine_identity(task)
    key = digest(dict(task_hash=task['constraint_hash'], engine=identity))[7:]
    folder = Path(root or ROOT / 'artifacts/threshold-baselines') / key
    adapter = PhysicalAdapter(folder)
    with _lock:
        result = None
        try:
            sid = load(folder / 'reference.json')['simulation_id']
            saved = folder / sid
            verify_inventory(saved)
            result = validate_output(saved)
            require(result['artifact']['task_ref']['constraint_hash'] == task['constraint_hash'], 'Baseline binding mismatch')
            require(result['artifact']['engine_version'] == identity, 'Baseline engine changed')
        except (OSError, ValueError, KeyError, TypeError):
            result = adapter.simulate(task, plan)
            require(result['artifact']['status'] == 'SUCCEEDED', 'Fault-free reference simulation failed; select manual thresholds')
            write(folder / 'reference.json', dict(simulation_id=result['artifact']['simulation_id']))
        _, _, scene, catalog = assets(network_id)
        report = verify_benchmark(task, plan, result, catalog, scene)
        require(report['validity'] == 'valid', 'Invalid reference simulation; select manual thresholds')
        saved = folder / result['artifact']['simulation_id']
        if not (saved / 'verification.json').is_file():
            write(saved / 'verification.json', report)
            seal(saved)
        from services.verifier.service import dense_frames
        values = derive({'frames': dense_frames(saved, task)}, task['time_window'], retention)
        return dict(mode='adaptive', algorithm='baseline-envelope-v2-dense', **values,
                    reference_simulation_id=result['artifact']['simulation_id'],
                    reference_input_hash=result['artifact']['input_hash'],
                    reference_task_hash=task['constraint_hash'], engine_version=identity,
                    network_ref=task['network_ref'], time_window=task['time_window'],
                    hydraulic_options=task['hydraulic_options'],
                    interpretation=f"Fault-free minimum node/time values × {values['retention'] * 100:g}%; relative degradation thresholds, not regulatory limits.")


def apply(task, policy):
    task['hard_constraints'].update(min_pressure_m=policy['min_pressure_m'],
        min_supply_ratio=policy['min_supply_ratio'], source=PREFIX + json.dumps(policy, ensure_ascii=False, sort_keys=True, allow_nan=False))
    task['assumptions'] = [
        'PDD required pressure is 20 m, fault parameters and the three-hour window are WaterGuard physical scenario assumptions; acceptance thresholds are declared separately in hard_constraints.'
        if item.startswith('PDD threshold 20 m, supply ratio 0.95,') else item
        for item in task['assumptions']]
    task['assumptions'].append('Acceptance thresholds are frozen before candidate evaluation; hydraulic PDD settings remain independent. ' + policy.get('interpretation', 'User-entered thresholds.'))


def adaptive_case(network_id, kind, engine='wntr'):
    task, plan = physical_case(network_id, kind, engine)
    apply(task, recommend(network_id, engine))
    rebind(task, plan)
    return task, plan
