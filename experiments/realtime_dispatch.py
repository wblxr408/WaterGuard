"""Deterministic real-time dispatch proof: honest response latency, no backdating.

For each frozen fault scenario this advances the post-fault timeline with an action
placed no earlier than the server-enforced real-latency floor
(fault_start + ceil((detection + decision_wall_clock + actuation)/step)*step),
accumulates ALL non-compliant node-seconds from fault onset (including the full
latency window before the action lands), measures per-node supply improvement vs the
no-action baseline, and revalidates the SAME plan under a configurable demand increase.

This is the DETERMINISTIC physical proof. It is NOT an AGH autonomous-decision record:
the mitigating action is chosen by the frozen objective over the server-enumerated
finite domain, exactly like the equal-budget fixed baseline. The live model-decision
acceptance (which reads the measured decision wall-clock from the native session)
runs separately once the AGH stack is up. Honest negatives are REQUIRED output: a
scenario that cannot recover under real latency is recorded, never massaged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.storage import write
from services.verifier.service import verify
from services.hydraulics.configuration import stress_multiplier
from waterguard_contracts.core import file_hash, require, task_hash

# Different fault LOCATION, TYPE, SEVERITY and DEMAND across the three public models.
# decision_wall_clock_s: for the deterministic proof we charge a conservative, declared
# model-decision latency (the real validated Net1 run measured 569.632s end-to-end);
# the live acceptance replaces this with the measured native-session value.
SCENARIOS = [
    dict(id='net1-leak-22', network='epa-net1', fault_kind='leak', link_id='22',
         fault_parameters={'leak_area_m2': 0.01865}, demand_increase_pct=7.5,
         decision_wall_clock_s=569.632, candidate_limit=24, max_actions=4,
         note='Validated location and severity, now charged the honest measured latency (floor 4500s).'),
    dict(id='net1-leak-22-severe', network='epa-net1', fault_kind='leak', link_id='22',
         fault_parameters={'leak_area_m2': 0.025}, demand_increase_pct=15,
         decision_wall_clock_s=400, candidate_limit=24, max_actions=4,
         note='Same pipe, larger orifice and higher demand growth; baseline fails harder.'),
    dict(id='net1-rupture-12', network='epa-net1', fault_kind='rupture', link_id='12',
         fault_parameters={}, demand_increase_pct=10,
         decision_wall_clock_s=450, candidate_limit=24, max_actions=4,
         note='Different location and a full-bore rupture (more severe type) than the leak cases.'),
    dict(id='net3-closure-123', network='epa-net3', fault_kind='closure', link_id='123',
         fault_parameters={}, demand_increase_pct=10, override_pump='10',
         decision_wall_clock_s=500, candidate_limit=24, max_actions=4,
         note='Transmission closure with a permitted backup pump; expected honest-negative.'),
    dict(id='china-rupture-1', network='china-zhijiang', fault_kind='rupture', link_id='1',
         fault_parameters={}, demand_increase_pct=10,
         decision_wall_clock_s=600, candidate_limit=24, max_actions=4,
         note='Chinese research-model trunk rupture; severe, expected honest-negative.'),
]


def build_task(scenario):
    kwargs = dict(network_id=scenario['network'], fault_kind=scenario['fault_kind'],
        threshold_mode='manual', min_pressure_m=20, min_supply_ratio=0.95,
        demand_increase_pct=scenario['demand_increase_pct'], candidate_limit=scenario['candidate_limit'],
        max_actions=scenario['max_actions'], max_wall_time_s=1200,
        response_latency={'decision_wall_clock_s': scenario['decision_wall_clock_s']},
        emergency_floor=scenario.get('emergency_floor', {}))
    if scenario.get('link_id'):
        kwargs['link_id'] = scenario['link_id']
    if scenario.get('fault_parameters'):
        kwargs['fault_parameters'] = scenario['fault_parameters']
    if scenario.get('override_pump'):
        details = network_details(scenario['network'])
        pump = next(f for f in details['catalog']['facilities'] if f['link_id'] == scenario['override_pump'])
        kwargs['control_overrides'] = [dict(link_id=scenario['override_pump'],
            control_names=pump['native_control_names'],
            reason='Frozen emergency alternate-source scenario; manual operation permitted for all strategies.')]
    task = propose_task(TaskProposal(**kwargs))
    if scenario.get('override_pump'):
        task['action_limits']['allowed_link_ids'].append(scenario['override_pump'])
        task['constraint_hash'] = task_hash(task)
    return task


def per_node_delta(baseline_report, selected_report):
    base = {r['node_id']: r for r in baseline_report['metrics']['node_availability']}
    sel = {r['node_id']: r for r in selected_report['metrics']['node_availability']}
    rows, improved, regressed = [], False, False
    for node_id in sorted(base, key=lambda n: (int(n) if n.isdigit() else 1 << 30, n)):
        b, s = base[node_id], sel[node_id]
        delta = s['normal_supply_duration_s'] - b['normal_supply_duration_s']
        if delta > 0:
            improved = True
        if delta < 0:
            regressed = True
        rows.append(dict(node_id=node_id, assessed_duration_s=b['assessed_duration_s'],
            baseline_normal_s=b['normal_supply_duration_s'], selected_normal_s=s['normal_supply_duration_s'],
            delta_s=delta, baseline_ratio=b['normal_supply_time_ratio'], selected_ratio=s['normal_supply_time_ratio']))
    return rows, improved, regressed


def choose_selected(task, catalog, scene, adapter, baseline_report, output):
    """Rank valid improvements by the frozen objectives within the tested domain."""
    from services.hydraulics.configuration import ranking_key
    from services.verifier.mitigation import mitigation_record
    recommended = best_effort = None
    actions = [p for p in benchmark_plans(task, catalog) if p['actions']]
    # One already evaluated baseline plus one same-plan demand review.
    for plan in actions[:max(0, task['budget']['max_simulations'] - 2)]:
        result = adapter.simulate(task, plan, timeout_s=task['budget']['max_wall_time_s'])
        folder = output / 'sims' / result['artifact']['simulation_id']
        report = verify(task, plan, result, catalog, scene, artifact_dir=folder)
        write(output / 'verification' / (plan['candidate_id'] + '-nominal.json'), report)
        if report['validity'] != 'valid':
            continue
        key = ranking_key(task, report)
        if not report['violations'] and (recommended is None or key < recommended[0]):
            recommended = (key, plan, report)
        rescue = mitigation_record(task, baseline_report, report)
        if rescue and rescue['improves_baseline'] and (best_effort is None or key < best_effort[0]):
            best_effort = (key, plan, report, rescue)
    rec = (recommended[1], recommended[2], True) if recommended else (None, None, False)
    be = dict(plan=best_effort[1], report=best_effort[2], resilience=best_effort[3]) if best_effort else None
    return rec[0], rec[1], rec[2], be


def emergency_rescue_record(baseline_report, best_effort):
    if best_effort is None:
        return None
    plan = best_effort['plan']
    return dict(best_effort['resilience'], actions=plan['actions'], plan_hash=plan['plan_hash'])


def run_scenario(scenario, output):
    out = output / scenario['id']
    out.mkdir(parents=True, exist_ok=True)
    task = build_task(scenario)
    details = network_details(scenario['network'])
    catalog, scene = details['catalog'], details['scene']
    adapter = PhysicalAdapter(out / 'sims')
    write(out / 'task.json', task)

    baseline = next(pl for pl in benchmark_plans(task, catalog) if not pl['actions'])
    base_res = adapter.simulate(task, baseline, timeout_s=task['budget']['max_wall_time_s'])
    base_rep = verify(task, baseline, base_res, catalog, scene,
                      artifact_dir=out / 'sims' / base_res['artifact']['simulation_id'])
    write(out / 'verification' / (baseline['candidate_id'] + '-nominal.json'), base_rep)
    require(base_rep['validity'] == 'valid', 'Baseline simulation is physically invalid')

    plan, report, passed, best_effort = choose_selected(task, catalog, scene, adapter, base_rep, out)
    rescue = emergency_rescue_record(base_rep, best_effort)
    if plan is None and best_effort:
        plan, report = best_effort['plan'], best_effort['report']
    earliest = task['action_limits']['earliest_s']
    record = dict(scenario_id=scenario['id'], network=scenario['network'], fault=task['fault'],
        note=scenario['note'], demand_increase_pct=scenario['demand_increase_pct'],
        response_latency=task['response_latency'], earliest_effective_s=earliest,
        emergency_floor=task.get('emergency_floor'),
        evaluation_start_s=task['time_window']['evaluation_start_s'], fault_start_s=task['fault']['start_s'],
        baseline=dict(shortage_m3=base_rep['metrics']['shortage_m3'], min_pressure_m=base_rep['metrics']['min_pressure_m'],
            mean_node_time_pass_ratio=base_rep['metrics']['mean_node_time_pass_ratio'],
            min_node_time_pass_ratio=base_rep['metrics']['min_node_time_pass_ratio'],
            emergency_mean_node_time_pass_ratio=base_rep['metrics'].get('emergency_mean_node_time_pass_ratio'),
            nodes_reaching_emergency_service=base_rep['metrics'].get('nodes_reaching_emergency_service'),
            assessed_node_count=base_rep['metrics'].get('assessed_node_count'),
            nominal_pass=not base_rep['violations']),
        emergency_rescue=rescue)
    require(all(a['simulation_time_s'] >= earliest for pl in [plan] if pl for a in pl['actions']),
            'Selected action backdates before the real-latency floor') if plan else None

    if plan is None:
        record.update(conclusion='NO_FEASIBLE_FOUND', selected=None,
            interpretation='No tested action passed the full gate or improved node service under frozen objectives.')
        write(out / 'result.json', record)
        return record

    rows, improved, regressed = per_node_delta(base_rep, report)
    # Same-plan demand-x revalidation at the frozen stress multiplier.
    multiplier = stress_multiplier(task)
    perturbation = dict(perturbation_id='rt-stress-' + plan['candidate_id'], candidate_id=plan['candidate_id'],
        parameters={'demand_multiplier': multiplier}, required_labels=task['perturbation_domain']['required'])
    stress_res = adapter.simulate(task, plan, perturbation=perturbation, timeout_s=task['budget']['max_wall_time_s'])
    stress_rep = verify(task, plan, stress_res, catalog, scene, perturbation,
                        artifact_dir=out / 'sims' / stress_res['artifact']['simulation_id'])
    write(out / 'verification' / (plan['candidate_id'] + '-stress.json'), stress_rep)
    stress_pass = stress_rep['validity'] == 'valid' and not stress_rep['violations']

    recovered = passed and stress_pass
    from services.verifier.mitigation import mitigation_record
    delivered = mitigation_record(task, base_rep, report)
    mitigated = bool(delivered and delivered['improves_baseline'])
    if not recovered and mitigated:
        rescue = dict(delivered, actions=plan['actions'], plan_hash=plan['plan_hash'])
    record.update(
        selected=dict(candidate_id=plan['candidate_id'], actions=plan['actions'], plan_hash=plan['plan_hash'],
            shortage_m3=report['metrics']['shortage_m3'], min_pressure_m=report['metrics']['min_pressure_m'],
            mean_node_time_pass_ratio=report['metrics']['mean_node_time_pass_ratio'],
            min_node_time_pass_ratio=report['metrics']['min_node_time_pass_ratio'],
            nominal_pass=passed, violation_count=len(report['violations'])),
        demand_revalidation=dict(multiplier=multiplier, label=task['perturbation_domain']['required'],
            min_pressure_m=stress_rep['metrics']['min_pressure_m'], shortage_m3=stress_rep['metrics']['shortage_m3'],
            mean_node_time_pass_ratio=stress_rep['metrics']['mean_node_time_pass_ratio'],
            pass_=stress_pass, violation_count=len(stress_rep['violations'])),
        per_node=rows, any_node_improved=improved, any_node_regressed=regressed,
        shortage_reduction_m3=base_rep['metrics']['shortage_m3'] - report['metrics']['shortage_m3'],
        conclusion='FEASIBLE_FOUND' if recovered else 'BEST_EFFORT_MITIGATION' if mitigated else 'NO_FEASIBLE_FOUND',
        emergency_rescue=rescue if not recovered else None,
        interpretation=('Honest real-latency dispatch recovered every user node and passed the demand-x revalidation.'
                        if recovered else
                        'Delivered mitigation improves the baseline but still violates frozen constraints or the demand-x stress; not recommended.'))
    if not recovered:
        record['selected'] = None
    record['engineering_feasible'] = recovered
    record['is_recommendation'] = False  # Deterministic experiment has no native AGH provenance.
    write(out / 'result.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/realtime-dispatch')
    parser.add_argument('--emergency-pressure-m', type=float)
    parser.add_argument('--emergency-supply-ratio', type=float)
    parser.add_argument('--only', nargs='+', help='Run only these scenario ids')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    scenarios = [s for s in SCENARIOS if not args.only or s['id'] in args.only]
    require(bool(scenarios), 'No scenarios selected')
    summary = dict(created_at=datetime.now(timezone.utc).isoformat(),
        module_sha256=file_hash(Path(__file__)),
        interpretation='Deterministic real-latency physical proof over the server-enumerated finite domain; '
                       'no AGH autonomy claim and no global-optimality claim. Honest negatives are retained.',
        cases=[])
    if (args.emergency_pressure_m is None) != (args.emergency_supply_ratio is None):
        parser.error('Both emergency thresholds must be supplied together')
    for scenario in scenarios:
        scenario = dict(scenario)
        if args.emergency_pressure_m is not None:
            scenario['emergency_floor'] = dict(min_pressure_m=args.emergency_pressure_m, min_supply_ratio=args.emergency_supply_ratio, source='Explicit experimental engineering assumption')
        print('RUN', scenario['id'], flush=True)
        record = run_scenario(scenario, args.output)
        summary['cases'].append({k: record[k] for k in ('scenario_id', 'network', 'conclusion',
            'earliest_effective_s', 'fault_start_s', 'shortage_reduction_m3', 'any_node_improved',
            'any_node_regressed', 'demand_revalidation') if k in record})
        write(args.output / 'summary.json', summary)
        print(scenario['id'], record['conclusion'], 'earliest_s', record['earliest_effective_s'],
              '| reduction', round(record.get('shortage_reduction_m3', 0), 6), flush=True)
    print(json.dumps(dict(scenarios=len(summary['cases']),
        feasible=[c['scenario_id'] for c in summary['cases'] if c['conclusion'] == 'FEASIBLE_FOUND'],
        mitigation=[c['scenario_id'] for c in summary['cases'] if c['conclusion'] == 'BEST_EFFORT_MITIGATION'],
        no_improvement=[c['scenario_id'] for c in summary['cases'] if c['conclusion'] == 'NO_FEASIBLE_FOUND']),
        ensure_ascii=False))


if __name__ == '__main__':
    main()
