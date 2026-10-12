"""Verify engine artifacts without consulting the model or agent self-assessment."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))

from waterguard_contracts.core import _verify_result, load, require, validate, verify_benchmark, verify_fixture


def dense_frames(folder: Path, task: dict) -> list[dict]:
    """Read already integrity-checked intervals; callers validate provenance first."""
    import pandas as pd
    pressure = pd.read_csv(folder / 'raw-node-pressure.csv', index_col='time_s', float_precision='round_trip')
    demand = pd.read_csv(folder / 'raw-node-demand.csv', index_col='time_s', float_precision='round_trip')
    expected = pd.read_csv(folder / 'expected-demand.csv', index_col='time_s', float_precision='round_trip')
    times, frames = list(pressure.index), []
    window = task['time_window']
    for raw_time, end in zip(times, times[1:]):
        start = max(int(raw_time), window['evaluation_start_s'])
        end = min(int(end), window['end_s'])
        if start < end:
            frames.append(dict(time_s=start, duration_s=end-start, nodes=[
                dict(node_id=node, pressure_m=float(pressure.at[raw_time, node]),
                     expected_demand_m3s=max(0., float(expected.at[raw_time, node])),
                     delivered_demand_m3s=max(0., float(demand.at[raw_time, node])))
                for node in task['hard_constraints']['node_ids']]))
    return frames


def verify(task: dict, plan: dict, result: dict, catalog: dict, scene: dict, perturbation: dict | None = None,
           *, artifact_dir: Path | None = None) -> dict:
    """Recompute constraints and metrics from stored series in its declared mode."""
    if result.get("mode") == "benchmark":
        if task['schema_version'] == '0.4.0':
            require(artifact_dir is not None, 'Dense verification requires original hydraulic artifacts')
            from services.hydraulics.integrity import validate_output, verify_inventory
            folder = Path(artifact_dir)
            verify_inventory(folder)
            require(validate_output(folder) == result, 'Verifier result differs from original artifacts')
            require(load(folder / 'task.json') == task and load(folder / 'plan.json') == plan,
                    'Verifier frozen input mismatch')
            require(load(folder / 'request.json').get('perturbation') == perturbation,
                    'Verifier perturbation mismatch')
            # Never accept the worker's summary/pass flags as business truth.
            # Re-integrate raw interval starts, including off-grid controls.
            frames = dense_frames(folder, task)
            report = _verify_result(task, plan, result, catalog, scene, 'benchmark',
                                    'waterguard-dense-verifier/1.1.0', perturbation, assessment_frames=frames)
            if perturbation is not None and set(task['perturbation_domain']['required']) <= set(perturbation['required_labels']):
                passed = report['validity'] == 'valid' and not report['violations']
                report['admission']['required_stress_tests_pass'] = 'pass' if passed else 'fail'
                report['recommendation_blockers'].remove('required_stress_tests_pending')
                if not passed:
                    report['recommendation_blockers'].append('required_stress_tests_failed')
                validate('VerificationReport', report)
            return report
        return verify_benchmark(task, plan, result, catalog, scene, perturbation)
    if result.get("mode") == "fixture":
        return verify_fixture(task, plan, result, catalog, scene, perturbation)
    raise ValueError("Verifier accepts only benchmark or fixture simulation artifacts")
