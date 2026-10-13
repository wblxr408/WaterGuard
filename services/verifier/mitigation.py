"""Resilience comparisons over independently verified, same-window reports."""
from services.hydraulics.configuration import ranking_key


def mitigation_record(task, baseline, report):
    if baseline['validity'] != 'valid' or report['validity'] != 'valid':
        return None
    if baseline['task_ref'] != report['task_ref'] or baseline['coverage'] != report['coverage']:
        return None
    base = {r['node_id']: r for r in baseline['metrics'].get('node_availability', [])}
    current = {r['node_id']: r for r in report['metrics'].get('node_availability', [])}
    if not base or base.keys() != current.keys():
        return None
    rows = []
    for node, b in base.items():
        s = current[node]
        if b['assessed_duration_s'] <= 0 or b['assessed_duration_s'] != s['assessed_duration_s']:
            return None
        eb, es = b.get('emergency_supply_duration_s'), s.get('emergency_supply_duration_s')
        rows.append(dict(node_id=node, assessed_duration_s=b['assessed_duration_s'],
            baseline_normal_s=b['normal_supply_duration_s'], selected_normal_s=s['normal_supply_duration_s'],
            delta_s=s['normal_supply_duration_s'] - b['normal_supply_duration_s'],
            baseline_ratio=b['normal_supply_time_ratio'], selected_ratio=s['normal_supply_time_ratio'],
            baseline_emergency_s=eb, selected_emergency_s=es,
            emergency_delta_s=None if eb is None or es is None else es-eb))
    improved = any(r['delta_s'] > 0 for r in rows)
    emergency_improved = any((r['emergency_delta_s'] or 0) > 0 for r in rows)
    emergency_regressed = any((r['emergency_delta_s'] or 0) < 0 for r in rows)
    metrics = report['metrics']
    return dict(candidate_id=report['candidate_id'], baseline_candidate_id=baseline['candidate_id'],
        improves_baseline=(improved or emergency_improved) and ranking_key(task, report)[:-1] < ranking_key(task, baseline)[:-1],
        is_recommendation=False, objective_order=task['objectives'], per_node=rows,
        any_node_improved=improved, any_node_regressed=any(r['delta_s'] < 0 for r in rows),
        any_emergency_improved=emergency_improved, any_emergency_regressed=emergency_regressed,
        fully_compliant_node_count=metrics.get('fully_compliant_node_count'),
        assessed_node_count=metrics.get('assessed_node_count'),
        min_node_time_pass_ratio=metrics.get('min_node_time_pass_ratio'),
        mean_node_time_pass_ratio=metrics.get('mean_node_time_pass_ratio'),
        emergency_floor=task.get('emergency_floor'),
        nodes_reaching_emergency_service=metrics.get('nodes_reaching_emergency_service'),
        emergency_mean_node_time_pass_ratio=metrics.get('emergency_mean_node_time_pass_ratio'),
        emergency_min_node_time_pass_ratio=metrics.get('emergency_min_node_time_pass_ratio'),
        hard_violation_count=len(report['violations']),
        shortage_reduction_m3=baseline['metrics']['shortage_m3'] - metrics['shortage_m3'],
        scope='Best tested candidate under frozen objectives; no global optimality or infeasibility proof.')
