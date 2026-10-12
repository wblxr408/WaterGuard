import json, sys
from pathlib import Path
sys.path.insert(0, str(Path('experiments/scenario-analysis').resolve()))
import topo_probe as T

datas = {net: T.summarize_net(net) for net in T.NET_FILES}

print('\n\n########## SCENARIO PROBES ##########')

# epa-net1
d1 = datas['epa-net1']
T.report_scenario(d1, 'epa-net1', 'N1-LEAK-12', '12', 'leak')
T.report_scenario(d1, 'epa-net1', 'N1-RUPTURE-110(tankfeed)', '110', 'rupture')
T.report_scenario(d1, 'epa-net1', 'N1-ISOLATION-31', '31', 'isolation', boundary=['121','122'])
T.report_scenario(d1, 'epa-net1', 'N1-LEAK-21', '21', 'leak')

# epa-net3
d3 = datas['epa-net3']
T.report_scenario(d3, 'epa-net3', 'N3-LEAK-123(trunk)', '123', 'leak')
T.report_scenario(d3, 'epa-net3', 'N3-RUPTURE-173(trunk)', '173', 'rupture')
# isolation target 123 => boundary auto-cut
b = T.isolation_boundary('epa-net3', d3['links'], '123')
print('\n  [auto boundary for epa-net3 target 123]:', b)
T.report_scenario(d3, 'epa-net3', 'N3-ISOLATION-123', '123', 'isolation', boundary=b)
# isolation of a singly-fed node's feeder: node 253 fed by pipe 291 (255-253)
# find the single pipe touching 253
feeders_253 = [lid for lid,l in d3['links'].items() if '253' in (l['start'],l['end'])]
print('  pipes touching node 253:', feeders_253)
T.report_scenario(d3, 'epa-net3', 'N3-LEAK-291(spur to 253)', '291', 'leak')

# china-zhijiang
dz = datas['china-zhijiang']
# source feed pipe: 184 (110-114 reservoir)
feeders_src = [lid for lid,l in dz['links'].items() if '114' in (l['start'],l['end'])]
print('\n  pipes touching reservoir 114:', feeders_src)
T.report_scenario(dz, 'china-zhijiang', 'Z-LEAK-1', '1', 'leak')
T.report_scenario(dz, 'china-zhijiang', 'Z-RUPTURE-184(SOURCE FEED)', '184', 'rupture')
bz = T.isolation_boundary('china-zhijiang', dz['links'], '13')
print('\n  [auto boundary for china target 13]:', bz)
T.report_scenario(dz, 'china-zhijiang', 'Z-ISOLATION-13', '13', 'isolation', boundary=bz)
# singly-fed node 16: feeder pipe
feeders_16 = [lid for lid,l in dz['links'].items() if '16' in (l['start'],l['end'])]
print('  pipes touching node 16:', feeders_16)
# isolation of node 16 => boundary is its single feeder
bz16 = T.isolation_boundary('china-zhijiang', dz['links'], feeders_16[0]) if feeders_16 else []
T.report_scenario(dz, 'china-zhijiang', 'Z-RUPTURE-184 repeat check', '184', 'rupture')
