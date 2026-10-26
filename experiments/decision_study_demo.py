"""Run the new decision-study journey on real public-network hydraulics.

Creates separate benchmark scenario evidence; does not call a model or control equipment.
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'packages/contracts')]
from fastapi.testclient import TestClient
from services.api.main import create_app
from services.api.runtime import RunStore
from services.api.workbench import TaskProposal, propose_task, benchmark_plans, network_details


def run(output):
    store=RunStore(output/'state.sqlite3',output)
    task=propose_task(TaskProposal(network_id='epa-net1',fault_kind='leak',threshold_mode='manual',
        min_pressure_m=20,min_supply_ratio=.95,candidate_limit=6,fault_parameters={'leak_area_m2':.01865}))
    plans=benchmark_plans(task,network_details('epa-net1')['catalog'])
    common=dict(task=task,plan=plans[0],max_wall_time_s=180)
    specs=[dict(common,kind='boundary',demand_multipliers=[.9,1,1.2],repair_end_times_s=[6300,7200,8100],
                max_simulations=6,use_surrogate=True,high_fidelity_budget=2),
           dict(common,kind='rescue',max_simulations=3,rescue_target='basic',rescue_options=[
                dict(label='抢修提前15分钟',cost=1,repair_end_s=6300),dict(label='抢修提前30分钟',cost=2,repair_end_s=5400)]),
           dict(common,kind='event',max_simulations=3,event_time_s=4500,response_time_s=600,new_repair_end_s=8100)]
    summaries=[]
    with TestClient(create_app(store)) as client:
        for request in specs:
            response=client.post('/api/decision-studies',json=request)
            if response.status_code!=202:raise RuntimeError(response.text)
            study_id=response.json()['study_id']
            while True:
                study=client.get(f'/api/decision-studies/{study_id}').json()
                if study['status'] not in ('RUNNING','QUEUED'):break
                time.sleep(.15)
            if study['status']!='COMPLETED':raise RuntimeError(json.dumps(study.get('error',study['status']),ensure_ascii=False))
            measured=[p for p in study['points'] if p['state']!='unknown']
            if not measured or not all(p['report']['validity']=='valid' for p in measured):
                raise RuntimeError('Study has no valid independently verified hydraulic results')
            if request['kind']=='boundary':
                assert study['fidelity_counts']==dict(low=4,high=2)
                assert study['surrogate']['training_pairs']==2
                assert len(measured)==2 and sum(p['state']=='unknown' for p in study['points'])==7
            if request['kind']=='event':
                assert all('remaining' in p for p in measured)
                assert all(a['simulation_time_s']>=5400 for p in measured[1:] for a in p['plan']['actions'])
            summaries.append(dict(kind=study['kind'],study_id=study_id,status=study['status'],simulations=study['simulations'],
                fidelity_counts=study['fidelity_counts'],selected_point_id=study['selected_point_id'],
                elapsed_s=study['elapsed_s'],surrogate=study.get('surrogate'),
                points=[dict(point_id=p['point_id'],label=p['label'],state=p['state'],shortage_m3=p.get('metrics',{}).get('shortage_m3'),
                    remaining=p.get('remaining'),cost=p['cost'],evidence_ref=p.get('evidence_ref')) for p in study['points']]))
            print(json.dumps(summaries[-1],ensure_ascii=False),flush=True)
    (output/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/decision-study-acceptance-20261018')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    run(args.output.resolve())
