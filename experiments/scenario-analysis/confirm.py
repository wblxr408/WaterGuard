import json, sys
from collections import deque, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path('experiments/scenario-analysis').resolve()))
import topo_probe as T

datas = {net: dict(zip(('junctions','reservoirs','tanks','links'), T.parse_inp(T.NET_FILES[net]))) for net in T.NET_FILES}
cats = {net: json.loads(T.CATALOGS[net].read_text(encoding='utf-8')) for net in T.NET_FILES}

def perm_ids(net):
    return {f['link_id'] for f in cats[net]['facilities'] if not f['requires_explicit_override']}

def connected_without(net, drop_links):
    links = datas[net]['links']
    g = defaultdict(list)
    for lid,l in links.items():
        if lid in drop_links: continue
        g[l['start']].append(l['end']); g[l['end']].append(l['start'])
    sources = datas[net]['reservoirs'] | datas[net]['tanks']
    seen=set(sources); q=deque(sources)
    while q:
        n=q.popleft()
        for m in g[n]:
            if m not in seen: seen.add(m); q.append(m)
    demand_nodes={n for n,d in datas[net]['junctions'].items() if d>0}
    stranded=sorted(demand_nodes - seen, key=lambda n:-datas[net]['junctions'][n])
    return stranded

# (a) china 184 sole source
links_z = datas['china-zhijiang']['links']
res_links=[lid for lid,l in links_z.items() if '114' in (l['start'],l['end'])]
print('CHINA reservoir-114 links:', res_links)
print('CHINA strand if rupture/isolate 184:',
      len(connected_without('china-zhijiang', {'184'})), 'demand nodes lose source (total demand',
      round(sum(datas['china-zhijiang']['junctions'][n] for n in connected_without('china-zhijiang',{'184'})),1), ')')

# (c) net1 isolation 31 boundary 121,122
print('NET1 strand if isolate boundary {121,122}:',
      connected_without('epa-net1', {'121','122'}))
# net3 isolation 123 auto boundary
b123 = T.isolation_boundary('epa-net3', datas['epa-net3']['links'], '123')
print('NET3 isolate 123 boundary', b123, '-> strand',
      connected_without('epa-net3', set(b123)))

# verify mitigator candidates are permitted (no override) for each scenario
def check_perm(net, ids):
    p=perm_ids(net)
    return {i:(i in p) for i in ids}
print('\nMitigator permission checks:')
print(' N1 leak12 cand[11,112,113,110]:', check_perm('epa-net1',['11','112','113','110']))
print(' N1 rupt21 cand[111,112,121,122,22]:', check_perm('epa-net1',['111','112','121','122','22']))
print(' N3 leak123 cand[120,171,173,329,131]:', check_perm('epa-net3',['120','171','173','329','131']))
print(' N3 rupt173 cand[171,329,175] + pump10(override):', check_perm('epa-net3',['171','329','175','10']))
print(' CHINA leak7 cand[9,10,34,6,11]:', check_perm('china-zhijiang',['9','10','34','6','11']))

# fault link validity: pipe + initially open
def fault_ok(net, lid):
    l=datas[net]['links'].get(lid)
    return l and l['kind']=='pipe' and l['status']!='closed'
for net,lid in [('epa-net1','12'),('epa-net1','21'),('epa-net3','123'),('epa-net3','173'),
                ('china-zhijiang','7'),('china-zhijiang','184')]:
    print(f' fault-link-ok {net}/{lid}:', fault_ok(net,lid))

# china nominal demand multiplier
print('\nCHINA INP demand_multiplier (nominal):', [l for l in T.NET_FILES['china-zhijiang'].read_text().splitlines() if 'Demand Multiplier' in l])
print('NET1 demand_multiplier:', [l for l in T.NET_FILES['epa-net1'].read_text().splitlines() if 'Demand Multiplier' in l])
