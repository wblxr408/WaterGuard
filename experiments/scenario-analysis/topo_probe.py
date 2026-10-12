"""Topology probe for scenario-feasibility matrix (read-only, no simulation).

Parses the pinned INP files and the frozen v3 catalogs to reproduce exactly the
distance/priority logic used by services/api/workbench.benchmark_plans and
services/api/planning.summarize_failure, so proposed mitigating facilities are
evidence-based. wntr is not installed in this environment; we parse INP directly.
"""
from __future__ import annotations
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NET_FILES = {
    'epa-net1': ROOT / 'data/networks/wntr-1.5.0/Net1.inp',
    'epa-net3': ROOT / 'data/networks/wntr-1.5.0/Net3.inp',
    'china-zhijiang': ROOT / 'data/networks/zhijiang/ZhiJiang.inp',
}
CATALOGS = {n: ROOT / f'data/catalogs/{n}-v3.json' for n in NET_FILES}
CONFIG = json.loads((ROOT / 'config/dispatch-defaults.json').read_text(encoding='utf-8'))


def parse_inp(path: Path):
    """Return nodes-by-type, links (id -> dict), and demand map from an EPANET INP."""
    section = None
    junctions, reservoirs, tanks = {}, set(), set()
    links = {}  # id -> dict(start,end,kind,status)
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split(';', 1)[0].strip()
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].upper()
            continue
        parts = line.split()
        if section == 'JUNCTIONS' and len(parts) >= 2:
            demand = float(parts[2]) if len(parts) >= 3 else 0.0
            junctions[parts[0]] = demand
        elif section == 'RESERVOIRS' and parts:
            reservoirs.add(parts[0])
        elif section == 'TANKS' and parts:
            tanks.add(parts[0])
        elif section == 'PIPES' and len(parts) >= 4:
            status = parts[7] if len(parts) >= 8 else 'Open'
            links[parts[0]] = dict(start=parts[1], end=parts[2], kind='pipe', status=status.lower())
        elif section == 'PUMPS' and len(parts) >= 3:
            links[parts[0]] = dict(start=parts[1], end=parts[2], kind='pump', status='open')
        elif section == 'VALVES' and len(parts) >= 3:
            links[parts[0]] = dict(start=parts[1], end=parts[2], kind='valve', status='open')
    return junctions, reservoirs, tanks, links


def adjacency(links):
    adj = defaultdict(list)
    for lid, l in links.items():
        adj[l['start']].append(l['end'])
        adj[l['end']].append(l['start'])
    return adj


def bfs_distance(adj, seeds):
    dist = {s: 0 for s in seeds}
    q = deque(seeds)
    while q:
        n = q.popleft()
        for m in adj[n]:
            if m not in dist:
                dist[m] = dist[n] + 1
                q.append(m)
    return dist


def permitted_facilities(catalog):
    """Mirror propose_task: allowed = facilities not requiring an explicit override."""
    return {f['link_id']: f for f in catalog['facilities'] if not f['requires_explicit_override']}


def isolation_boundary(net, links, fault_link):
    """Mirror scenarios.case isolation boundary for non-net1 networks."""
    tgt = links[fault_link]
    nodes = {tgt['start'], tgt['end']}
    return sorted(lid for lid, l in links.items()
                  if (l['start'] in nodes) != (l['end'] in nodes))


def rank_candidates(net, fault_link, boundary_links, links, adj, catalog, kinds=('pump', 'pipe')):
    """Distance-ranked permitted facilities near the fault (benchmark_plans.priority)."""
    perm = permitted_facilities(catalog)
    excluded = {fault_link, *boundary_links}
    seeds = []
    for lid in ([fault_link] if fault_link in links else []) + list(boundary_links):
        seeds += [links[lid]['start'], links[lid]['end']]
    dist = bfs_distance(adj, set(seeds))
    rows = []
    for lid, fac in perm.items():
        if lid in excluded:
            continue
        l = links.get(lid)
        if l is None:
            continue
        d = min(dist.get(l['start'], 10**9), dist.get(l['end'], 10**9))
        rows.append((d, fac['kind'], lid, l['start'], l['end'], fac['initial_status']))
    rows.sort(key=lambda r: (0 if r[1] == 'pump' and r[5] == 'closed' else 1, r[0], r[2]))
    return rows, dist


def summarize_net(net):
    junctions, reservoirs, tanks, links = parse_inp(NET_FILES[net])
    catalog = json.loads(CATALOGS[net].read_text(encoding='utf-8'))
    perm = permitted_facilities(catalog)
    adj = adjacency(links)
    pipes_open = [lid for lid, l in links.items() if l['kind'] == 'pipe' and l['status'] != 'closed']
    pumps = [lid for lid, l in links.items() if l['kind'] == 'pump']
    print(f'\n==== {net} ====')
    print(f'junctions={len(junctions)} reservoirs={sorted(reservoirs)} tanks={sorted(tanks)} '
          f'pipes={sum(1 for l in links.values() if l["kind"]=="pipe")} pumps={pumps}')
    print(f'catalog facilities={len(catalog["facilities"])} permitted(no-override)={len(perm)}')
    override_only = [f['link_id'] for f in catalog['facilities'] if f['requires_explicit_override']]
    print(f'override-only facilities={override_only}')
    cfg = CONFIG['scenario_links'][net]
    print(f'config fault_link_id={cfg.get("fault_link_id")} pump_cycle_id={cfg.get("pump_cycle_id")}')
    # degree-1 demand junctions (single-fed; isolation of their only pipe strands them)
    deg = defaultdict(int)
    for l in links.values():
        deg[l['start']] += 1
        deg[l['end']] += 1
    singly_fed = sorted((n for n in junctions if deg[n] == 1 and junctions[n] > 0),
                        key=lambda n: -junctions[n])
    print(f'singly-fed demand junctions (deg==1, demand>0): {len(singly_fed)} '
          f'top={[(n, round(junctions[n],1)) for n in singly_fed[:8]]}')
    return dict(junctions=junctions, reservoirs=reservoirs, tanks=tanks, links=links,
                catalog=catalog, perm=perm, adj=adj, pipes_open=pipes_open, pumps=pumps)


def report_scenario(data, net, label, fault_link, kind, boundary=None):
    links, adj, catalog, junctions = data['links'], data['adj'], data['catalog'], data['junctions']
    boundary = boundary or []
    print(f'\n--- {label} [{net}] {kind} on link {fault_link}'
          + (f' boundary={boundary}' if boundary else '') + ' ---')
    if fault_link in links:
        l = links[fault_link]
        print(f'  fault pipe endpoints: {l["start"]}(demand {junctions.get(l["start"],0)}) '
              f'-> {l["end"]}(demand {junctions.get(l["end"],0)}) status={l["status"]} kind={l["kind"]}')
    rows, dist = rank_candidates(net, fault_link, boundary, links, adj, catalog)
    top = rows[:10]
    print('  top permitted mitigators (dist,kind,id,from,to,init):')
    for r in top:
        print(f'    d={r[0]} {r[1]} id={r[2]} ({r[3]}-{r[4]}) init={r[5]}')
    # isolation strand check
    if kind == 'isolation':
        import itertools
        g = defaultdict(list)
        for lid, ln in links.items():
            if lid in boundary:
                continue
            g[ln['start']].append(ln['end'])
            g[ln['end']].append(ln['start'])
        # component of fault start without boundary
        seen = {links[fault_link]['start']}
        q = deque(seen)
        while q:
            n = q.popleft()
            for m in g[n]:
                if m not in seen:
                    seen.add(m)
                    q.append(m)
        src = seen & (data['reservoirs'] | data['tanks'])
        isolated_demand = sorted((n for n in seen if junctions.get(n, 0) > 0), key=lambda n: -junctions[n])
        print(f'  isolated component size={len(seen)} contains_source={bool(src)} '
              f'isolated_demand_nodes={[(n, junctions[n]) for n in isolated_demand[:6]]}')
        print(f'  -> isolated demand total={round(sum(junctions[n] for n in isolated_demand),2)} '
              f'(these nodes are UNHELPABLE by any permitted facility)')
    return rows


if __name__ == '__main__':
    datas = {net: summarize_net(net) for net in NET_FILES}
    # exercised below by caller-specified scenarios
    globals()['DATAS'] = datas
