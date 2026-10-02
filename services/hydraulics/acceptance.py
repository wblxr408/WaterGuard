"""Executable student-1 delivery matrix and compact, attributable evidence export."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import zipfile

from .adapter import write
from .compiler import assets
from .datasets import ROOT, NETWORKS
from .runtime import PhysicalAdapter, seal
from .scenarios import case
from waterguard_contracts.core import file_hash, load, require, verify_benchmark

KINDS=('normal','closure','leak','rupture','isolation','pump-cycle','link-cycle')


def run(output=ROOT/'artifacts/student1'):
    output=Path(output)
    adapter=PhysicalAdapter(output)
    cases=[]
    for network in NETWORKS:
        for engine,kinds in [('wntr',KINDS),('epanet',('normal','closure','pump-cycle','link-cycle'))]:
            for kind in kinds:
                task,plan=case(network,kind,engine)
                result=adapter.simulate(task,plan)
                sid=result['artifact']['simulation_id']
                folder=output/sid
                _,_,scene,catalog=assets(network)
                report=verify_benchmark(task,plan,result,catalog,scene)
                write(folder/'verification.json',report)
                seal(folder)
                frames=[] if result['series'] is None else result['series']['frames']
                cases.append(dict(network_id=network,case=kind,engine=engine,simulation_id=sid,
                    engine_status=result['artifact']['status'],validity=report['validity'],metrics=report['metrics'],admission=report['admission'],
                    physical_checks=load(folder/'physical-checks.json'),
                    leakage_volume_m3=sum(sum(l['leak_flow_m3s'] for l in f['leaks'])*f['duration_s'] for f in frames),
                    result_ref=sid+'/result.json',verification_ref=sid+'/verification.json',evidence_ref=sid+'/evidence-manifest.json',
                    result_hash=file_hash(folder/'result.json'),errors=result['diagnostics']['errors']))
                print(network,engine,kind,result['artifact']['status'],result['diagnostics']['errors'],flush=True)
    summary=dict(schema_version='0.3.0',run_id='student1-acceptance',mode='benchmark',created_at=datetime.now(timezone.utc).isoformat(),
                 platform=platform.platform(),cases=cases)
    write(output/'index.json',summary)
    require(all(c['engine_status']=='SUCCEEDED' and c['validity']=='valid' for c in cases),'Acceptance case failed; inspect retained diagnostics')
    return summary


def export(output,target):
    output,target=Path(output),Path(target)
    index=load(output/'index.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.write(output/'index.json','index.json')
        for c in index['cases']:
            for p in sorted((output/c['simulation_id']).iterdir()):
                if p.is_file():
                    z.write(p,p.relative_to(output).as_posix())
    write(target.with_suffix('.zip.sha256.json'),dict(file=target.name,sha256=file_hash(target),mode='benchmark'))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/student1')
    p.add_argument('--summary',type=Path)
    p.add_argument('--export',type=Path)
    args=p.parse_args()
    summary=run(args.output)
    if args.summary:write(args.summary,summary)
    if args.export:export(args.output,args.export)
