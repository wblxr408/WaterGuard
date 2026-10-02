from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
from uuid import uuid4

from .adapter import ENGINE, EpanetAdapter, network_assets, write
from .datasets import NETWORKS, ROOT
from waterguard_contracts.core import (digest, file_hash, load, plan_hash, require,
                                     task_hash, task_ref, validate, verify_benchmark)


def make_case(network_id, fault_link=None):
    wn, source, scene, catalog=network_assets(network_id)
    settings=load(ROOT/source['overlays'][0]['ref'])
    suffix='normal' if fault_link is None else 'closure-'+fault_link
    task=dict(schema_version='0.2.0',task_id=network_id+'-'+suffix,revision=1,
              network_ref=dict(network_id=network_id,revision=source['revision'],sha256=source['sha256'],overlay_hash=source['overlays'][0]['sha256']),
              fault=None if fault_link is None else dict(kind='closure',link_id=fault_link,start_s=3600,end_s=10800,mapping='Timed EPANET pipe closure; no leak or rupture model.'),
              time_window=settings['time_window'],objectives=['minimize_shortage','minimize_action_cost'],
              hard_constraints=dict(min_pressure_m=20,min_supply_ratio=0.95,node_ids=wn.junction_name_list,pressure_tolerance_m=1e-6,ratio_tolerance=1e-6,source='WaterGuard benchmark assumptions, not source model or regulatory limits.'),
              soft_preferences=[],action_catalog_ref=f'data/catalogs/{network_id}.json',action_catalog_hash=file_hash(ROOT/f'data/catalogs/{network_id}.json'),
              action_limits=dict(max_actions=2,allowed_link_ids=[f['link_id'] for f in catalog['facilities']],allowed_kinds=['link_open','link_close'],earliest_s=3600,latest_s=9900,min_switch_interval_s=900),
              perturbation_domain=dict(required=['demand-plus-10pct'],demand_multipliers=[1,1.1]),assumptions=settings['assumptions'],budget=dict(max_simulations=40,max_parallel=2,max_wall_time_s=600,max_revisions=4))
    task['constraint_hash']=task_hash(task)
    plan=dict(schema_version='0.2.0',candidate_id=task['task_id']+'-baseline',task_ref=task_ref(task),parent_candidate_id=None,actions=[],source='rule',reason='Deterministic public-network baseline; no agent decision or invented improvement.')
    plan['plan_hash']=plan_hash(plan)
    validate('TaskSpec',task)
    validate('ActionPlan',plan)
    return task,plan,scene,catalog


def run(output_root=ROOT/'artifacts/hydraulics', networks=None):
    output_root=Path(output_root)
    adapter=EpanetAdapter(output_root)
    run_id='benchmark-'+uuid4().hex
    cases=[]
    faults={'epa-net1':'12','epa-net3':'123'}
    for network_id in networks or NETWORKS:
        for fault_link in (None,faults[network_id]):
            task,plan,scene,catalog=make_case(network_id,fault_link)
            result=adapter.simulate(task,plan)
            report=verify_benchmark(task,plan,result,catalog,scene)
            sid=result['artifact']['simulation_id']
            folder=output_root/sid
            write(folder/'verification.json',report)
            artifacts=[dict(ref=p.name,sha256=file_hash(p),kind=p.suffix.lstrip('.')) for p in sorted(folder.iterdir()) if p.is_file()]
            evidence=dict(schema_version='0.2.0',bundle_id='bundle-'+sid,run_id=run_id,mode='benchmark',native_trace_ref=None,association_ref=None,artifacts=artifacts,hashes={a['ref']:a['sha256'] for a in artifacts},redactions=[],missing_items=['native_agh_trace','demand-plus-10pct'],live_export_allowed=False)
            validate('EvidenceManifest',evidence)
            write(folder/'evidence-manifest.json',evidence)
            cases.append(dict(network_id=network_id,case='normal' if fault_link is None else 'closure',fault_link_id=fault_link,simulation_id=sid,
                              engine_status=result['artifact']['status'],validity=report['validity'],metrics=report['metrics'],admission=report['admission'],
                              node_count=len(scene['nodes']),link_count=len(scene['links']),
                              evaluated_junctions=len(task['hard_constraints']['node_ids']),frame_count=0 if result['series'] is None else len(result['series']['frames']),
                              result_ref=sid+'/result.json',verification_ref=sid+'/verification.json',evidence_ref=sid+'/evidence-manifest.json',
                              result_hash=file_hash(folder/'result.json'),errors=result['diagnostics']['errors']))
            print(json.dumps(cases[-1],ensure_ascii=False),flush=True)
    index=dict(schema_version='0.2.0',run_id=run_id,mode='benchmark',engine_version=ENGINE,created_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),cases=cases)
    write(output_root/'index.json',index)
    require(all(c['engine_status']=='SUCCEEDED' and c['validity']=='valid' for c in cases),'At least one hydraulic calculation failed; artifacts and diagnostics retained')
    return index


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Compute public Net1/Net3 baselines and pipe-closure scenarios with EPANET')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/hydraulics')
    parser.add_argument('--network',choices=list(NETWORKS))
    parser.add_argument('--summary',type=Path,help='Optionally save a small verification summary for the repository')
    parser.add_argument('--export',type=Path,help='Export this benchmark run, raw engine outputs and evidence manifests as ZIP')
    args=parser.parse_args()
    index=run(args.output,[args.network] if args.network else None)
    if args.summary:
        write(args.summary,index)
    if args.export:
        import zipfile
        args.export.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(args.export,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(args.output/'index.json','index.json')
            for case in index['cases']:
                folder=args.output/case['simulation_id']
                for path in sorted(folder.iterdir()):
                    if path.is_file():
                        bundle.write(path,path.relative_to(args.output).as_posix())
        write(args.export.with_suffix('.zip.sha256.json'),dict(file=args.export.name,sha256=file_hash(args.export),mode='benchmark',run_id=index['run_id']))
    print('Real hydraulic runs complete; mode=benchmark, no AGH session or final recommendation.')
