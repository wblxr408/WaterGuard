"""Observed facility effects derived from original flows; never a predicted benefit."""
from __future__ import annotations

from collections import defaultdict, deque

IDLE_FLOW = 1e-9


def _sort_key(link_id):
    return (0, int(link_id), '') if link_id.isdigit() else (1, 0, link_id)


def facility_effects(links, mean_flows, failed_demand, fault_nodes, facility_ids):
    """Describe where each facility's observed flow goes during the failure window.

    `links` maps link id to (start node, end node); `mean_flows` is the signed time-mean
    flow per link (positive start to end); `failed_demand` maps failed node to its expected
    demand; `fault_nodes` are the fault pipe's endpoints. Idle links carry no edge."""
    unknown = sorted(set(facility_ids) - set(links))
    if unknown:
        raise ValueError('Unknown facility link: ' + ', '.join(unknown))
    edges = []
    for link_id in sorted(links, key=_sort_key):
        start, end = links[link_id]
        flow = mean_flows.get(link_id, 0.0)
        if flow > IDLE_FLOW:
            edges.append((link_id, start, end))
        elif flow < -IDLE_FLOW:
            edges.append((link_id, end, start))
    total = sum(failed_demand.values())
    rows = []
    for link_id in sorted(set(facility_ids), key=_sort_key):
        start, end = links[link_id]
        flow = mean_flows.get(link_id, 0.0)
        if abs(flow) <= IDLE_FLOW:
            rows.append(dict(link_id=link_id, direction='idle', mean_flow=flow, downstream_failed_nodes=[],
                             downstream_failed_demand_share=0.0, feeds_fault=False, basis=_BASIS))
            continue
        head = end if flow > 0 else start
        adjacency = defaultdict(list)
        for edge_id, source, target in edges:
            if edge_id != link_id:
                adjacency[source].append(target)
        reached, pending = {head}, deque([head])
        while pending:
            for neighbour in adjacency[pending.popleft()]:
                if neighbour not in reached:
                    reached.add(neighbour)
                    pending.append(neighbour)
        failed = sorted(node for node in reached if node in failed_demand)
        share = sum(failed_demand[node] for node in failed) / total if total > 0 else 0.0
        rows.append(dict(link_id=link_id, direction='start_to_end' if flow > 0 else 'end_to_start', mean_flow=flow,
                         downstream_failed_nodes=failed, downstream_failed_demand_share=share,
                         feeds_fault=any(node in reached for node in fault_nodes), basis=_BASIS))
    rows.sort(key=lambda r: (r['direction'] == 'idle', -r['downstream_failed_demand_share'], not r['feeds_fault'],
                             -abs(r['mean_flow']), _sort_key(r['link_id'])))
    return rows


_BASIS = ('observed from original link flows in the failure window; downstream means water reaches those nodes through '
          'the observed flow graph. Closing a facility that supplies failed nodes can worsen them; this is not a predicted benefit.')

ACTIVE_LIMIT = 10


def observed_failure_effects(task, report, folder, scene, catalog, permitted_ids):
    """Read the original flow and demand series of an already verified failure and describe facility effects.

    Returns the most consequential carrying facilities plus every idle standby source (closed
    at the start), because a standby source is the usual way to add supply."""
    import pandas as pd
    window = task['time_window']
    flows = pd.read_csv(folder / 'raw-link-flowrate.csv', index_col='time_s', float_precision='round_trip')
    demand = pd.read_csv(folder / 'expected-demand.csv', index_col='time_s', float_precision='round_trip')
    inside = [t for t in flows.index if window['evaluation_start_s'] <= t < window['end_s']]
    if not inside:
        raise ValueError('No flow samples inside the evaluation window')
    mean_flows = {str(link): float(flows.loc[inside, link].mean()) for link in flows.columns}
    failed_nodes = sorted({v['node_id'] for v in report['violations']})
    expected = {str(n): float(demand.loc[[t for t in demand.index if window['evaluation_start_s'] <= t < window['end_s']], n].mean())
                for n in failed_nodes if n in demand.columns}
    links = {l['id']: (l['from_node'], l['to_node']) for l in scene['links']}
    fault = task.get('fault') or {}
    fault_nodes = links.get(fault.get('link_id'), ())
    rows = facility_effects(links, mean_flows, expected, fault_nodes, sorted(set(permitted_ids) & set(links)))
    standby = {f['link_id'] for f in catalog['facilities'] if f['initial_status'] == 'closed'}
    active = [r for r in rows if r['direction'] != 'idle'][:ACTIVE_LIMIT]
    idle = [r for r in rows if r['direction'] == 'idle' and r['link_id'] in standby]
    return active + idle
