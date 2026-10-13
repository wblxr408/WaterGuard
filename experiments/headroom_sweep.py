"""Discover where a bounded search can improve on fixed-order enumeration.

Physical, deterministic and resumable. For each fault scenario it evaluates the
production enumeration (the fixed-order arm is simply its first k plans) plus extra
multi-facility combinations, all under one frozen task and one declared latency. The
output is a headroom map: scenarios where the best tested plan materially beats
fixed-order at a small simulation budget are the only ones where a smarter search,
including an AGH agent, can demonstrate value. Saturated scenarios are recorded too.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import combinations
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))

METRICS = ('min_node_time_pass_ratio', 'mean_node_time_pass_ratio', 'emergency_min_node_time_pass_ratio',
           'emergency_mean_node_time_pass_ratio', 'nodes_reaching_emergency_service',
           'fully_compliant_node_count', 'assessed_node_count', 'shortage_m3', 'min_pressure_m')
LEAK_AREA = 0.01865


def scenario_list(networks, stride):
    from services.api.workbench import network_details
    out = []
    for network in networks:
        links = network_details(network)['fault_link_ids']
        picked = links[::stride] if stride > 1 else links
        if network == 'epa-net3' and '123' not in picked:
            picked = ['123'] + picked
        for link in picked:
            for kind in ('leak', 'rupture', 'closure'):
                out.append(dict(id=f'{network}-{kind}-{link}', network=network, kind=kind, link=link))
    return out


def run_one(spec, output, fixed_limit, extra_top, extra_size, decision_s):
    from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details
    from services.hydraulics.compiler import compile_task
    from services.hydraulics.runtime import PhysicalAdapter
    from services.hydraulics.storage import write
    from services.verifier.service import verify
    from waterguard_contracts.core import plan_hash, task_hash, task_ref
    out = output / spec['id']
    out.mkdir(parents=True, exist_ok=True)
    details = network_details(spec['network'])
    catalog, scene = details['catalog'], details['scene']
    overrides = [dict(link_id=f['link_id'], control_names=f['native_control_names'],
                      reason='Manual operation permitted for every strategy in the headroom study.')
                 for f in catalog['facilities'] if f['requires_explicit_override']]
    kwargs = dict(network_id=spec['network'], fault_kind=spec['kind'], link_id=spec['link'], threshold_mode='manual',
                  min_pressure_m=20, min_supply_ratio=0.95, demand_increase_pct=10, candidate_limit=fixed_limit,
                  max_actions=3, max_wall_time_s=7200,
                  response_latency=dict(decision_wall_clock_s=decision_s, measurement='declared',
                                        source='Same declared decision allowance for every arm in the headroom study.'))
    if spec['kind'] == 'leak':
        kwargs['fault_parameters'] = dict(leak_area_m2=LEAK_AREA)
    if overrides:
        kwargs['control_overrides'] = overrides
    try:
        task = propose_task(TaskProposal(**kwargs))
    except ValueError as error:
        summary = dict(spec, skipped='task_rejected', detail=str(error)[:200], simulations=0)
        write(out / 'summary.json', summary)
        return summary
    if overrides:
        task['action_limits']['allowed_link_ids'] += [o['link_id'] for o in overrides
                                                      if o['link_id'] not in task['action_limits']['allowed_link_ids']]
        task['constraint_hash'] = task_hash(task)
    write(out / 'task.json', task)
    adapter = PhysicalAdapter(out / 'sims')
    plans = benchmark_plans(task, catalog)
    started = time.monotonic()
    rows = []

    def evaluate(plan, origin):
        result = adapter.simulate(task, plan, timeout_s=task['budget']['max_wall_time_s'])
        try:
            report = verify(task, plan, result, catalog, scene, artifact_dir=out / 'sims' / result['artifact']['simulation_id'])
            row = dict(validity=report['validity'], violations=len(report['violations']),
                       **{k: report['metrics'].get(k) for k in METRICS})
        except (FileNotFoundError, ValueError) as error:
            row = dict(validity='simulation_failed', violations=None, error=repr(error)[:160])
        rows.append(dict(order=len(rows), origin=origin, candidate_id=plan['candidate_id'], actions=plan['actions'], **row))
        write(out / 'rows.json', rows)

    for plan in plans:
        evaluate(plan, 'fixed')
        if len(rows) == 1 and rows[0]['validity'] != 'valid':
            summary = dict(spec, skipped='baseline_invalid', detail=rows[0].get('error'), simulations=1)
            write(out / 'summary.json', summary)
            return summary
    singles = [p['actions'][0] for p in plans if len(p['actions']) == 1][:extra_top]
    seen = {json.dumps(p['actions'], sort_keys=True) for p in plans}
    for size in range(2, extra_size + 1):
        for combo in combinations(singles, size):
            actions = sorted(combo, key=lambda a: (a['simulation_time_s'], a['link_id'], a['kind']))
            key = json.dumps(actions, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            plan = dict(schema_version=task['schema_version'], candidate_id=f'extra-{len(rows)}', task_ref=task_ref(task),
                        parent_candidate_id=None, actions=actions, source='enumeration', reason='headroom study')
            plan['plan_hash'] = plan_hash(plan)
            try:
                compile_task(task, plan)
            except Exception:
                continue
            evaluate(plan, 'extra')
    summary = dict(spec, task_ref=task_ref(task), earliest_s=task['action_limits']['earliest_s'],
                   simulations=len(rows), wall_s=time.monotonic() - started)
    write(out / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--networks', nargs='+', default=['epa-net1'])
    parser.add_argument('--stride', type=int, default=1)
    parser.add_argument('--fixed-limit', type=int, default=100)
    parser.add_argument('--extra-top', type=int, default=6)
    parser.add_argument('--extra-size', type=int, default=3)
    parser.add_argument('--decision-s', type=float, default=300)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/headroom-sweep')
    parser.add_argument('--only', nargs='*')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    specs = [s for s in scenario_list(args.networks, args.stride) if not args.only or s['id'] in args.only]
    todo = [s for s in specs if not (args.output / s['id'] / 'summary.json').exists()]
    print('scenarios', len(specs), 'todo', len(todo), flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, s, args.output, args.fixed_limit, args.extra_top, args.extra_size, args.decision_s): s
                   for s in todo}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                done = future.result()
                print('DONE', spec['id'], done.get('skipped', ''), done['simulations'], round(done.get('wall_s', 0)), flush=True)
            except Exception as error:
                (args.output / spec['id']).mkdir(parents=True, exist_ok=True)
                (args.output / spec['id'] / 'error.txt').write_text(repr(error), encoding='utf-8')
                print('FAIL', spec['id'], repr(error)[:200], flush=True)


if __name__ == '__main__':
    main()
