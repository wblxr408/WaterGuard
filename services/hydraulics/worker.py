"""One engine run per process. Called by PhysicalAdapter, not an HTTP endpoint."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import sys
import warnings
import pandas as pd

import wntr
from scipy.optimize import OptimizeWarning
from .adapter import write
from .compiler import compile_task
from .physical import assess, engineering_summary
from .numerics import solver_statistics
from .runtime import finish, engine_identity, runtime_environment
from waterguard_contracts.core import load, require, validate, file_hash


def compute(folder):
    folder = Path(folder)
    request=load(folder/'request.json')
    task,plan,perturbation=request['task'],request['plan'],request.get('perturbation')
    captured=[]
    raw_converged=None
    code='INPUT_INVALID'
    try:
        require(os.environ.get('PYTHONHASHSEED')=='0' and all(os.environ.get(k)=='1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')),'Worker numerical environment mismatch')
        require(request['engine_version']==engine_identity(task),'Worker implementation/runtime drift')
        require(task==load(folder/'task.json') and plan==load(folder/'plan.json'),'Request differs from frozen task/plan')
        require(load(folder/'environment.json')==runtime_environment(),'Worker environment differs from frozen input')
        for key,name in [('source_sha256','source.inp'),('overlay_sha256','runtime-overlay.json'),('task_sha256','task.json'),('plan_sha256','plan.json'),('environment_sha256','environment.json')]:
            require(load(folder/'input-manifest.json')[key]==file_hash(folder/name),'Frozen input drift: '+name)
        wn,source,scene,catalog,mapping=compile_task(task,plan,perturbation)
        detailed=task['schema_version']=='0.4.0'
        engine=task['hydraulic_options']['engine']
        hydraulic_step=task['hydraulic_options']['hydraulic_timestep_s']
        wn.options.time.report_timestep=('ALL' if engine=='wntr' else hydraulic_step) if detailed else task['time_window']['step_s']
        frames=task['time_window']['end_s']//(hydraulic_step if detailed else task['time_window']['step_s'])+1
        estimated_values=frames*(len(wn.node_name_list)*12+len(wn.link_name_list)*8)
        require(estimated_values<=request['execution_limits']['max_result_values'],'Result size exceeds execution limit')
        write(folder/'entity-map.json',mapping)
        write(folder/'scene.json',scene)
        write(folder/'action-catalog.json',catalog)
        write(folder/'compiled-network.json',wn.to_dict())
        code='SIMULATION_FAILED'
        stats=[]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            try:
                for _,pump in wn.pumps():
                    pump.get_head_curve_coefficients()
                if engine=='epanet':
                    raw=wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=str(folder/'engine'),version=2.2,convergence_error=True)
                else:
                    with solver_statistics() as stats:
                        solver_options=None if not detailed else dict(TOL=task['physical_parameters']['solver_tolerance'],MAXITER=1000,BACKTRACKING=True)
                        raw=wntr.sim.WNTRSimulator(wn).run_sim(convergence_error=True,solver_options=solver_options)
            finally:
                captured=[str(w.message) for w in caught]
        require(raw.error_code in (None,0),f'Engine error_code={raw.error_code}')
        raw_converged=True
        for category,tables in [('node',raw.node),('link',raw.link)]:
            for field,table in tables.items():
                table.to_csv(folder/f'raw-{category}-{field}.csv',index_label='time_s',lineterminator='\n',float_format='%.17g')
        if engine=='epanet':
            require('WARNING' not in (folder/'engine.rpt').read_text(errors='replace').upper(),'EPANET report warning')
        # Three points determine three pump coefficients exactly; covariance cannot be
        # estimated with zero residual degrees of freedom. Preserve that specific warning
        # and independently check all curve points below. Never whitelist convergence warnings.
        unexpected=[w for w in caught if not (w.category is OptimizeWarning and
            str(w.message)=='Covariance of the parameters could not be estimated' and
            w.filename.replace('\\','/').endswith('/wntr/network/elements.py'))]
        require(not unexpected, 'Engine warnings require inspection: '+'; '.join(str(w.message) for w in unexpected))
        w=task['time_window']
        times=list(range(0,w['end_s']+1,w['step_s']))
        raw_times=list(raw.node['pressure'].index)
        require(raw_times==sorted(set(raw_times)) and raw_times[0]==0 and raw_times[-1]==w['end_s'],'Invalid internal time axis')
        require(set(times)<=set(raw_times),'Missing public reporting times')
        require(all(int(t)==t for t in raw_times),'Fractional internal result time')
        for tables in (raw.node,raw.link):
            for field,table in tables.items():
                require(list(table.index)==raw_times,'Missing or reordered result times in '+field)
                require(all(math.isfinite(float(v)) for v in table.to_numpy().ravel()),'Non-finite raw result in '+field)
        expected=pd.DataFrame({name:[node.demand_timeseries_list.at(t+wn.options.time.pattern_start,multiplier=wn.options.hydraulic.demand_multiplier) for t in raw_times] for name,node in wn.junctions()},index=raw_times)
        checks=assess(wn,raw,mapping,raw_times,expected,exact_tank_balance=detailed and engine=='wntr')
        write(folder/'physical-checks.json',checks)
        require(checks['passed'],'Physical invariant check failed; inspect physical-checks.json')
        expected.to_csv(folder/'expected-demand.csv',index_label='time_s',lineterminator='\n')
        write(folder/'hydraulic-analysis.json',engineering_summary(wn,raw,mapping,raw_times,expected,task,stats))
        fault=task['fault']
        rounded=0

        def nonnegative(value):
            nonlocal rounded
            value=float(value)
            require(value>=-1e-9,'Negative demand beyond numerical tolerance')
            if value<0:
                rounded+=1
                return 0.0
            return value

        frames=[]
        for t in times[:-1]:
            active=fault is not None and fault['start_s']<=t<fault['end_s']
            nodes=[dict(node_id=name,pressure_m=float(raw.node['pressure'].at[t,name]),expected_demand_m3s=nonnegative(expected.at[t,name]),
                        delivered_demand_m3s=nonnegative(raw.node['demand'].at[t,name]),leak_flow_m3s=nonnegative(raw.node['leak_demand'].at[t,name]) if 'leak_demand' in raw.node else 0.0) for name in mapping['original_junction_ids']]
            links=[]
            for name,segments in mapping['segments'].items():
                status=int(raw.link['status'].at[t,segments[0]])
                require(status in (0,1,2),'Unrecognized engine status')
                state='closed' if status==0 else 'intact'
                is_target=active and fault['link_id']==name
                if is_target and fault['kind'] in ('leak','rupture'):
                    state='leaking' if fault['kind']=='leak' else 'ruptured'
                if active and fault['kind']=='isolation' and name in fault['boundary_link_ids']:
                    state='isolated'
                links.append(dict(link_id=name,flow_m3s=float(raw.link['flowrate'].at[t,segments[0]]),
                    downstream_flow_m3s=float(raw.link['flowrate'].at[t,segments[-1]]),status='broken' if state=='ruptured' else ('closed' if status==0 else 'open'),physical_state=state))
            leaks=[dict(**spec,pressure_m=float(raw.node['pressure'].at[t,spec['node_id']]),leak_flow_m3s=nonnegative(raw.node['leak_demand'].at[t,spec['node_id']])) for spec in mapping['leaks']]
            tanks=[]
            for name,tank in wn.tanks():
                level=float(raw.node['head'].at[t,name])-tank.elevation
                tanks.append(dict(node_id=name,head_m=float(raw.node['head'].at[t,name]),level_m=level,
                    volume_m3=float(tank.get_volume(level)),net_inflow_m3s=float(raw.node['demand'].at[t,name])))
            frames.append(dict(time_s=t,duration_s=w['step_s'],nodes=nodes,links=links,leaks=leaks,tanks=tanks))
        if rounded:
            captured.append(f'{rounded} values in [-1e-9,0) m3/s set to zero; raw CSV remains unchanged.')
        series=dict(schema_version=task['schema_version'],simulation_id=folder.name,mode='benchmark',units=source['units'],sampling='interval_start_hold',frames=frames)
        validate('Series',series)
        return finish(folder,series=series,warnings=captured,raw_converged=True)
    except Exception as exc:
        if code=='SIMULATION_FAILED' and raw_converged is None:
            raw_converged=False
        return finish(folder,errors=[f'{type(exc).__name__}: {exc}'],warnings=captured,raw_converged=raw_converged,error_code=code)


if __name__=='__main__':
    compute(sys.argv[1])
