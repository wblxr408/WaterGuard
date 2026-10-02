"""Explicit fixture authoring tool. Values below are authored examples, not hydraulics."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waterguard_contracts.core import (FIXTURES, VERSION, file_hash, input_hash,
                                      plan_hash, task_hash, task_ref, verify_fixture)

FIXTURES.mkdir(parents=True, exist_ok=True)


def write(name, value):
    (FIXTURES / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')


network_id = 'wg-fixture-loop-v1'
units = dict(length='m', pressure_head='m', flow='m3/s', time='s')
nodes = [
    dict(id='R1', kind='reservoir', x_m=0, y_m=0, elevation_m=50, critical=False),
    dict(id='J1', kind='junction', x_m=100, y_m=0, elevation_m=0, critical=False),
    dict(id='J2', kind='junction', x_m=200, y_m=0, elevation_m=0, critical=True),
    dict(id='J3', kind='junction', x_m=200, y_m=100, elevation_m=0, critical=False),
]
links = [dict(id=id, from_node=start, to_node=end, kind='pipe', initial_status=status) for id, start, end, status in [
    ('P_SUPPLY', 'R1', 'J1', 'open'), ('P_MAIN', 'J1', 'J2', 'open'),
    ('P_BYPASS', 'J1', 'J3', 'closed'), ('P_BRANCH', 'J2', 'J3', 'open'),
]]
scene = dict(schema_version=VERSION, network_id=network_id, mode='fixture', coordinate_system='local_cartesian_m', provenance='synthetic', nodes=nodes, links=links)
write('scene.json', scene)
(FIXTURES / 'network.inp').write_text('''[TITLE]
WaterGuard authored debug topology. Fixture series are NOT calculated from this INP.
[JUNCTIONS]
;ID Elevation Demand(LPS)
J1 0 2
J2 0 3
J3 0 1
[RESERVOIRS]
R1 50
[PIPES]
;ID Node1 Node2 Length Diameter(mm) Roughness MinorLoss Status
P_SUPPLY R1 J1 100 200 120 0 OPEN
P_MAIN J1 J2 100 150 120 0 OPEN
P_BYPASS J1 J3 200 150 120 0 CLOSED
P_BRANCH J2 J3 100 150 120 0 OPEN
[OPTIONS]
UNITS LPS
HEADLOSS H-W
[TIMES]
DURATION 3:00
HYDRAULIC TIMESTEP 0:30
REPORT TIMESTEP 0:30
[COORDINATES]
R1 0 0
J1 100 0
J2 200 0
J3 200 100
[END]
''', encoding='utf-8', newline='\n')
overlay = dict(provenance='synthetic', critical_nodes=['J2'], description='J2 is an assumed critical user, not a measured hospital. Local coordinates are illustrative; no CRS or buried depth is asserted.')
write('overlay.json', overlay)
manifest = dict(schema_version=VERSION, network_id=network_id, revision=1, sha256=file_hash(FIXTURES/'network.inp'),
                source_url='repo:packages/contracts/fixtures/day1/network.inp', source_kind='synthetic', license='LicenseRef-WaterGuard-Internal-Fixture',
                units=units, coordinate_system='local_cartesian_m',
                overlays=[dict(ref='overlay.json', sha256=file_hash(FIXTURES/'overlay.json'), provenance='synthetic', description=overlay['description'])],
                original_file='network.inp', description='Project-authored debug topology. Native INP uses LPS and mm diameter; contract output uses SI. No public-network or hydraulic validation claim.')
write('network-manifest.json', manifest)
catalog = dict(schema_version=VERSION, catalog_id='catalog-day1-v1', network_id=network_id, facilities=[
    dict(link_id='P_BYPASS', kind='pipe', allowed_actions=['link_open','link_close'], initial_status='closed', action_cost=1, mapping='Set P_BYPASS link status at simulation_time_s; no pump or valve-layer inference.')])
write('action-catalog.json', catalog)
window = dict(start_s=0, end_s=10800, step_s=1800, warmup_end_s=3600, evaluation_start_s=3600)
task = dict(schema_version=VERSION, task_id='task-day1-closure', revision=1,
            network_ref=dict(network_id=network_id, revision=1, sha256=manifest['sha256'], overlay_hash=manifest['overlays'][0]['sha256']),
            fault=dict(kind='closure', link_id='P_MAIN', start_s=3600, end_s=10800, mapping='Force P_MAIN closed during [3600,10800); no leak emitter, rupture or isolation-zone model.'),
            time_window=window, objectives=['minimize_shortage','minimize_action_cost'],
            hard_constraints=dict(min_pressure_m=20, min_supply_ratio=0.95, node_ids=['J1','J2','J3'], pressure_tolerance_m=0.000001, ratio_tolerance=0.000000001, source='Synthetic day-1 development assumptions; not regulatory or measured limits.'),
            soft_preferences=[], action_catalog_ref='action-catalog.json', action_catalog_hash=file_hash(FIXTURES/'action-catalog.json'),
            action_limits=dict(max_actions=2, allowed_link_ids=['P_BYPASS'], allowed_kinds=['link_open','link_close'], earliest_s=3600, latest_s=9000, min_switch_interval_s=1800),
            perturbation_domain=dict(required=['demand-plus-10pct'], demand_multipliers=[1,1.1]),
            assumptions=['All samples are authored fixture values, not WNTR/EPANET output.', 'Pressure head m; flow m3/s; simulation time s.', 'J2 critical status is a synthetic overlay.', 'No hydraulic convergence or AGH execution is claimed.'],
            budget=dict(max_simulations=40,max_parallel=2,max_wall_time_s=600,max_revisions=4))
task['constraint_hash'] = task_hash(task)
write('task.json',task)
plans = []
for suffix, actions in [('a',[]),('b',[dict(kind='link_open',link_id='P_BYPASS',simulation_time_s=3600)])]:
    plan = dict(schema_version=VERSION, candidate_id='candidate-'+suffix, task_ref=task_ref(task), parent_candidate_id=None if suffix=='a' else 'candidate-a',
                actions=actions, source='fixture', reason='Authored fault baseline with no action.' if suffix=='a' else 'Authored example: open bypass after A fails; not an Agnes decision.')
    plan['plan_hash'] = plan_hash(plan)
    write('candidate-'+suffix+'.json',plan)
    plans.append(plan)
    frames=[]
    for t in range(0,10800,1800):
        fault = t>=3600
        improved = fault and suffix=='b'
        pressures = [35,0,0] if fault and not improved else ([35,26,24] if improved else [35,30,28])
        demand = [0.002,0.003,0.001]
        supply = [0.002,0,0] if fault and not improved else demand
        flows = [0.002,0,0,0] if fault and not improved else ([0.006,0,0.004,-0.003] if improved else [0.006,0.004,0,0.001])
        frames.append(dict(time_s=t,duration_s=1800,
            nodes=[dict(node_id=node,pressure_m=p,expected_demand_m3s=d,delivered_demand_m3s=q,leak_flow_m3s=0) for node,p,d,q in zip(['J1','J2','J3'],pressures,demand,supply)],
            links=[dict(link_id=l['id'],flow_m3s=q,status=('closed' if fault else 'open') if l['id']=='P_MAIN' else ('open' if improved else 'closed') if l['id']=='P_BYPASS' else 'open') for l,q in zip(links,flows)]))
    series=dict(schema_version=VERSION,simulation_id='simulation-'+suffix,mode='fixture',units=units,sampling='interval_start_hold',frames=frames)
    diag=dict(schema_version=VERSION,simulation_id='simulation-'+suffix,mode='fixture',converged=None,errors=[],warnings=['Synthetic fixture: no hydraulic engine was run.'],provenance='Hand-authored deterministic development samples; converged=null means unmeasured.')
    artifact=dict(schema_version=VERSION,simulation_id='simulation-'+suffix,candidate_id=plan['candidate_id'],task_ref=task_ref(task),perturbation_id=None,mode='fixture',engine_version='fixture-author/0.1.0',input_hash=input_hash(task,plan,'fixture-author/0.1.0'),status='SUCCEEDED',series_ref='series-'+suffix+'.json',diagnostics_ref='diagnostics-'+suffix+'.json',time_axis=window)
    result=dict(schema_version=VERSION,mode='fixture',artifact=artifact,series=series,diagnostics=diag)
    write('series-'+suffix+'.json',series)
    write('diagnostics-'+suffix+'.json',diag)
    write('result-'+suffix+'.json',result)
    write('verification-'+suffix+'.json',verify_fixture(task,plan,result,catalog,scene))
run=dict(schema_version=VERSION,run_id='run-day1-fixture',task_ref=task_ref(task),mode='fixture',status='COMPLETED',phase='PACKAGING',agh_session_ref=None,model_config_ref=None,budget_usage=dict(simulations=0,model_calls=None,wall_time_s=0),conclusion='INCOMPLETE',selected_candidate_id=None,missing_items=['native_agh_trace','real_hydraulic_results','demand-plus-10pct'])
write('run.json',run)
initial_run={**run,'status':'RUNNING','phase':'BASELINE'}
write('run-initial.json',initial_run)
events=[]
steps=[('run.started',None,'run-initial.json','user')]
for suffix in ('a','b'):
    steps.extend([('candidate.submitted',suffix,f'candidate-{suffix}.json','gateway'),('simulation.completed',suffix,f'result-{suffix}.json','worker'),('verification.completed',suffix,f'verification-{suffix}.json','verifier')])
steps.append(('run.completed',None,'run.json','gateway'))
for sequence,(event_type,suffix,payload,origin) in enumerate(steps,1):
    events.append(dict(schema_version=VERSION,event_id=f'evt-fixture-{sequence:03}',sequence=sequence,run_id=run['run_id'],task_revision=1,constraint_hash=task['constraint_hash'],mode='fixture',agh_session_ref=None,native_tool_call_ref=None,native_reference_missing_reason='Fixture has no native AGH session or tool call.',correlation_id=f'corr-fixture-{suffix or "run"}',origin=origin,type=event_type,candidate_id=None if suffix is None else 'candidate-'+suffix,simulation_id='simulation-'+suffix if event_type in ('simulation.completed','verification.completed') else None,occurred_at=f'2026-10-02T08:00:{sequence:02}Z',simulation_time_s=None,payload_ref=payload))
(FIXTURES/'events.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False,separators=(',',':'))+'\n' for e in events),encoding='utf-8',newline='\n')
artifacts=[dict(ref=p.name,sha256=file_hash(p),kind=p.suffix.lstrip('.')) for p in sorted(FIXTURES.iterdir()) if p.is_file() and p.name!='evidence-manifest.json']
write('evidence-manifest.json',dict(schema_version=VERSION,bundle_id='bundle-day1-fixture',run_id=run['run_id'],mode='fixture',native_trace_ref=None,association_ref=None,artifacts=artifacts,hashes={a['ref']:a['sha256'] for a in artifacts},redactions=[],missing_items=run['missing_items'],live_export_allowed=False))
print('Authored day-1 fixture bundle. No AGH or hydraulic engine was invoked.')
