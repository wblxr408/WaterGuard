"""Versioned physical mappings. No candidate selection or business decisions."""
from __future__ import annotations

import math
import networkx as nx
import wntr

from .adapter import network_assets, write
from .datasets import DATA, NETWORKS, ROOT, manifest
from waterguard_contracts.core import check_plan, check_task, file_hash, load, require, validate

VERSION = '0.3.0'
BRIDGE_LENGTH_M = 1e-6


def source_controls(wn):
    targets = {}
    for name, control in wn.controls():
        for action in control.actions():
            target, _ = action.target()
            if hasattr(target, 'link_type'):
                targets.setdefault(target.name, []).append(name)
    return {key: sorted(set(value)) for key, value in targets.items()}


def assets(network_id):
    wn, source, scene, _ = network_assets(network_id)
    scene['schema_version'] = VERSION
    native = source_controls(wn)
    facilities = []
    for name, link in wn.links():
        kinds = []
        if link.link_type == 'Pipe' and not link.check_valve:
            kinds = ['link_open', 'link_close']
        elif link.link_type == 'Pump':
            require(link.pump_type == 'HEAD' and link.base_speed == 1, 'Only fixed-speed head-curve pumps are supported')
            curve = wn.get_curve(link.pump_curve_name)
            require(len(curve.points) >= 1 and all(q >= 0 and h > 0 for q, h in curve.points), 'Invalid pump curve')
            kinds = ['pump_on', 'pump_off']
        if kinds:
            facilities.append(dict(link_id=name, kind=link.link_type.lower(), allowed_actions=kinds,
                initial_status='closed' if int(link.initial_status) == 0 else 'open', action_cost=1,
                mapping='Timed fixed status; source controls require an exact task-level override acknowledgment.',
                native_control_names=native.get(name, []), requires_explicit_override=name in native))
    catalog = dict(schema_version=VERSION, catalog_id=network_id+'-operations-v3', network_id=network_id, facilities=facilities)
    validate('ActionCatalog', catalog)
    return wn, source, scene, catalog


def validate_perturbation(task, plan, perturbation):
    if perturbation is None:
        return
    require(isinstance(perturbation, dict) and set(perturbation) == {'perturbation_id', 'candidate_id', 'parameters', 'required_labels'}, 'Invalid perturbation record')
    require(perturbation['candidate_id'] == plan['candidate_id'], 'Perturbation candidate mismatch')
    require(isinstance(perturbation['perturbation_id'], str) and perturbation['perturbation_id'], 'Invalid perturbation identifier')
    require(isinstance(perturbation['parameters'], dict) and set(perturbation['parameters']) == {'demand_multiplier'}, 'Unsupported perturbation parameters')
    require(perturbation['parameters']['demand_multiplier'] in task['perturbation_domain']['demand_multipliers'], 'Perturbation demand multiplier outside declared domain')
    require(perturbation['required_labels'] == task['perturbation_domain']['required'], 'Perturbation labels do not match task domain')


def compile_task(task, plan, perturbation=None):
    wn, source, scene, catalog = assets(task['network_ref']['network_id'])
    check_task(task, catalog, scene)
    check_plan(task, plan, catalog)
    require(task['schema_version'] == plan['schema_version'] and task['schema_version'] in ('0.3.0','0.4.0'), 'Physical adapter requires matching task and plan 0.3/0.4')
    require(task['network_ref'] == dict(network_id=source['network_id'], revision=source['revision'], sha256=source['sha256'], overlay_hash=source['overlays'][0]['sha256']), 'Network/overlay binding mismatch')
    catalog_ref = f'data/catalogs/{source["network_id"]}-v3.json'
    require(task['action_catalog_ref'] == catalog_ref and load(ROOT/catalog_ref) == catalog, 'Operation catalog mismatch')
    require(task['action_catalog_hash'] == file_hash(ROOT/catalog_ref), 'Operation catalog hash mismatch')
    w, options = task['time_window'], task['hydraulic_options']
    require(w['start_s'] == 0 and w['end_s'] <= 7*86400 and w['step_s'] >= 60, 'Supported horizon: start 0, at most 7 days, report step >=60s')
    require(w['step_s'] % options['hydraulic_timestep_s'] == 0, 'Hydraulic step must divide report step')
    require(options['minimum_pressure_m'] < options['required_pressure_m'] and options['required_pressure_m'] >= 0.07, 'Invalid PDD pressure range')
    require(0 < options['pressure_exponent'] <= 1, 'PDD exponent outside supported (0,1]')
    require(options['demand_multiplier'] in task['perturbation_domain']['demand_multipliers'], 'Demand multiplier outside declared domain')
    validate_perturbation(task, plan, perturbation)
    fault = task['fault']
    if fault:
        extra=set(fault)-{'kind','link_id','start_s','end_s','mapping'}
        expected_extra={'closure':set(),'isolation':{'boundary_link_ids'},'leak':{'location_fraction','leak_area_m2','discharge_coefficient'},'rupture':{'location_fraction','discharge_coefficient'}}[fault['kind']]
        require(extra==expected_extra,'Fault parameters do not match the selected physical model')
        require(all(fault[t] % w['step_s'] == 0 for t in ('start_s','end_s')), 'Fault times must be reporting boundaries')
        pipe = wn.get_link(fault['link_id'])
        require(pipe.link_type == 'Pipe' and not pipe.check_valve, 'Fault requires an ordinary pipe')
        require(int(pipe.initial_status) == 1, 'Fault target must initially be open')
        if fault['kind'] in ('leak', 'rupture'):
            require(options['engine'] == 'wntr', 'Leaks and ruptures require WNTRSimulator; EPANET substitution is forbidden')
    native = source_controls(wn)
    removed = []
    used_overrides = set()
    touched = {a['link_id'] for a in plan['actions']}
    if fault:
        touched.update(fault.get('boundary_link_ids', [fault['link_id']]))
    for override in task['control_policy']['overrides']:
        link_id = override['link_id']
        require(link_id not in used_overrides, 'Duplicate control override')
        used_overrides.add(link_id)
        require(override['control_names'] == native.get(link_id), 'Override must name the exact sorted native controls')
        for name in override['control_names']:
            # Never remove a multi-target control by acknowledging only one target.
            require(all(key == link_id for key, names in native.items() if name in names), 'Override would affect another facility')
            wn.remove_control(name)
            removed.append(name)
    require(all(link not in native or link in used_overrides for link in touched), 'Native control conflict; explicit task.control_policy override required')
    wn.options.time.duration = w['end_s']
    wn.options.time.hydraulic_timestep = options['hydraulic_timestep_s']
    wn.options.time.report_timestep = w['step_s']
    wn.options.time.report_start = 0
    wn.options.quality.parameter = 'NONE'
    wn.options.hydraulic.demand_model = 'PDD'
    wn.options.hydraulic.minimum_pressure = options['minimum_pressure_m']
    wn.options.hydraulic.required_pressure = options['required_pressure_m']
    wn.options.hydraulic.pressure_exponent = options['pressure_exponent']
    # A multiplier is a complete scenario parameter, not an extra hidden mutation.
    wn.options.hydraulic.demand_multiplier *= options['demand_multiplier'] if perturbation is None else perturbation['parameters']['demand_multiplier']
    physics=task.get('physical_parameters')
    if physics is not None:
        require(task['schema_version']=='0.4.0','Physical parameters require contracts 0.4.0')
        if options['engine']=='epanet':
            from wntr.epanet.util import FlowUnits,HydParam,from_si
            units=FlowUnits[wn.options.hydraulic.inpfile_units]
            wn.options.hydraulic.accuracy=1e-5
            # WNTR 1.5 writes these two EPANET option values in native INP units.
            wn.options.hydraulic.headerror=float(from_si(units,.001,HydParam.HydraulicHead))
            wn.options.hydraulic.flowchange=float(from_si(units,1e-7,HydParam.Flow))
            wn.options.hydraulic.trials=200
        else:
            require(options['pressure_exponent']==.5,'Validated WNTR 1.5 PDD smoothing requires exponent 0.5')
        for _,p in wn.pipes():
            p.roughness*=physics['roughness_multiplier']
        for _,r in wn.reservoirs():
            r.base_head+=physics['reservoir_head_offset_m']
        for _,tank in wn.tanks():
            level=tank.init_level+physics['tank_level_offset_m']
            require(tank.min_level<=level<=tank.max_level,'Perturbed initial tank level outside physical bounds')
            tank.init_level=level
        require(set(physics['node_demand_multipliers'])<=set(wn.junction_name_list),'Unknown demand node')
        for name,multiplier in physics['node_demand_multipliers'].items():
            for demand in wn.get_node(name).demand_timeseries_list:
                demand.base_value*=multiplier
        require(not physics['node_pdd'] or options['engine']=='wntr','Per-node PDD requires WNTR; EPANET uses global parameters')
        require(set(physics['node_pdd'])<=set(wn.junction_name_list),'Unknown PDD node')
        for name,settings in physics['node_pdd'].items():
            require(settings['minimum_pressure_m']<settings['required_pressure_m'],'Invalid local PDD pressure range')
            require(settings['pressure_exponent']==.5,'Validated local PDD smoothing requires exponent 0.5')
            node=wn.get_node(name)
            node.minimum_pressure=settings['minimum_pressure_m']
            node.required_pressure=settings['required_pressure_m']
            node.pressure_exponent=settings['pressure_exponent']
        require(not physics['background_leaks'] or options['engine']=='wntr','Background leaks require WNTR')
    from wntr.network.controls import Control, ControlAction, SimTimeCondition
    from wntr.network import LinkStatus

    def at(name, link, time, state):
        require(name not in wn.control_name_list, 'Generated control name collision')
        wn.add_control(name, Control(SimTimeCondition(wn, '=', time), ControlAction(link, 'status', state)))

    def close_window(name, link):
        initial = link.initial_status
        at(name+'_start', link, fault['start_s'], LinkStatus.Closed)
        at(name+'_end', link, fault['end_s'], initial)

    mapping = dict(fault_window=None if fault is None else [fault['start_s'],fault['end_s']],original_junction_ids=list(wn.junction_name_list), original_link_ids=list(wn.link_name_list),
        segments={name:[name] for name in wn.link_name_list}, leaks=[],leak_windows={}, bridge=None, isolated_node_ids=[],
        removed_native_controls=sorted(removed), source_controls=native,
        flow_convention='flow_m3s is inflow at original start; downstream_flow_m3s is outflow at original end. They differ when a pipe leaks.',
        closure_semantics='Close an existing pipe; no leak discharge.',
        rupture_semantics='Two separated pipe ends, each with a full-cross-section atmospheric orifice; virtual bridge open before/after failure, closed during failure. No water-hammer model.')
    if fault:
        pipe = wn.get_link(fault['link_id'])
        if fault['kind'] == 'closure':
            close_window('wg_closure', pipe)
        elif fault['kind'] == 'isolation':
            boundary = set(fault['boundary_link_ids'])
            require(fault['link_id'] not in boundary, 'Isolation target must be inside, not on the boundary')
            require(boundary <= {name for name,p in wn.pipes() if not p.check_valve}, 'Isolation boundaries must be ordinary pipes')
            graph = nx.Graph()
            graph.add_nodes_from(wn.node_name_list)
            graph.add_edges_from((p.start_node_name,p.end_node_name) for name,p in wn.links() if name not in boundary)
            component = nx.node_connected_component(graph, pipe.start_node_name)
            require(pipe.end_node_name in component and not component.intersection(wn.reservoir_name_list+wn.tank_name_list), 'Boundary does not isolate the target from every source/tank')
            expected_boundary = {name for name,p in wn.links() if (p.start_node_name in component) != (p.end_node_name in component)}
            require(boundary == expected_boundary, 'Boundary must be the exact cut around the target component')
            mapping['isolated_node_ids'] = sorted(component)
            for i,name in enumerate(sorted(boundary)):
                close_window(f'wg_isolate_{i}', wn.get_link(name))
        else:
            fraction = fault['location_fraction']
            diameter, minor = pipe.diameter, pipe.minor_loss
            area = fault['leak_area_m2'] if fault['kind']=='leak' else math.pi*diameter**2/4
            require(area <= math.pi*diameter**2/4, 'Leak area exceeds pipe cross-section; select rupture model')
            names = ['wg_fault_left','wg_fault_right','wg_fault_tail','wg_fault_bridge']
            require(not set(names).intersection(wn.node_name_list+wn.link_name_list), 'Fault entity ID collision')
            if fault['kind']=='leak':
                wntr.morph.split_pipe(wn,pipe.name,'wg_fault_tail','wg_fault_left',split_at_point=fraction,return_copy=False)
                leak_nodes = ['wg_fault_left']
            else:
                wntr.morph.break_pipe(wn,pipe.name,'wg_fault_tail','wg_fault_left','wg_fault_right',split_at_point=fraction,return_copy=False)
                wn.add_pipe('wg_fault_bridge','wg_fault_left','wg_fault_right',length=BRIDGE_LENGTH_M,diameter=diameter,roughness=pipe.roughness,minor_loss=0,initial_status='OPEN')
                close_window('wg_rupture',wn.get_link('wg_fault_bridge'))
                mapping['bridge'] = dict(link_id='wg_fault_bridge',length_m=BRIDGE_LENGTH_M)
                leak_nodes = ['wg_fault_left','wg_fault_right']
            # Morph duplicates the minor loss; allocate it so pre-fault loss stays unchanged.
            wn.get_link(pipe.name).minor_loss = minor*fraction
            wn.get_link('wg_fault_tail').minor_loss = minor*(1-fraction)
            mapping['segments'][pipe.name] = [pipe.name,'wg_fault_tail']
            for node_id in leak_nodes:
                wn.get_node(node_id).add_leak(wn,area=area,discharge_coeff=fault['discharge_coefficient'],start_time=fault['start_s'],end_time=fault['end_s'])
                mapping['leaks'].append(dict(node_id=node_id,source_link_id=pipe.name,area_m2=area,discharge_coefficient=fault['discharge_coefficient']))
                mapping['leak_windows'][node_id]=[fault['start_s'],fault['end_s']]
    if physics:
        for spec in physics['background_leaks']:
            name=spec['node_id']
            require(name in mapping['original_junction_ids'],'Background leak requires an original junction')
            require(name not in mapping['leak_windows'],'Duplicate leakage node')
            node=wn.get_node(name)
            node.add_leak(wn,area=spec['area_m2'],discharge_coeff=spec['discharge_coefficient'],start_time=0,end_time=w['end_s'])
            mapping['leaks'].append(dict(node_id=name,source_link_id='node:'+name,area_m2=spec['area_m2'],discharge_coefficient=spec['discharge_coefficient']))
            mapping['leak_windows'][name]=[0,w['end_s']]
    mapping['physical_parameters']=physics
    mapping['perturbation']=perturbation
    for i,action in enumerate(sorted(plan['actions'],key=lambda a:(a['simulation_time_s'],a['link_id']))):
        link=wn.get_link(action['link_id'])
        require((action['kind'].startswith('pump_')) == (link.link_type=='Pump'), 'Action/physical facility kind mismatch')
        at(f'wg_action_{i}',link,action['simulation_time_s'],LinkStatus.Open if action['kind'] in ('link_open','pump_on') else LinkStatus.Closed)
    mapping['compiled_links'] = [dict(link_id=name,from_node=p.start_node_name,to_node=p.end_node_name) for name,p in wn.links()]
    mapping['compiled_junction_ids'] = list(wn.junction_name_list)
    return wn,source,scene,catalog,mapping
