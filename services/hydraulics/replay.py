"""Validate a stored run and recompute its frozen inputs; never reuse cached numbers."""
import argparse
import math
from pathlib import Path
from .adapter import write
from .datasets import ROOT
from .runtime import PhysicalAdapter, engine_identity
from waterguard_contracts.core import file_hash, load, require, resolve


def compare_frames(a,b):
    """Floating solver roundoff is reported, never hidden by rounding stored values."""
    errors={}
    equivalent=True
    def compare(x,y,path):
        nonlocal equivalent
        if isinstance(x,float) and isinstance(y,(int,float)):
            error=abs(x-y)
            errors[path.rsplit('/',1)[-1]]=max(errors.get(path.rsplit('/',1)[-1],0),error)
            equivalent &= math.isclose(x,y,abs_tol=1e-10,rel_tol=1e-12)
        elif isinstance(x,dict) and isinstance(y,dict) and x.keys()==y.keys():
            for key in x:compare(x[key],y[key],path+'/'+key)
        elif isinstance(x,list) and isinstance(y,list) and len(x)==len(y):
            for i,(u,v) in enumerate(zip(x,y)):compare(u,v,path+'/'+str(i))
        else:
            equivalent &= x==y
    compare(a,b,'frames')
    return dict(numerically_identical=a==b,numerically_equivalent=bool(equivalent),absolute_tolerance=1e-10,
                relative_tolerance=1e-12,max_absolute_difference_by_field=errors)


def verify_evidence(folder):
    folder=Path(folder).resolve()
    manifest=load(folder/'evidence-manifest.json')
    files={p.name for p in folder.iterdir() if p.is_file() and p.name!='evidence-manifest.json'}
    require(files==set(manifest['hashes']),'Evidence inventory mismatch')
    for ref,expected in manifest['hashes'].items():
        require(file_hash(resolve(folder,ref))==expected,'Evidence hash mismatch: '+ref)
    result=load(folder/'result.json')
    require(result['artifact']['status']=='SUCCEEDED','Cannot numerically replay an invalid run')
    return result


def replay(folder,output=ROOT/'artifacts/replay'):
    folder=Path(folder)
    previous=verify_evidence(folder)
    task,plan=load(folder/'task.json'),load(folder/'plan.json')
    require(previous['artifact']['engine_version']==engine_identity(task),'Implementation/version changed; use a new comparison, not an exact replay')
    result=PhysicalAdapter(output).simulate(task,plan)
    require(result['artifact']['status']=='SUCCEEDED',str(result['diagnostics']['errors']))
    comparison=compare_frames(result['series']['frames'],previous['series']['frames'])
    return dict(source_simulation_id=previous['artifact']['simulation_id'],simulation_id=result['artifact']['simulation_id'],
                recomputed=True,**comparison,input_hash_identical=result['artifact']['input_hash']==previous['artifact']['input_hash'])


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('folder',type=Path)
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/replay')
    args=p.parse_args()
    result=replay(args.folder,args.output)
    print(result)
    require(result['numerically_equivalent'] and result['input_hash_identical'],'Replay mismatch')
