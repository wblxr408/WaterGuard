"""Validate the worker protocol independently of the worker's success claim."""
from pathlib import Path
from .datasets import ROOT
from waterguard_contracts.core import file_hash, input_hash, load, require, task_ref, validate


def validate_output(folder):
    folder=Path(folder)
    request=load(folder/'request.json')
    task,plan=request['task'],request['plan']
    result=load(folder/'result.json')
    validate('SimulationResult',result)
    artifact,series,diag=result['artifact'],result['series'],result['diagnostics']
    require(artifact['simulation_id']==folder.name==diag['simulation_id'],'Worker simulation binding mismatch')
    require(artifact['task_ref']==task_ref(task) and artifact['candidate_id']==plan['candidate_id'],'Worker task/plan binding mismatch')
    require(artifact['engine_version']==request['engine_version'],'Worker engine binding mismatch')
    require(artifact['input_hash']==input_hash(task,plan,request['engine_version']),'Worker input hash mismatch')
    require(artifact['time_axis']==task['time_window'],'Worker time axis mismatch')
    require(task==load(folder/'task.json') and plan==load(folder/'plan.json'),'Worker frozen input mismatch')
    require(load(folder/'input-manifest.json')['input_hash']==artifact['input_hash'],'Worker input manifest mismatch')
    for key,ref in [('diagnostics_ref','diagnostics.json'),('entity_map_ref','entity-map.json'),('physical_checks_ref','physical-checks.json'),('input_manifest_ref','input-manifest.json')]:
        require(artifact[key]==ref,'Worker artifact reference mismatch')
    require(result['mode']==artifact['mode']==diag['mode']=='benchmark','Worker mode mismatch')
    require(diag==load(folder/'diagnostics.json'),'Worker diagnostic file mismatch')
    if artifact['status']=='SUCCEEDED':
        require(diag['converged'] is True and diag['raw_converged'] is True and not diag['errors'],'Worker success without convergence')
        require(series is not None and series==load(folder/'series.json'),'Worker series file mismatch')
        require(series['simulation_id']==folder.name and series['mode']=='benchmark','Worker series binding mismatch')
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
    else:
        require(series is None and artifact['series_ref'] is None and bool(diag['errors']),'Invalid worker failure result')
    return result


def verify_inventory(folder):
    folder=Path(folder).resolve()
    manifest=load(folder/'evidence-manifest.json')
    validate('EvidenceManifest',manifest)
    entries=manifest['artifacts']
    refs=[x['ref'] for x in entries]
    require(len(refs)==len(set(refs)) and {x['ref']:x['sha256'] for x in entries}==manifest['hashes'],'Evidence manifest index mismatch')
    files={p.name for p in folder.iterdir() if p.is_file() and p.name!='evidence-manifest.json'}
    require(files==set(refs),'Evidence inventory mismatch')
    for ref,expected in manifest['hashes'].items():
        require(Path(ref).name==ref and not (folder/ref).is_symlink(),'Unsafe evidence reference')
        require(file_hash(folder/ref)==expected,'Evidence hash mismatch: '+ref)
    return manifest
