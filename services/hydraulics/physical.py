"""Independent hydraulic equations, dense diagnostics and applicability limits."""
import math
import numpy as np

G=9.81
HW_K=10.666829500036352


def assess(wn,raw,mapping,times,expected=None,*,exact_tank_balance=False):
    junctions=list(wn.junction_name_list)
    links=list(wn.link_name_list)
    incidence=np.zeros((len(junctions),len(links)))
    indexes={name:i for i,name in enumerate(junctions)}
    for j,name in enumerate(links):
        p=wn.get_link(name)
        if p.start_node_name in indexes:incidence[indexes[p.start_node_name],j]-=1
        if p.end_node_name in indexes:incidence[indexes[p.end_node_name],j]+=1
    q=raw.link['flowrate'].loc[times,links].to_numpy(dtype=float)
    demand=raw.node['demand'].loc[times,junctions].to_numpy(dtype=float)
    leak=raw.node['leak_demand'].loc[times,junctions].to_numpy(dtype=float) if 'leak_demand' in raw.node else np.zeros_like(demand)
    continuity=float(np.max(np.abs(q@incidence.T-demand-leak)))
    leak_error=inactive=energy_error=pump_fit=pump_operating=pdd_error=pdd_abs=pdd_scaled=tank_bounds=tank_balance=closed_flow=0.0
    energy_count=pdd_count=pdd_smoothed=0
    for spec in mapping['leaks']:
        name=spec['node_id']
        start,end=mapping.get('leak_windows',{}).get(name,mapping['fault_window'])
        for t in times:
            actual=float(raw.node['leak_demand'].at[t,name]);p=float(raw.node['pressure'].at[t,name])
            if not start<=t<end:inactive=max(inactive,abs(actual))
            elif p>1e-3:
                ideal=spec['discharge_coefficient']*spec['area_m2']*math.sqrt(2*G*p)
                leak_error=max(leak_error,abs(actual-ideal)/ideal)
    for name,pipe in wn.pipes():
        resistance=HW_K*pipe.length*pipe.roughness**-1.852*pipe.diameter**-4.871
        minor=8*pipe.minor_loss/(G*math.pi**2*pipe.diameter**4)
        for t in times:
            flow=float(raw.link['flowrate'].at[t,name]);status=int(raw.link['status'].at[t,name])
            if status==0:closed_flow=max(closed_flow,abs(flow));continue
            # Zero-flow disconnected nodes have no uniquely meaningful head equation.
            if abs(flow)<1e-7:continue
            observed=float(raw.node['head'].at[t,pipe.start_node_name]-raw.node['head'].at[t,pipe.end_node_name])
            loss=math.copysign(resistance*abs(flow)**1.852+minor*flow**2,flow)
            energy_error=max(energy_error,abs(observed-loss));energy_count+=1
    for name,pump in wn.pumps():
        a,b,c=pump.get_head_curve_coefficients()
        for flow,head in wn.get_curve(pump.pump_curve_name).points:
            pump_fit=max(pump_fit,abs(a-b*flow**c-head))
        for t in times:
            flow=float(raw.link['flowrate'].at[t,name])
            if int(raw.link['status'].at[t,name])==0:closed_flow=max(closed_flow,abs(flow));continue
            if flow<1e-8:continue
            gain=float(raw.node['head'].at[t,pump.end_node_name]-raw.node['head'].at[t,pump.start_node_name])
            pump_operating=max(pump_operating,abs(gain-(a-b*flow**c)))
    if expected is not None:
        for name,node in wn.junctions():
            p0=node.minimum_pressure if node.minimum_pressure is not None else wn.options.hydraulic.minimum_pressure
            pf=node.required_pressure if node.required_pressure is not None else wn.options.hydraulic.required_pressure
            exponent=node.pressure_exponent if node.pressure_exponent is not None else wn.options.hydraulic.pressure_exponent
            for t in times:
                full=float(expected.at[t,name]);p=float(raw.node['pressure'].at[t,name])
                if full<=1e-12:continue
                # WNTR smooths its law in 0.05 m bands; do not mistake it for a new physics law.
                if p0<p<p0+.05 or pf-.05<p<pf:pdd_smoothed+=1;continue
                ratio=0 if p<=p0 else (1 if p>=pf else ((p-p0)/(pf-p0))**exponent)
                actual=float(raw.node['demand'].at[t,name])/full
                pdd_error=max(pdd_error,abs(actual-ratio));pdd_count+=1
                difference=abs(actual-ratio)*full
                pdd_abs=max(pdd_abs,difference)
                pdd_scaled=max(pdd_scaled,difference/(1e-7+1e-4*full))
    for name,tank in wn.tanks():
        levels=[float(raw.node['head'].at[t,name])-tank.elevation for t in times]
        volumes=[float(tank.get_volume(level)) for level in levels]
        for level in levels:tank_bounds=max(tank_bounds,tank.min_level-level,level-tank.max_level)
        for i in range(len(times)-1):
            residual=volumes[i+1]-volumes[i]-float(raw.node['demand'].at[times[i],name])*(times[i+1]-times[i])
            tank_balance=max(tank_balance,abs(residual))
    passed=continuity<=2e-6 and leak_error<=1e-4 and inactive<=1e-10 and pump_fit<=1e-4 and energy_error<=.05 and pump_operating<=.05 and pdd_scaled<=1 and tank_bounds<=.001 and closed_flow<=1e-7
    if exact_tank_balance:passed=passed and tank_balance<=.02
    return dict(kind='physical_checks',passed=bool(passed),mass_balance_max_abs_m3s=continuity,mass_balance_tolerance_m3s=2e-6,
        leak_law_max_relative_error=leak_error,leak_law_tolerance=1e-4,inactive_leak_max_m3s=inactive,
        pump_curve_max_fit_error_m=pump_fit,pump_curve_fit_tolerance_m=1e-4,
        pipe_energy_max_abs_error_m=energy_error,pipe_energy_tolerance_m=.05,pipe_energy_samples=energy_count,
        pump_operating_head_max_abs_error_m=pump_operating,pump_operating_tolerance_m=.05,
        pdd_max_ratio_error=pdd_error,pdd_max_abs_error_m3s=pdd_abs,pdd_scaled_error=pdd_scaled,
        pdd_relative_tolerance=1e-4,pdd_absolute_tolerance_m3s=1e-7,pdd_assessed_samples=pdd_count,pdd_smoothing_samples_excluded=pdd_smoothed,
        tank_bounds_max_violation_m=tank_bounds,tank_balance_max_abs_m3=tank_balance,
        tank_balance_assessment='event-resolved piecewise storage balance' if exact_tank_balance else 'sampled diagnostic only; intervening controls may change inflow',
        tank_balance_gate_enabled=exact_tank_balance,closed_link_max_flow_m3s=closed_flow,
        interpretation='Numerical equation consistency within an extended-period incompressible model; no field calibration or transient validation.')


def engineering_summary(wn,raw,mapping,times,expected,task,solver_calls):
    users=mapping['original_junction_ids'];start=task['time_window']['evaluation_start_s']
    constraints=task['hard_constraints']
    extrema={name:dict(node_id=name,min_pressure_m=None,min_pressure_time_s=None,min_supply_ratio=None,min_supply_time_s=None,
        pressure_violation_duration_s=0,supply_violation_duration_s=0) for name in constraints['node_ids']}
    shortage=full=served=leakage=electrical=hydraulic=0.0
    min_pressure=None;velocity=0.0;negative=set();curve_extrapolated=set();laminar=set()
    density=1000*wn.options.hydraulic.specific_gravity
    for i,t in enumerate(times[:-1]):
        dt=times[i+1]-t
        if t<start:continue
        for name in users:
            d=float(expected.at[t,name]);q=float(raw.node['demand'].at[t,name]);p=float(raw.node['pressure'].at[t,name])
            full+=d*dt;served+=min(max(q,0),d)*dt;shortage+=max(d-q,0)*dt
            min_pressure=p if min_pressure is None else min(min_pressure,p)
            if p<-.1:negative.add(name)
            if name in extrema:
                item=extrema[name]
                if item['min_pressure_m'] is None or p<item['min_pressure_m']:
                    item['min_pressure_m'],item['min_pressure_time_s']=p,int(t)
                if p+constraints['pressure_tolerance_m']<constraints['min_pressure_m']:item['pressure_violation_duration_s']+=dt
                ratio=None if d==0 else max(q,0)/d
                if ratio is not None:
                    if item['min_supply_ratio'] is None or ratio<item['min_supply_ratio']:
                        item['min_supply_ratio'],item['min_supply_time_s']=ratio,int(t)
                    if ratio+constraints['ratio_tolerance']<constraints['min_supply_ratio']:item['supply_violation_duration_s']+=dt
        if 'leak_demand' in raw.node:leakage+=max(float(raw.node['leak_demand'].loc[t].sum()),0)*dt
        for name,pipe in wn.pipes():
            q=abs(float(raw.link['flowrate'].at[t,name]));v=q/(math.pi*pipe.diameter**2/4)
            velocity=max(velocity,v)
            # Reference-water screening only; temperature and absolute roughness are not measured.
            if int(raw.link['status'].at[t,name])!=0 and q>1e-7 and v*pipe.diameter/1e-6<2000:laminar.add(name)
        for name,pump in wn.pumps():
            q=float(raw.link['flowrate'].at[t,name])
            if int(raw.link['status'].at[t,name])==0 or q<=0:continue
            gain=float(raw.node['head'].at[t,pump.end_node_name]-raw.node['head'].at[t,pump.start_node_name])
            power=max(density*G*q*gain,0)
            efficiency=wn.options.energy.global_efficiency/100
            if pump.efficiency_curve is not None:
                points=pump.efficiency_curve.points
                efficiency=float(np.interp(q,[x for x,_ in points],[y for _,y in points]))/100
            hydraulic+=power*dt/3_600_000
            if 0<efficiency<=1:electrical+=power/efficiency*dt/3_600_000
            curve=wn.get_curve(pump.pump_curve_name).points
            if q>max(x for x,_ in curve):curve_extrapolated.add(name)
    return dict(mode='benchmark',model='extended-period incompressible Hazen-Williams + Wagner PDD + fixed-area atmospheric orifices',
        internal_samples=len(times),public_samples=len(range(0,task['time_window']['end_s'],task['time_window']['step_s'])),
        time_resolution='all WNTR accepted hydraulic/control events' if task['schema_version']=='0.4.0' and task['hydraulic_options']['engine']=='wntr' else 'regular engine report samples',
        dense_metrics=dict(shortage_m3=shortage,supply_ratio=None if full==0 else served/full,min_pressure_m=min_pressure,leakage_m3=leakage),
        dense_constraint_diagnostics=dict(sampling='Accepted internal interval start, held to next event; not continuous transient extrema',
            thresholds=constraints,node_extrema=list(extrema.values()),
            violation_node_ids=sorted(n for n,v in extrema.items() if v['pressure_violation_duration_s'] or v['supply_violation_duration_s']),
            interpretation='Measurements against frozen thresholds; coarse public-series verification alone is insufficient for dense-time admission.'),
        pump_energy=dict(hydraulic_kwh=hydraulic,estimated_electrical_kwh=electrical,efficiency_provenance='Source efficiency curves/global efficiency; not measured electrical consumption',density_kg_m3=density),
        operating_diagnostics=dict(max_pipe_velocity_m_s=velocity,negative_pressure_user_nodes=sorted(negative),pump_curve_extrapolation_ids=sorted(curve_extrapolated),
            possible_laminar_hw_links=sorted(laminar),reynolds_screening_kinematic_viscosity_m2_s=1e-6),
        solver=dict(algorithm='WNTR sparse Newton with backtracking' if solver_calls else 'EPANET Global Gradient Algorithm',
            calls=len(solver_calls),total_iterations=sum(c['iterations'] or 0 for c in solver_calls),max_final_residual=max((c['final_residual_inf'] for c in solver_calls),default=None),
            solve_elapsed_s=sum(c['elapsed_s'] for c in solver_calls),records=solver_calls),
        physical_parameters=task.get('physical_parameters'),
        omitted_physics=['water hammer/compressibility and entrained air','cavitation/NPSH and contamination intrusion','pressure-dependent crack area and soil/backpressure','real valve locations/stroking and actuator delays','field-calibrated demands/roughness/efficiency'])
