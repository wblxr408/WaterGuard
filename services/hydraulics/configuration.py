"""Explicit operational defaults; their effective values are frozen in TaskSpec."""
import os
import math
from pathlib import Path

from waterguard_contracts.core import latency_floor_s, load, require

DEFAULT_PATH = Path(__file__).resolve().parents[2] / 'config/dispatch-defaults.json'


def defaults():
    return load(Path(os.environ.get('WG_DISPATCH_CONFIG', DEFAULT_PATH)))


def demand_domain(increase_pct, nominal_multiplier):
    require(increase_pct > 0, 'Demand increase must be positive')
    multiplier = nominal_multiplier * (1 + increase_pct / 100)
    return dict(required=[f'demand-plus-{increase_pct:.15g}pct'], demand_multipliers=[nominal_multiplier, multiplier])


def response_latency(fault_start_s, step_s, decision_wall_clock_s, *, detection_s=None, actuation_s=None,
                     measurement='measured', source='AGH native session wall-clock'):
    """Freeze the real response latency and its derived no-backdating floor.

    detection_s / actuation_s default to the configured engineering assumptions;
    decision_wall_clock_s is the actual elapsed model-decision time (measured from
    the native session, or declared for a deterministic offline bound)."""
    config = defaults().get('response_latency', {})
    detection = config.get('detection_latency_s', 0) if detection_s is None else detection_s
    actuation = config.get('actuation_latency_s', 0) if actuation_s is None else actuation_s
    floor = latency_floor_s(fault_start_s, step_s, detection, decision_wall_clock_s, actuation)
    return dict(detection_s=float(detection), actuation_s=float(actuation),
                decision_wall_clock_s=float(decision_wall_clock_s), earliest_effective_s=floor,
                measurement=measurement, source=source)


def decision_wall_clock_s(events):
    """Elapsed seconds from the native session start to the selected plan submission.

    `events` are the parsed agh/native/events.jsonl rows. The decision clock is the
    authoritative, independently reproducible latency; monotonic run wall-time is not
    a substitute because it also covers host packaging."""
    from datetime import datetime
    require(bool(events) and events[0].get('type') == 'session/start', 'Native trace missing session/start')
    start = events[0]['ts']
    submits = [e['ts'] for e in events if e.get('type') == 'tool/call'
               and isinstance(e.get('data'), dict) and e['data'].get('name') == 'wg_submit_plan']
    require(bool(submits), 'Native trace has no wg_submit_plan call')
    def when(stamp):
        return datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    elapsed = (when(submits[-1]) - when(start)).total_seconds()
    require(math.isfinite(elapsed) and elapsed >= 0, 'Non-monotonic native decision timestamps')
    return elapsed


def stress_multiplier(task):
    required = task['perturbation_domain']['required']
    require(len(required) == 1, 'Demand review requires one configured growth label')
    label = required[0]
    require(label.startswith('demand-plus-') and label.endswith('pct'), 'Unsupported demand growth label')
    growth = float(label[len('demand-plus-'):-len('pct')])
    require(math.isfinite(growth) and growth > 0, 'Invalid demand growth')
    target = task.get('hydraulic_options', {}).get('demand_multiplier', 1) * (1 + growth / 100)
    matches = [m for m in task['perturbation_domain']['demand_multipliers'] if math.isclose(m, target, rel_tol=1e-12)]
    require(bool(matches), 'Required demand growth has no matching frozen multiplier')
    return matches[0]


def ranking_key(task, report):
    """Frozen lexicographic objective order, never a claim of global optimality."""
    axes = {
        'maximize_min_node_availability': ('min_node_time_pass_ratio', -1),
        'maximize_mean_node_availability': ('mean_node_time_pass_ratio', -1),
        'minimize_shortage': ('shortage_m3', 1),
        'minimize_action_cost': ('action_cost', 1),
    }
    metrics = report['metrics']
    return tuple(float('inf') if metrics.get(axes[o][0]) is None else axes[o][1] * metrics[axes[o][0]]
                 for o in task['objectives']) + (report['candidate_id'],)
