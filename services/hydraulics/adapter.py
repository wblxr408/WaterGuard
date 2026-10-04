from __future__ import annotations

import json
import math
import warnings
import importlib.metadata
import platform
from pathlib import Path
from uuid import uuid4

import wntr

from .datasets import DATA, NETWORKS, ROOT, manifest
from waterguard_contracts.core import (check_plan, check_task, file_hash, input_hash,
    load, require, task_ref, validate)

VERSION = '0.2.0'
ENGINE = f'WNTR/{wntr.__version__};EPANET/2.2'


def write(path, value):
    from .storage import write as atomic_write
    atomic_write(path,value)


def network_assets(network_id):
    """Topology and read-only catalog; layout coordinates never assert ground distance."""
    source = manifest(network_id)
    wn = wntr.network.WaterNetworkModel(str(DATA/NETWORKS[network_id]))
    nodes = []
    for name, node in wn.nodes():
        x, y = node.coordinates
        nodes.append(dict(id=name, kind=node.node_type.lower(), x_m=float(x), y_m=float(y),
                          elevation_m=float(node.base_head if node.node_type == 'Reservoir' else node.elevation), critical=False))
    links = [dict(id=name, from_node=link.start_node_name, to_node=link.end_node_name,
                  kind=link.link_type.lower(), initial_status='closed' if int(link.initial_status)==0 else 'open') for name,link in wn.links()]
    # User operation of pumps and any link targeted by source controls is not enabled.
    controlled = set()
    for _, control in wn.controls():
        for action in control.actions():
            target, _ = action.target()
            if hasattr(target, 'link_type'):
                controlled.add(target.name)
    facilities = [dict(link_id=name,kind='pipe',allowed_actions=['link_open','link_close'],initial_status='closed' if int(link.initial_status)==0 else 'open',action_cost=1,mapping='EPANET timed pipe-status control; no inferred valve or pump.') for name,link in wn.pipes() if name not in controlled and not link.check_valve]
    scene = dict(schema_version=VERSION,network_id=network_id,mode='benchmark',coordinate_system='schematic_layout_m',provenance='derived',nodes=nodes,links=links,
                 display_coordinate_note='Upstream drawing units are unspecified. Original coordinates map 1:1 into arbitrary layout metres only; these are not ground distances, CRS coordinates or pipe lengths. Elevations are converted by WNTR to physical metres.')
    catalog = dict(schema_version=VERSION,catalog_id=network_id+'-pipes-v1',network_id=network_id,facilities=facilities)
    validate('NetworkScene',scene)
    validate('ActionCatalog',catalog)
    return wn, source, scene, catalog


class EpanetAdapter:
    """Fresh model and unique artifact directory per invocation. Benchmark execution only."""
    def __init__(self, output_root=ROOT/'artifacts/hydraulics'):
        self.output_root=Path(output_root)

    def simulate(self, task, plan):
        wn, source, scene, catalog = network_assets(task['network_ref']['network_id'])
        check_task(task, catalog, scene)
        check_plan(task, plan, catalog)
        require(task['schema_version']=='0.2.0', 'Real adapter requires contracts 0.2.0')
        require(task['network_ref']['revision']==source['revision'] and task['network_ref']['sha256']==source['sha256'], 'Source revision/hash mismatch')
        require(task['network_ref']['overlay_hash']==source['overlays'][0]['sha256'], 'Runtime overlay mismatch')
        catalog_path=ROOT/f'data/catalogs/{source["network_id"]}.json'
        require(load(catalog_path)==catalog, 'Stored catalog differs from source-derived capabilities')
        require(task['action_catalog_hash']==file_hash(catalog_path), 'Action catalog mismatch')
        settings=load(ROOT/source['overlays'][0]['ref'])
        w=task['time_window']
        require(w==settings['time_window'], 'Unsupported runtime time window')
        require(task['action_catalog_ref']==f'data/catalogs/{source["network_id"]}.json', 'Unknown action catalog reference')
        require(w['start_s']==0, 'EPANET initial state starts at t=0')
        wn.options.time.duration=w['end_s']
        wn.options.time.hydraulic_timestep=w['step_s']
        wn.options.time.report_timestep=w['step_s']
        wn.options.time.report_start=0
        wn.options.quality.parameter='NONE'
        wn.options.hydraulic.demand_model='PDD'
        wn.options.hydraulic.minimum_pressure=settings['pdd']['minimum_pressure_m']
        wn.options.hydraulic.required_pressure=settings['pdd']['required_pressure_m']
        wn.options.hydraulic.pressure_exponent=settings['pdd']['pressure_exponent']
        # Preserve upstream demand patterns, tanks, pumps and native controls.
        from wntr.network.controls import Control, ControlAction, SimTimeCondition
        from wntr.network import LinkStatus

        def status_control(name, link, time, status):
            wn.add_control(name, Control(SimTimeCondition(wn, '>=', time), ControlAction(link,'status',status)))

        fault=task['fault']
        if fault is not None:
            allowed={f['link_id'] for f in catalog['facilities']}
            require(fault['kind']=='closure' and fault['link_id'] in allowed,'Unsupported fault or source-control conflict')
            require(fault['end_s']==w['end_s'], 'Partial-window fault recovery is not implemented')
            require(fault['start_s'] % w['step_s']==0, 'Fault not on reporting boundary')
            status_control('wg_fault',wn.get_link(fault['link_id']),fault['start_s'],LinkStatus.Closed)
        for index,action in enumerate(plan['actions']):
            status_control(f'wg_action_{index}',wn.get_link(action['link_id']),action['simulation_time_s'],LinkStatus.Open if action['kind']=='link_open' else LinkStatus.Closed)

        simulation_id='sim-'+uuid4().hex
        output=self.output_root/simulation_id
        output.mkdir(parents=True,exist_ok=False)
        write(output/'task.json',task)
        write(output/'plan.json',plan)
        write(output/'network-manifest.json',source)
        write(output/'runtime-overlay.json',settings)
        write(output/'scene.json',scene)
        write(output/'action-catalog.json',catalog)
        (output/'source.inp').write_bytes((DATA/NETWORKS[source['network_id']]).read_bytes())
        (output/'LICENSE-WNTR.md').write_bytes((DATA/'LICENSE.md').read_bytes())
        write(output/'environment.json',dict(python=platform.python_version(),platform=platform.platform(),
              packages={name:importlib.metadata.version(name) for name in ('wntr','numpy','scipy','pandas','networkx')},
              engine=ENGINE,source_sha256=file_hash(output/'source.inp')))
        series=None
        errors=[]
        captured=[]
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                raw=wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=str(output/'engine'),version=2.2,convergence_error=True)
            captured=[str(w.message) for w in caught]
            require(raw.error_code in (None,0),f'EPANET error_code={raw.error_code}')
            # Preserve native results before checking/converting them, including t=end.
            for category, tables in [('node',raw.node),('link',raw.link)]:
                for field, table in tables.items():
                    table.to_csv(output/f'raw-{category}-{field}.csv',index_label='time_s',lineterminator='\n')
            expected=wntr.metrics.expected_demand(wn,start_time=0,end_time=w['end_s'],timestep=w['step_s'])
            frames=[]
            negative_roundoff=0
            for time in range(0,w['end_s'],w['step_s']):
                samples=[]
                for name in wn.junction_name_list:
                    d=float(expected.at[time,name])
                    q=float(raw.node['demand'].at[time,name])
                    p=float(raw.node['pressure'].at[time,name])
                    require(all(math.isfinite(v) for v in (d,q,p)), 'Non-finite hydraulic sample')
                    require(d>=0 and q>=-1e-9, 'Negative user demand beyond numerical tolerance')
                    if q<0:
                        negative_roundoff+=1
                        q=0.0
                    samples.append(dict(node_id=name,pressure_m=p,expected_demand_m3s=d,delivered_demand_m3s=q,leak_flow_m3s=0.0))
                link_samples=[]
                for name in wn.link_name_list:
                    flow=float(raw.link['flowrate'].at[time,name])
                    require(math.isfinite(flow),'Non-finite link flow')
                    status=int(raw.link['status'].at[time,name])
                    require(status in (0,1,2),'Unknown EPANET link status')
                    link_samples.append(dict(link_id=name,flow_m3s=flow,status='closed' if status==0 else 'open'))
                frames.append(dict(time_s=time,duration_s=w['step_s'],nodes=samples,links=link_samples))
            if negative_roundoff:
                captured.append(f'{negative_roundoff} delivered-demand values in [-1e-9,0) m3/s rounded to zero; original values preserved in raw-node-demand.csv.')
            # EPANET warnings can signal an invalid hydraulic solution despite an output file.
            report=(output/'engine.rpt').read_text(errors='replace')
            require('WARNING' not in report.upper(), 'EPANET report contains a hydraulic warning; inspect engine.rpt')
            series=dict(schema_version=VERSION,simulation_id=simulation_id,mode='benchmark',units=source['units'],sampling='interval_start_hold',frames=frames)
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
            series=None
        diag=dict(schema_version=VERSION,simulation_id=simulation_id,mode='benchmark',converged=not errors,errors=errors,warnings=captured,
                  provenance='Computed by WNTR EpanetSimulator with EPANET 2.2; raw CSV, compiled INP and engine report retained. No native AGH session.')
        artifact=dict(schema_version=VERSION,simulation_id=simulation_id,candidate_id=plan['candidate_id'],task_ref=task_ref(task),perturbation_id=None,mode='benchmark',engine_version=ENGINE,input_hash=input_hash(task,plan,ENGINE),status='FAILED' if errors else 'SUCCEEDED',series_ref='series.json' if series else None,diagnostics_ref='diagnostics.json',time_axis=w)
        result=dict(schema_version=VERSION,mode='benchmark',artifact=artifact,series=series,diagnostics=diag)
        validate('SimulationResult',result)
        if series:
            write(output/'series.json',series)
        write(output/'diagnostics.json',diag)
        write(output/'result.json',result)
        return result

    def get_series(self,simulation_id,node_id=None,start_s=None,end_s=None):
        from waterguard_contracts.core import resolve
        result=load(resolve(self.output_root,simulation_id+'/result.json'))
        require(result['artifact']['simulation_id']==simulation_id,'Simulation ID mismatch')
        series=result['series']
        require(series is not None,'Simulation has no valid series')
        known={n['node_id'] for f in series['frames'] for n in f['nodes']}
        require(node_id is None or node_id in known,'Unknown node')
        require(start_s is None or end_s is None or start_s<end_s,'Invalid series range')
        series['frames']=[f for f in series['frames'] if (start_s is None or f['time_s']>=start_s) and (end_s is None or f['time_s']<end_s)]
        if node_id is not None:
            for frame in series['frames']:
                frame['nodes']=[n for n in frame['nodes'] if n['node_id']==node_id]
        validate('SeriesPage',series)
        return series
