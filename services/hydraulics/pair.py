"""Real, same-task A/B artifacts for backend feedback integration."""
import argparse
from pathlib import Path
from .acceptance import export
from .adapter import write
from .compiler import assets
from .datasets import ROOT
from .runtime import PhysicalAdapter, seal
from .scenarios import feedback_pair
from .replay import compare_frames
from waterguard_contracts.core import require, verify_benchmark


def run(output=ROOT/'artifacts/feedback-pair'):
    output=Path(output)
    task,a,b=feedback_pair()
    _,_,scene,catalog=assets('epa-net3')
    results=[]
    cases=[]
    for plan in (a,b):
        result=PhysicalAdapter(output).simulate(task,plan)
        require(result['artifact']['status']=='SUCCEEDED',str(result['diagnostics']['errors']))
        report=verify_benchmark(task,plan,result,catalog,scene)
        sid=result['artifact']['simulation_id']
        write(output/sid/'verification.json',report)
        seal(output/sid)
        cases.append(dict(case=plan['candidate_id'],simulation_id=sid,network_id='epa-net3',engine='wntr',
            metrics=report['metrics'],admission=report['admission'],result_ref=sid+'/result.json',verification_ref=sid+'/verification.json'))
        results.append(result)
    prewarm=compare_frames(results[0]['series']['frames'][:4],results[1]['series']['frames'][:4])
    require(prewarm['numerically_equivalent'],'A/B prewarm mismatch')
    require(cases[0]['admission']['nominal_constraints_pass']=='fail','Expected actual baseline violation')
    require(cases[1]['metrics']['shortage_m3']<cases[0]['metrics']['shortage_m3'],'B did not improve actual shortage')
    summary=dict(schema_version='0.3.0',mode='benchmark',run_id='physical-feedback-pair',cases=cases,prewarm_comparison=prewarm,
        task_constraint_hash=task['constraint_hash'],conclusion='B improves shortage; both retain pressure violations and are not recommendations.',
        attribution='Fixed engineering comparison; AGH must select its own next action from tool feedback in the eventual Live run.')
    write(output/'index.json',summary)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/feedback-pair')
    p.add_argument('--summary',type=Path)
    p.add_argument('--export',type=Path)
    args=p.parse_args()
    summary=run(args.output)
    if args.summary:write(args.summary,summary)
    if args.export:export(args.output,args.export)
    print([(c['case'],c['metrics']['shortage_m3']) for c in summary['cases']])
