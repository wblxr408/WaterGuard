"""Validate the worker protocol independently of the worker's success claim."""
import json
import math
import warnings
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
from scipy.optimize import OptimizeWarning
from .datasets import ROOT
from waterguard_contracts.core import digest, file_hash, input_hash, load, plan_hash, require, task_hash, task_ref, validate


def equivalent(actual,expected,path):
    """CSV round trips retain doubles; tolerate only documented solver roundoff."""
    if isinstance(expected,float):
        require(isinstance(actual,(int,float)) and not isinstance(actual,bool) and
            math.isfinite(actual) and math.isclose(actual,expected,abs_tol=1e-10,rel_tol=1e-12),'Raw/sidecar mismatch: '+path)
    elif isinstance(expected,dict):
        require(isinstance(actual,dict) and actual.keys()==expected.keys(),'Sidecar fields mismatch: '+path)
        for key,value in expected.items():equivalent(actual[key],value,path+'/'+key)
    elif isinstance(expected,list):
        require(isinstance(actual,list) and len(actual)==len(expected),'Sidecar coverage mismatch: '+path)
        for i,(a,b) in enumerate(zip(actual,expected)):equivalent(a,b,path+'/'+str(i))
    else:require(type(actual)==type(expected) and actual==expected,'Raw/sidecar mismatch: '+path)


def validate_boundaries(wn,raw,engine,times):
    """Check fixed heads and WNTR's disconnected-junction zero sentinel."""
    links=list(wn.links());sources=set(wn.reservoir_name_list+wn.tank_name_list)
    incident={name:[] for name in wn.node_name_list}
    for name,link in links:
        incident[link.start_node_name].append(name);incident[link.end_node_name].append(name)
    reachable_cache={}
    for t in times:
        statuses=tuple(float(raw.link['status'].at[t,name]) for name,_ in links)
        require(all(s in (0,1,2) for s in statuses),'Invalid raw link status')
        if statuses not in reachable_cache:
            adjacency={name:[] for name in wn.node_name_list}
            for (_,link),status in zip(links,statuses):
                if status!=0:
                    adjacency[link.start_node_name].append(link.end_node_name)
                    adjacency[link.end_node_name].append(link.start_node_name)
            reachable=set(sources);pending=list(sources)
            while pending:
                for name in adjacency[pending.pop()]:
                    if name not in reachable:reachable.add(name);pending.append(name)
            reachable_cache[statuses]=reachable
        for name,node in wn.junctions():
            if engine=='wntr' and name not in reachable_cache[statuses]:
                # WNTR 1.5 store_results_in_network/save_results explicitly
                # zero head, pressure, supply and leaks for disconnected nodes.
                # Only topology-proved isolation can use this convention.
                require(all(abs(float(raw.node[field].at[t,name]))<=1e-9 for field in ('head','pressure','demand','leak_demand')) and
                    all(abs(float(raw.link['flowrate'].at[t,link]))<=1e-9 for link in incident[name]),'Invalid isolated junction sentinel: '+name)
            else:
                error=float(raw.node['head'].at[t,name])-float(raw.node['pressure'].at[t,name])-node.elevation
                require(abs(error)<=1e-4,'Raw head/pressure/elevation mismatch: '+name)
        for name,node in wn.reservoirs():
            require(math.isclose(float(raw.node['head'].at[t,name]),float(node.head_timeseries.at(t)),abs_tol=1e-4,rel_tol=1e-7),'Raw source head boundary mismatch: '+name)


def validate_raw(folder,task,plan,series):
    from .compiler import compile_task
    from .physical import assess,engineering_summary
    wn,source,_,_,mapping=compile_task(task,plan)
    from .datasets import SOURCES
    require(load(folder/'network-manifest.json')==source and file_hash(folder/'LICENSE-WNTR.md')==SOURCES['LICENSE.md'][1],'Frozen source/license manifest mismatch')
    detailed=task['schema_version']=='0.4.0';engine=task['hydraulic_options']['engine']
    if engine=='epanet':
        require(all((folder/name).is_file() and (folder/name).stat().st_size>0 for name in ('engine.inp','engine.rpt','engine.bin')),'Missing native EPANET evidence')
        require('WARNING' not in (folder/'engine.rpt').read_text(errors='replace').upper(),'Native EPANET report warning')
    if detailed:wn.options.time.report_timestep='ALL' if engine=='wntr' else task['hydraulic_options']['hydraulic_timestep_s']
    compiled=load(folder/'compiled-network.json');rebuilt=json.loads(json.dumps(wn.to_dict(),allow_nan=False))
    # WNTR stores the source's absolute path as a display name. Bind its file
    # name and all physical fields, while allowing a checkout at another root.
    for model in (compiled,rebuilt):model['name']=model['name'].replace('\\','/').rsplit('/',1)[-1]
    require(compiled==rebuilt,'Compiled network mismatch')
    require(load(folder/'entity-map.json')==mapping,'Worker entity mapping mismatch')
    require(series['units']==source['units'] and series['sampling']=='interval_start_hold','Worker units/sampling mismatch')
    raw=SimpleNamespace(node={},link={})
    for category in ('node','link'):
        for file in folder.glob('raw-'+category+'-*.csv'):
            field=file.stem[len('raw-'+category+'-'):]
            table=pd.read_csv(file,index_col='time_s',float_precision='round_trip')
            # EPANET BinFile returns float32 arrays; casting restores its exact
            # native values from historical shortest-decimal CSV representations.
            getattr(raw,category)[field]=table.astype(np.float32) if engine=='epanet' else table
    for tables,fields in ((raw.node,['head','pressure','demand']+(['leak_demand'] if engine=='wntr' else [])),(raw.link,['flowrate','status'])):
        require(set(fields)<=tables.keys(),'Missing raw hydraulic fields')
    times=list(raw.node['pressure'].index);w=task['time_window']
    public=list(range(0,w['end_s']+1,w['step_s']))
    require(times==sorted(set(times)) and times[0]==0 and times[-1]==w['end_s'] and set(public)<=set(times),'Raw time coverage mismatch')
    maximum_step=task['hydraulic_options']['hydraulic_timestep_s'] if detailed else w['step_s']
    require(all(int(t)==t for t in times) and all(b-a<=maximum_step for a,b in zip(times,times[1:])),'Raw time gaps')
    for category,ids in ((raw.node,wn.node_name_list),(raw.link,wn.link_name_list)):
        for field,table in category.items():
            require(list(table.index)==times and len(table.columns)==len(ids) and set(table.columns)==set(ids),'Raw table coverage mismatch: '+field)
            require(np.isfinite(table.to_numpy(dtype=float)).all(),'Non-finite raw data: '+field)
    # An energy-consistent uniform head shift is still wrong if it violates a
    # fixed source boundary. Check these independently of the pass sidecar.
    validate_boundaries(wn,raw,engine,times)
    expected=pd.DataFrame({name:[n.demand_timeseries_list.at(t+wn.options.time.pattern_start,multiplier=wn.options.hydraulic.demand_multiplier) for t in times] for name,n in wn.junctions()},index=times)
    stored=pd.read_csv(folder/'expected-demand.csv',index_col='time_s',float_precision='round_trip')
    actual_values=stored.size+sum(t.size for category in (raw.node,raw.link) for t in category.values())
    require(actual_values<=load(folder/'request.json')['execution_limits']['max_result_values'],'Actual result size exceeds execution limit')
    require(list(stored.index)==times and list(stored.columns)==list(expected.columns),'Expected demand coverage mismatch')
    require(np.isfinite(stored.to_numpy()).all() and np.allclose(stored.to_numpy(),expected.to_numpy(),atol=1e-10,rtol=1e-12),'Expected demand differs from frozen model')
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore',message='^Covariance of the parameters could not be estimated$',category=OptimizeWarning,module=r'^wntr\.network\.elements$')
        # The worker already preserves this exact three-point covariance warning;
        # the parent repeats and checks its curve fit instead of duplicating it.
        checks=assess(wn,raw,mapping,times,expected,exact_tank_balance=detailed and engine=='wntr')
    require(checks['passed'] is True,'Independent raw physics check failed')
    equivalent(load(folder/'physical-checks.json'),checks,'physics')
    if detailed:
        analysis=load(folder/'hydraulic-analysis.json')
        equivalent(analysis,engineering_summary(wn,raw,mapping,times,expected,task,analysis['solver']['records']),'analysis')
    def nonnegative(value):
        value=float(value);require(value>=-1e-9,'Negative raw demand');return max(value,0.0)
    for frame in series['frames']:
        t=frame['time_s'];fault=task['fault'];active=fault is not None and fault['start_s']<=t<fault['end_s']
        for n in frame['nodes']:
            name=n['node_id']
            for field,value in dict(pressure_m=float(raw.node['pressure'].at[t,name]),expected_demand_m3s=nonnegative(expected.at[t,name]),delivered_demand_m3s=nonnegative(raw.node['demand'].at[t,name]),leak_flow_m3s=nonnegative(raw.node['leak_demand'].at[t,name]) if engine=='wntr' else 0.0).items():equivalent(n[field],value,'series/'+name+'/'+field)
        for link in frame['links']:
            name=link['link_id'];segments=mapping['segments'][name];status=int(raw.link['status'].at[t,segments[0]])
            require(status in (0,1,2),'Invalid raw link status')
            state='closed' if status==0 else 'intact'
            if active and fault['link_id']==name and fault['kind'] in ('leak','rupture'):state='leaking' if fault['kind']=='leak' else 'ruptured'
            if active and fault['kind']=='isolation' and name in fault['boundary_link_ids']:state='isolated'
            for field,value in dict(flow_m3s=float(raw.link['flowrate'].at[t,segments[0]]),downstream_flow_m3s=float(raw.link['flowrate'].at[t,segments[-1]]),status='broken' if state=='ruptured' else ('closed' if status==0 else 'open'),physical_state=state).items():equivalent(link[field],value,'series/'+name+'/'+field)
        leaks=[dict(**spec,pressure_m=float(raw.node['pressure'].at[t,spec['node_id']]),leak_flow_m3s=nonnegative(raw.node['leak_demand'].at[t,spec['node_id']])) for spec in mapping['leaks']]
        equivalent(frame['leaks'],leaks,'series/leaks')
        tanks=[]
        for name,tank in wn.tanks():
            head=float(raw.node['head'].at[t,name]);level=head-tank.elevation
            tanks.append(dict(node_id=name,head_m=head,level_m=level,volume_m3=float(tank.get_volume(level)),net_inflow_m3s=float(raw.node['demand'].at[t,name])))
        equivalent(frame['tanks'],tanks,'series/tanks')


def validate_output(folder,*,expected_request=None,expected_manifest=None,expected_environment=None):
    folder=Path(folder)
    require(all(p.is_file() and not p.is_symlink() for p in folder.iterdir()),'Unsafe worker artifact')
    request=load(folder/'request.json')
    if expected_request is not None:require(request==expected_request,'Worker modified supervisor request')
    task,plan=request['task'],request['plan']
    validate('TaskSpec',task);validate('ActionPlan',plan)
    require(task_hash(task)==task['constraint_hash'] and plan_hash(plan)==plan['plan_hash'] and plan['task_ref']==task_ref(task),'Frozen input hash/binding mismatch')
    result=load(folder/'result.json')
    validate('SimulationResult',result)
    artifact,series,diag=result['artifact'],result['series'],result['diagnostics']
    require(task['schema_version']==plan['schema_version']==result['schema_version']==artifact['schema_version']==diag['schema_version'],'Worker schema version mismatch')
    require(artifact['simulation_id']==folder.name==diag['simulation_id'],'Worker simulation binding mismatch')
    require(artifact['task_ref']==task_ref(task) and artifact['candidate_id']==plan['candidate_id'],'Worker task/plan binding mismatch')
    require(artifact['engine_version']==request['engine_version'],'Worker engine binding mismatch')
    require(artifact['input_hash']==input_hash(task,plan,request['engine_version']),'Worker input hash mismatch')
    require(artifact['time_axis']==task['time_window'],'Worker time axis mismatch')
    require(task==load(folder/'task.json') and plan==load(folder/'plan.json'),'Worker frozen input mismatch')
    manifest=load(folder/'input-manifest.json');environment=load(folder/'environment.json')
    if expected_manifest is not None:require(manifest==expected_manifest,'Worker modified input manifest')
    if expected_environment is not None:require(environment==expected_environment,'Worker modified environment')
    require(manifest['input_hash']==artifact['input_hash'] and manifest['engine_version']==request['engine_version'],'Worker input manifest mismatch')
    require(request['engine_version'].endswith(';runtime/'+digest(environment)[7:]),'Frozen runtime identity mismatch')
    require(artifact['perturbation_id'] is None,'Unexpected perturbation binding')
    for key,name in [('source_sha256','source.inp'),('overlay_sha256','runtime-overlay.json'),('task_sha256','task.json'),('plan_sha256','plan.json'),('environment_sha256','environment.json')]:
        if key=='environment_sha256' and key not in manifest:continue  # Historical 0.4 evidence.
        require(manifest[key]==file_hash(folder/name),'Frozen file hash mismatch: '+name)
    require(manifest['source_sha256']==task['network_ref']['sha256'] and manifest['overlay_sha256']==task['network_ref']['overlay_hash'],'Source/overlay identity mismatch')
    require(manifest['failure_model']==task['fault'] and manifest['hydraulic_options']==task['hydraulic_options'] and manifest['control_policy']==task['control_policy'],'Input manifest options mismatch')
    for key,ref in [('diagnostics_ref','diagnostics.json'),('entity_map_ref','entity-map.json'),('physical_checks_ref','physical-checks.json'),('input_manifest_ref','input-manifest.json')]:
        require(artifact[key]==ref,'Worker artifact reference mismatch')
    require(result['mode']==artifact['mode']==diag['mode']=='benchmark','Worker mode mismatch')
    require(diag==load(folder/'diagnostics.json'),'Worker diagnostic file mismatch')
    if artifact['status']=='SUCCEEDED':
        require(diag['converged'] is True and diag['raw_converged'] is True and not diag['errors'] and diag['error_code'] is None,'Worker success without convergence')
        require(series is not None and series==load(folder/'series.json'),'Worker series file mismatch')
        require(series['simulation_id']==folder.name and series['mode']=='benchmark','Worker series binding mismatch')
        require(series['schema_version']==task['schema_version'],'Worker series version mismatch')
        require(artifact['series_ref']=='series.json','Worker series reference mismatch')
        mapping=load(folder/'entity-map.json')
        require(load(folder/'physical-checks.json')['passed'] is True,'Worker physics not passed')
        window=task['time_window']
        require([f['time_s'] for f in series['frames']]==list(range(0,window['end_s'],window['step_s'])),'Worker frame coverage mismatch')
        for frame in series['frames']:
            require(frame['duration_s']==window['step_s'],'Worker frame duration mismatch')
            for field,key,ids in [('nodes','node_id',mapping['original_junction_ids']),('links','link_id',mapping['original_link_ids'])]:
                require(sorted(x[key] for x in frame[field])==sorted(ids),'Worker entity coverage mismatch')
        for name in ('compiled-network.json','expected-demand.csv','raw-node-pressure.csv','raw-node-demand.csv','raw-link-flowrate.csv'):
            require((folder/name).is_file() and (folder/name).stat().st_size>0,'Missing worker evidence: '+name)
        if task['schema_version']=='0.4.0':
            analysis=load(folder/'hydraulic-analysis.json')
            require(analysis['mode']=='benchmark' and analysis['internal_samples']>=len(series['frames']), 'Missing dense hydraulic analysis')
            require(analysis['physical_parameters']==task['physical_parameters'],'Physical analysis parameter mismatch')
        validate_raw(folder,task,plan,series)
    else:
        require(series is None and artifact['series_ref'] is None and bool(diag['errors']) and diag['error_code'] is not None,'Invalid worker failure result')
        if artifact['status']=='CANCELLED':require(diag['error_code'] in ('CANCELLED','SUPERVISOR_INTERRUPTED'),'Invalid cancellation code')
    return result


def verify_inventory(folder):
    folder=Path(folder).resolve()
    paths=list(folder.iterdir())
    require(all(p.is_file() and not p.is_symlink() for p in paths),'Unsafe evidence file or directory')
    manifest=load(folder/'evidence-manifest.json')
    validate('EvidenceManifest',manifest)
    require(manifest['run_id']==folder.name and manifest['bundle_id']=='bundle-'+folder.name and manifest['mode']=='benchmark' and manifest['live_export_allowed'] is False,'Evidence bundle binding mismatch')
    entries=manifest['artifacts']
    refs=[x['ref'] for x in entries]
    require(len(refs)==len(set(refs)) and {x['ref']:x['sha256'] for x in entries}==manifest['hashes'],'Evidence manifest index mismatch')
    files={p.name for p in folder.iterdir() if p.is_file() and p.name!='evidence-manifest.json'}
    require(files==set(refs),'Evidence inventory mismatch')
    for ref,expected in manifest['hashes'].items():
        require(Path(ref).name==ref and '/' not in ref and '\\' not in ref and not (folder/ref).is_symlink(),'Unsafe evidence reference')
        require(file_hash(folder/ref)==expected,'Evidence hash mismatch: '+ref)
    return manifest
