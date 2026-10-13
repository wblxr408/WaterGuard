"""Measure fixed-order search quality per simulation budget and the headroom beyond it.

Read-only study: uses the production enumeration, physical adapter and independent
verifier. Output is descriptive evidence for design decisions, not a delivery claim.
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.storage import write
from services.verifier.service import verify
from waterguard_contracts.core import load, plan_hash, task_ref

KEYS = ('min_node_time_pass_ratio', 'mean_node_time_pass_ratio', 'emergency_min_node_time_pass_ratio',
        'emergency_mean_node_time_pass_ratio', 'nodes_reaching_emergency_service',
        'fully_compliant_node_count', 'assessed_node_count', 'shortage_m3', 'min_pressure_m')


def evaluate(adapter, task, plan, catalog, scene, out):
    result = adapter.simulate(task, plan, timeout_s=task['budget']['max_wall_time_s'])
    report = verify(task, plan, result, catalog, scene, artifact_dir=out / 'sims' / result['artifact']['simulation_id'])
    return dict(candidate_id=plan['candidate_id'], actions=plan['actions'], validity=report['validity'],
                violations=len(report['violations']), **{k: report['metrics'].get(k) for k in KEYS})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--proposal', type=Path, default=ROOT / 'artifacts/live-mitigation-acceptance/proposal-delivery.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/search-headroom/net3-closure-123')
    parser.add_argument('--fixed-limit', type=int, default=40)
    parser.add_argument('--top-k', type=int, default=8)
    parser.add_argument('--max-size', type=int, default=3)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    proposal = load(args.proposal)
    proposal.update(candidate_limit=args.fixed_limit, max_wall_time_s=3600)
    task = propose_task(TaskProposal(**proposal))
    details = network_details(proposal['network_id'])
    catalog, scene = details['catalog'], details['scene']
    write(out / 'task.json', task)
    adapter = PhysicalAdapter(out / 'sims')
    started = time.monotonic()

    fixed_plans = benchmark_plans(task, catalog)
    rows = []
    for plan in fixed_plans:
        rows.append(evaluate(adapter, task, plan, catalog, scene, out))
        write(out / 'fixed.json', rows)
        print('fixed', len(rows), plan['candidate_id'], round(time.monotonic() - started), flush=True)

    singles = [p['actions'][0] for p in fixed_plans if len(p['actions']) == 1]
    top = singles[:args.top_k]
    broad = []
    from services.hydraulics.compiler import compile_task
    seen = {json.dumps(p['actions'], sort_keys=True) for p in fixed_plans}
    for size in range(2, args.max_size + 1):
        for combo in combinations(top, size):
            actions = sorted(combo, key=lambda a: (a['simulation_time_s'], a['link_id'], a['kind']))
            key = json.dumps(actions, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            plan = dict(schema_version=task['schema_version'], candidate_id=f'broad-{len(broad)}', task_ref=task_ref(task),
                        parent_candidate_id=None, actions=actions, source='enumeration', reason='headroom study')
            plan['plan_hash'] = plan_hash(plan)
            compile_task(task, plan)
            broad.append(evaluate(adapter, task, plan, catalog, scene, out))
            write(out / 'broad.json', broad)
            print('broad', len(broad), [a['link_id'] + a['kind'] for a in actions], round(time.monotonic() - started), flush=True)
    write(out / 'summary.json', dict(task_ref=task_ref(task), earliest_s=task['action_limits']['earliest_s'],
                                     fixed=len(rows), broad=len(broad), wall_s=time.monotonic() - started))


if __name__ == '__main__':
    main()
