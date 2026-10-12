"""Run a user-configured real AGH dispatch and require actual fault recovery.

The discovery scenario is declared input, never a dispatch answer. Only the
model chooses controls. Independent raw-artifact audits must prove recovery.
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from scripts.contest_acceptance import api, inspect_run, source_hashes
from services.api.workbench import TaskProposal, propose_task
from services.hydraulics.storage import write
from waterguard_contracts.core import load, require


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--proposal', type=Path, required=True)
    parser.add_argument('--base', default='http://127.0.0.1:8010')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inspect-run')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    frozen_sources = source_hashes()
    if args.inspect_run:
        run_id = args.inspect_run
        run = api(args.base, '/api/runs/' + run_id)
        write(args.output / 'task.json', api(args.base, '/api/tasks/' + run['task_ref']['task_id'] + '?revision=' + str(run['task_ref']['revision'])))
    else:
        proposed = propose_task(TaskProposal(**load(args.proposal)))
        require(proposed['fault'] is not None, 'Dispatch success requires an actual declared fault')
        task = api(args.base, '/api/tasks', proposed)
        write(args.output / 'task.json', task)
        write(args.output / 'source_hashes.json', frozen_sources)
        run = api(args.base, '/api/tasks/' + task['task_id'] + '/runs',
                  dict(mode='live', idempotency_key=task['task_id']))
        run_id = run['run_id']
        write(args.output / 'run.json', run)
        print('LIVE', run_id, flush=True)
        deadline = time.monotonic() + task['budget']['max_wall_time_s'] + 60
        while time.monotonic() < deadline:
            run = api(args.base, '/api/runs/' + run_id)
            if run['status'] in {'COMPLETED', 'ERROR', 'CANCELLED', 'BUDGET_EXHAUSTED'}:
                break
            time.sleep(2)
        time.sleep(1)
    audit = inspect_run(args.base, run_id, args.output)
    task = load(args.output / 'task.json')
    require(task.get('response_latency') is not None,
            'Real-time dispatch requires a frozen response_latency; add it to the proposal')
    # Independently re-measure the model decision wall-clock from the frozen native
    # trace and prove the frozen action floor did not backdate the decision.
    from services.hydraulics.configuration import decision_wall_clock_s
    from waterguard_contracts.core import latency_floor_s
    events = [json.loads(line) for line in
              (ROOT / 'artifacts' / run_id / 'agh/native/events.jsonl').read_text(encoding='utf-8').splitlines()]
    measured_decision_s = decision_wall_clock_s(events)
    latency = task['response_latency']
    measured_floor = latency_floor_s(task['fault']['start_s'], task['time_window']['step_s'],
                                     latency['detection_s'], measured_decision_s, latency['actuation_s'])
    require(task['time_window']['evaluation_start_s'] == task['fault']['start_s'],
            'Accounting window must start at fault onset')
    require(task['action_limits']['earliest_s'] >= measured_floor,
            f'Frozen action floor {task["action_limits"]["earliest_s"]}s backdates the real decision '
            f'(measured floor {measured_floor}s from decision wall-clock {measured_decision_s:.3f}s)')
    rows = audit['candidates']
    baseline = next(r for r in rows if not r['actions'] and r['perturbation'] is None)
    require(not baseline['nominal_pass'], 'Fault did not cause original-constraint failure')
    require(all(a['simulation_time_s'] >= task['action_limits']['earliest_s']
                for r in rows if r['perturbation'] is None for a in r['actions']),
            'A candidate action backdates before the real-latency floor')

    # Honest-negative aware: record whatever the audited run concluded.
    feasible = audit['conclusion'] == 'FEASIBLE_FOUND' and audit['selected_candidate_id']
    selected = per_node = stress = None
    if feasible:
        selected = next(r for r in rows if r['candidate_id'] == audit['selected_candidate_id'] and r['perturbation'] is None)
        stress = [r for r in rows if r['candidate_id'] == selected['candidate_id'] and r['perturbation'] is not None]
        require(selected['actions'] and selected['nominal_pass'], 'Selected candidate did not pass nominal constraints')
        require(stress and all(r['nominal_pass'] for r in stress), 'Same-plan demand-x revalidation did not pass')
        require(selected['metrics']['shortage_m3'] < baseline['metrics']['shortage_m3'] or
                selected['metrics']['mean_node_time_pass_ratio'] > baseline['metrics']['mean_node_time_pass_ratio'],
                'Control did not improve measured supply')
        base_av = {r['node_id']: r for r in baseline['metrics']['node_availability']}
        sel_av = {r['node_id']: r for r in selected['metrics']['node_availability']}
        per_node = [dict(node_id=n, assessed_duration_s=base_av[n]['assessed_duration_s'],
                         baseline_normal_s=base_av[n]['normal_supply_duration_s'],
                         selected_normal_s=sel_av[n]['normal_supply_duration_s'],
                         delta_s=sel_av[n]['normal_supply_duration_s'] - base_av[n]['normal_supply_duration_s'])
                    for n in sorted(base_av, key=lambda x: (int(x) if x.isdigit() else 1 << 30, x))]
        require(not any(r['delta_s'] < 0 for r in per_node), 'A user node regressed vs the no-action baseline')
        require(any(r['delta_s'] > 0 for r in per_node), 'No user node improved vs the no-action baseline')
    require(source_hashes() == frozen_sources, 'Execution sources changed during acceptance')
    outcome = dict(run_id=run_id, conclusion=audit['conclusion'], selected_candidate_id=audit['selected_candidate_id'],
        baseline=baseline, selected=selected, stress=stress, per_node_improvement=per_node,
        actions_chosen_by='actual AGH native tool calls',
        fault_start_s=task['fault']['start_s'], evaluation_start_s=task['time_window']['evaluation_start_s'],
        frozen_response_latency=latency, measured_decision_wall_clock_s=measured_decision_s,
        measured_latency_floor_s=measured_floor, frozen_action_floor_s=task['action_limits']['earliest_s'],
        first_action_s=min((a['simulation_time_s'] for r in rows if r['perturbation'] is None for a in r['actions']), default=None),
        no_backdating='Frozen action floor >= floor derived from the independently measured decision wall-clock.',
        scope='Declared public-model discovery case; no field deployment or global optimality claim. Honest negatives retained.')
    write(args.output / ('success.json' if feasible else 'honest-negative.json'), outcome)
    print(json.dumps(dict(run_id=run_id, conclusion=audit['conclusion'],
        earliest_s=task['action_limits']['earliest_s'], measured_decision_wall_clock_s=round(measured_decision_s, 3),
        selected=selected['actions'] if selected else None), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
