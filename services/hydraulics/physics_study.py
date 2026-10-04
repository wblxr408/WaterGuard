"""Reproducible physical sensitivity, numerical refinement and fixed-plan comparison."""
import argparse
import copy
from pathlib import Path
from .acceptance import export
from .compiler import assets
from .datasets import ROOT
from .runtime import PhysicalAdapter,seal
from .scenarios import physical_case,feedback_pair,rebind
from .storage import write
from waterguard_contracts.core import load,verify_benchmark,require


def run(output=ROOT/'artifacts/physics-study'):
    output=Path(output);adapter=PhysicalAdapter(output);cases=[]
    def calculate(label,task,plan,group):
        task,plan=copy.deepcopy(task),copy.deepcopy(plan)
        task['task_id']+='-'+label
        plan['candidate_id']+='-'+label
        rebind(task,plan)
        result=adapter.simulate(task,plan)
        sid=result['artifact']['simulation_id'];folder=output/sid
        _,_,scene,catalog=assets(task['network_ref']['network_id'])
        verification=verify_benchmark(task,plan,result,catalog,scene)
        write(folder/'verification.json',verification);seal(folder)
        analysis=load(folder/'hydraulic-analysis.json') if (folder/'hydraulic-analysis.json').exists() else None
        record=dict(case=label,group=group,network_id=task['network_ref']['network_id'],engine=task['hydraulic_options']['engine'],
            simulation_id=sid,status=result['artifact']['status'],errors=result['diagnostics']['errors'],
            result_ref=sid+'/result.json',analysis_ref=sid+'/hydraulic-analysis.json' if analysis else None,
            metrics=None if analysis is None else analysis['dense_metrics'],energy=None if analysis is None else analysis['pump_energy'],
            solver=None if analysis is None else {k:v for k,v in analysis['solver'].items() if k!='records'},
            physical_checks=load(folder/'physical-checks.json'),nominal_admission=verification['admission'])
        cases.append(record)
        print(label,record['status'],record['errors'],flush=True)
        return record
    task,plan=physical_case()
    refinement=[]
    for dt in (300,150,75):
        task['hydraulic_options']['hydraulic_timestep_s']=dt
        refinement.append(calculate('rupture-step-'+str(dt),task,plan,'time_refinement'))
    task['hydraulic_options']['hydraulic_timestep_s']=300
    task['physical_parameters']['solver_tolerance']=1e-6
    calculate('rupture-solver-default-tol',task,plan,'solver_precision')
    task['physical_parameters']['solver_tolerance']=1e-8
    variants=[('demand-plus-10pct',{'demand_multiplier':1.1}),('roughness-C-minus-20pct',{'roughness_multiplier':.8}),
        ('reservoir-head-minus-2m',{'reservoir_head_offset_m':-2}),('tank-level-minus-0.3m',{'tank_level_offset_m':-.3}),
        ('Cd-0.60',{'discharge_coefficient':.6}),('Cd-0.90',{'discharge_coefficient':.9}),
        ('rupture-at-quarter',{'location_fraction':.25}),('rupture-at-three-quarters',{'location_fraction':.75}),
        ('repair-15min-later',{'repair_end_s':8100}),('local-demand-plus-20pct',{'node_demand_multipliers':{'121':1.2}}),
        ('local-service-pressure-30m',{'node_pdd':{'121':dict(minimum_pressure_m=2,required_pressure_m=30,pressure_exponent=.5)}}),
        ('background-leak-node121',{'background_leaks':[dict(node_id='121',area_m2=1e-5,discharge_coefficient=.75)]})]
    for label,changes in variants:
        t=copy.deepcopy(task)
        for key,value in changes.items():
            if key in ('discharge_coefficient','location_fraction'):t['fault'][key]=value
            elif key=='repair_end_s':t['fault']['end_s']=value
            elif key=='demand_multiplier':t['hydraulic_options'][key]=value
            else:t['physical_parameters'][key]=value
        calculate(label,t,plan,'physical_sensitivity')
    cross=[]
    for engine in ('wntr','epanet'):
        t,p=physical_case('epa-net3','closure',engine)
        t['hydraulic_options']['hydraulic_timestep_s']=75
        cross.append(calculate('closure-'+engine,t,p,'cross_engine'))
    comparisons=[]
    base,a,b=feedback_pair()
    base['schema_version']=a['schema_version']=b['schema_version']='0.4.0'
    base['physical_parameters']=copy.deepcopy(task['physical_parameters'])
    base['hydraulic_options']['hydraulic_timestep_s']=150
    for label,changes in [('nominal',{}),('demand-high',{'demand_multiplier':1.1}),('roughness-low',{'roughness_multiplier':.8}),('head-low',{'reservoir_head_offset_m':-2})]:
        t=copy.deepcopy(base)
        for key,value in changes.items():
            if key=='demand_multiplier':t['hydraulic_options'][key]=value
            else:t['physical_parameters'][key]=value
        left=calculate(label+'-A',t,a,'fixed_plan_comparison');right=calculate(label+'-B',t,b,'fixed_plan_comparison')
        comparisons.append(dict(scenario=label,a=left['simulation_id'],b=right['simulation_id'],
            shortage_reduction_m3=None if not left['metrics'] or not right['metrics'] else left['metrics']['shortage_m3']-right['metrics']['shortage_m3'],
            both_valid=left['status']==right['status']=='SUCCEEDED',b_nominal_admission=right['nominal_admission']))
    refinement_deltas=[]
    for coarse,fine in zip(refinement,refinement[1:]):
        if coarse['metrics'] and fine['metrics']:
            refinement_deltas.append(dict(coarse=coarse['case'],fine=fine['case'],
                shortage_abs_change_m3=abs(coarse['metrics']['shortage_m3']-fine['metrics']['shortage_m3']),
                leakage_abs_change_m3=abs(coarse['metrics']['leakage_m3']-fine['metrics']['leakage_m3']),
                min_pressure_abs_change_m=abs(coarse['metrics']['min_pressure_m']-fine['metrics']['min_pressure_m'])))
    resolution=None
    if len(refinement_deltas)==2:
        latest=refinement_deltas[-1];fine=refinement[-1]['metrics']
        resolution=dict(shortage_relative_change=latest['shortage_abs_change_m3']/fine['shortage_m3'],
            leakage_relative_change=latest['leakage_abs_change_m3']/fine['leakage_m3'],
            min_pressure_abs_change_m=latest['min_pressure_abs_change_m'],
            volume_relative_target=.001,pressure_abs_target_m=.02,
            interpretation='Successive refinement agreement for this case; not an error bound against field truth.')
        resolution['passed']=resolution['shortage_relative_change']<=.001 and resolution['leakage_relative_change']<=.001 and resolution['min_pressure_abs_change_m']<=.02
    cross_difference=None
    if all(c['metrics'] for c in cross):
        cross_difference={k:abs(cross[0]['metrics'][k]-cross[1]['metrics'][k]) for k in ('shortage_m3','supply_ratio','min_pressure_m')}
    summary=dict(schema_version='0.4.0',mode='benchmark',run_id='physics-study',cases=cases,
        total_cases=len(cases),invalid_cases=[c['case'] for c in cases if c['status']!='SUCCEEDED'],
        time_refinement=refinement_deltas,numerical_resolution_assessment=resolution,cross_engine_abs_difference=cross_difference,fixed_plan_comparisons=comparisons,
        uncertainty_interpretation='Explicit engineering scenarios; no measured distribution, confidence interval or probability is inferred.',
        algorithm_interpretation='Mature hydraulic solvers, independently checked equations; fixed A/B comparison does not establish an optimal action policy.',
        transient_adequacy='Not established: no elastic water-hammer, air/column separation, cavitation or actuator stroke model.')
    write(output/'index.json',summary)
    require(not summary['invalid_cases'],'Physical study has invalid cases; inspect retained diagnostics')
    require(resolution is not None and resolution['passed'],'Physical study resolution target not achieved')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'artifacts/physics-study')
    p.add_argument('--summary',type=Path);p.add_argument('--export',type=Path);args=p.parse_args()
    result=run(args.output)
    if args.summary:write(args.summary,result)
    if args.export:export(args.output,args.export)
