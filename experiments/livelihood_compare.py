"""Three-strategy comparison on one frozen task: fixed order, fixed first aid, first aid plus Agnes refinement.

Fixed order and first aid are deterministic physical arms (no model). The hybrid arm drives the real
AGH session through the API and is only run when the stack is up. Every arm is scored by the same
independent verifier and the same frozen livelihood-first order; gain is never inferred.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))

LIVELIHOOD_KEYS = ('priority_nodes_reaching_emergency_service', 'nodes_reaching_emergency_service', 'assessed_node_count',
                   'emergency_mean_node_time_pass_ratio', 'emergency_min_node_time_pass_ratio', 'fully_compliant_node_count',
                   'min_node_time_pass_ratio', 'mean_node_time_pass_ratio', 'shortage_m3')


def strategy_row(strategy, task, best, *, simulations, model_calls, wall_time_s, baseline=None):
    """One comparable line for the best verified candidate of a strategy; None best means nothing valid."""
    if best is None:
        return dict(strategy=strategy, simulations=simulations, model_calls=model_calls, wall_time_s=wall_time_s, best=None)
    metrics = best['report']['metrics']
    row = dict(strategy=strategy, simulations=simulations, model_calls=model_calls, wall_time_s=wall_time_s,
               candidate_id=best['plan']['candidate_id'], actions=best['plan']['actions'],
               violations=len(best['report']['violations']), **{k: metrics.get(k) for k in LIVELIHOOD_KEYS})
    if baseline is not None:
        base = baseline['report']['metrics']
        row['served_nodes_vs_baseline'] = (metrics.get('nodes_reaching_emergency_service') or 0) - (base.get('nodes_reaching_emergency_service') or 0)
        row['shortage_reduction_m3'] = base['shortage_m3'] - metrics['shortage_m3']
    return row


def best_of(task, records):
    from services.hydraulics.configuration import ranking_key
    valid = [r for r in records if r['report']['validity'] == 'valid']
    return min(valid, key=lambda r: ranking_key(task, r['report'])) if valid else None


def hybrid_row(task, records, run, summary):
    """Report an actual native trial and its delivered plan; a failed trial never acquires invented credit."""
    from waterguard_contracts.core import task_ref
    if run['task_ref'] != task_ref(task):
        raise ValueError('Comparison requires the same frozen task')
    usage = run['budget_usage']
    delivered = summary.get('selected_candidate_id') or summary.get('mitigation_candidate_id')
    best = next((r for r in records if r['plan']['candidate_id'] == delivered), None)
    baseline = next((r for r in records if not r['plan']['actions']), None)
    row = strategy_row('hybrid', task, best, simulations=usage['simulations'], model_calls=usage['model_calls'],
                       wall_time_s=usage['wall_time_s'], baseline=baseline)
    row.update(run_id=run['run_id'], status=run['status'], conclusion=run['conclusion'],
               delivery_source=summary.get('delivery_source'), refinement_gain=summary.get('refinement_gain'),
               error=run.get('error'))
    return row


def deterministic_arms(task, catalog, scene, output):
    """Run fixed order and fixed first aid on one task; first aid reuses the fixed-order results it shares."""
    from services.api.workbench import benchmark_plans
    from services.api.workflow_guard import first_aid_allotment
    from services.hydraulics.runtime import PhysicalAdapter
    from services.hydraulics.storage import write
    from services.verifier.service import verify
    adapter = PhysicalAdapter(output / 'simulations')
    write(output / 'task.json', task)
    plans = benchmark_plans(task, catalog)
    records, clock = [], []
    for plan in plans:
        started = time.monotonic()
        result = adapter.simulate(task, plan, timeout_s=task['budget']['max_wall_time_s'])
        report = verify(task, plan, result, catalog, scene, artifact_dir=output / 'simulations' / result['artifact']['simulation_id'])
        records.append(dict(plan=plan, report=report))
        clock.append(time.monotonic() - started)
    write(output / 'records.json', records)
    baseline = records[0]
    allotment = first_aid_allotment(task, task['budget']['max_simulations'] - 1, 0)
    first_aid = records[:1 + allotment]
    fixed_row = strategy_row('fixed_order', task, best_of(task, records), simulations=len(records), model_calls=0,
                             wall_time_s=sum(clock), baseline=baseline)
    first_aid_row = strategy_row('first_aid', task, best_of(task, first_aid), simulations=len(first_aid), model_calls=0,
                                 wall_time_s=sum(clock[:1 + allotment]), baseline=baseline)
    return [fixed_row, first_aid_row], records


def main():
    from services.api.workbench import TaskProposal, network_details, propose_task
    from services.hydraulics.storage import write
    from waterguard_contracts.core import load
    parser = argparse.ArgumentParser()
    parser.add_argument('--proposal', type=Path, default=ROOT / 'artifacts/live-mitigation-acceptance/proposal-delivery.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/livelihood-compare')
    parser.add_argument('--candidate-limit', type=int, default=8)
    parser.add_argument('--priority-nodes', nargs='*', help='Dispatcher-declared priority service nodes')
    parser.add_argument('--frozen-task', type=Path, help='Use exactly this task for all three strategies')
    parser.add_argument('--live', action='store_true', help='Execute/resume the real AGH hybrid strategy')
    args = parser.parse_args()
    proposal = load(args.proposal)
    proposal.update(candidate_limit=args.candidate_limit)
    if args.priority_nodes:
        proposal['service_priorities'] = dict(node_ids=args.priority_nodes, basis='Declared on the command line for this comparison')
    task = load(args.frozen_task) if args.frozen_task else propose_task(TaskProposal(**proposal))
    details = network_details(proposal['network_id'])
    args.output.mkdir(parents=True, exist_ok=True)
    saved = args.output / 'deterministic-arms.json'
    if args.frozen_task and saved.exists():
        from waterguard_contracts.core import task_ref
        from services.verifier.service import verify
        previous = load(saved)
        if any(task_ref(task).get(k) != v for k, v in previous['task_ref'].items()):
            raise ValueError('Comparison requires the same frozen task')
        rows = previous['rows']
        # Recover the previously executed physical arms from their actual raw intervals.
        records = []
        for folder in (args.output / 'simulations').glob('sim-*'):
            plan, result = load(folder / 'plan.json'), load(folder / 'result.json')
            report = verify(task, plan, result, details['catalog'], details['scene'], artifact_dir=folder)
            records.append(dict(plan=plan, report=report))
        records.sort(key=lambda r: int(r['plan']['candidate_id'].rsplit('-', 1)[1]))
    else:
        rows, records = deterministic_arms(task, details['catalog'], details['scene'], args.output)
    from waterguard_contracts.core import task_ref
    write(args.output / 'deterministic-arms.json', dict(task_ref=task_ref(task), objectives=task['objectives'],
        earliest_s=task['action_limits']['earliest_s'], rows=rows))
    for row in rows:
        print(row['strategy'], row.get('candidate_id'), 'served+', row.get('served_nodes_vs_baseline'),
              'shortage-', row.get('shortage_reduction_m3'), 'sims', row['simulations'], flush=True)
    if args.live:
        from experiments.submission_completion import live, api
        audit = live(task, args.output / 'hybrid-live')
        run = api('/api/runs/' + audit['run_id'])
        root = ROOT / 'artifacts' / run['run_id']
        summary_path = root / 'recommendation-summary.json'
        summary = load(summary_path) if summary_path.exists() else {}
        native_records = []
        # native_audit independently remeasures each candidate before this aggregation.
        if audit['native_export_allowed']:
            for folder in (root / 'candidates').iterdir():
                native_records.append(dict(plan=load(folder / 'plan.json'), report=load(folder / 'verification.json')))
        rows.append(hybrid_row(task, native_records, run, summary))
        write(args.output / 'comparison.json', dict(task_ref=run['task_ref'], objectives=task['objectives'],
            earliest_s=task['action_limits']['earliest_s'], rows=rows, native_audit=audit,
            scope='Same frozen task and simulation cap; fixed arms are physical-only, hybrid includes real model and closure costs.'))
        print('HYBRID', rows[-1], flush=True)


if __name__ == '__main__':
    main()
