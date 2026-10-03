"""Model adequacy checks against physical equations and parameter response."""
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import pandas as pd

from services.hydraulics.compiler import compile_task
from services.hydraulics.physical import assess,engineering_summary
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.scenarios import physical_case,rebind
from waterguard_contracts.core import load


class ModelQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);cls.adapter=PhysicalAdapter(cls.root)
        cls.runs={}
        for label,network,kind,engine in [('normal','epa-net1','normal','wntr'),('rupture','epa-net3','rupture','wntr'),('epanet','epa-net3','normal','epanet')]:
            task,plan=physical_case(network,kind,engine)
            result=cls.adapter.simulate(task,plan)
            if result['artifact']['status']!='SUCCEEDED':raise AssertionError(result['diagnostics'])
            cls.runs[label]=(task,plan,result,cls.root/result['artifact']['simulation_id'])

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def raw(self,label):
        folder=self.runs[label][3]
        raw=SimpleNamespace(node={},link={})
        for category in ('node','link'):
            for file in folder.glob('raw-'+category+'-*.csv'):
                getattr(raw,category)[file.stem[len('raw-'+category+'-'):]]=pd.read_csv(file,index_col='time_s')
        return raw,pd.read_csv(folder/'expected-demand.csv',index_col='time_s')

    def test_event_resolution_energy_pdd_storage_and_solver_residual(self):
        for label in ('normal','rupture','epanet'):
            _,_,result,folder=self.runs[label]
            checks=load(folder/'physical-checks.json');analysis=load(folder/'hydraulic-analysis.json')
            self.assertTrue(checks['passed'])
            self.assertGreater(analysis['internal_samples'],analysis['public_samples'])
            self.assertLess(checks['pipe_energy_max_abs_error_m'],.005)
            self.assertLess(checks['pump_operating_head_max_abs_error_m'],.001)
            self.assertLess(checks['pdd_scaled_error'],1)
            if label!='epanet':
                self.assertLess(checks['tank_balance_max_abs_m3'],.02)
                self.assertLess(analysis['solver']['max_final_residual'],1e-8)
                self.assertTrue(checks['tank_balance_gate_enabled'])
            else:self.assertFalse(checks['tank_balance_gate_enabled'])

    def test_energy_and_storage_checks_detect_corrupted_physics(self):
        task,plan,_,folder=self.runs['normal']
        wn,_,_,_,mapping=compile_task(task,plan)
        raw,expected=self.raw('normal');times=list(raw.node['head'].index)
        raw.node['head'].at[times[0],'13']+=1
        checks=assess(wn,raw,mapping,times,expected,exact_tank_balance=True)
        self.assertFalse(checks['passed']);self.assertGreater(checks['pipe_energy_max_abs_error_m'],.5)
        raw,expected=self.raw('normal')
        tank=wn.tank_name_list[0]
        raw.node['head'].at[times[1],tank]+=.05
        checks=assess(wn,raw,mapping,times,expected,exact_tank_balance=True)
        self.assertFalse(checks['passed']);self.assertGreater(checks['tank_balance_max_abs_m3'],.02)

    def test_internal_low_pressure_is_located_between_public_samples(self):
        task,plan,_,folder=self.runs['normal'];wn,_,_,_,mapping=compile_task(task,plan)
        raw,expected=self.raw('normal');times=list(raw.node['head'].index)
        t=next(t for t in times if t>=3600 and t%900!=0)
        raw.node['pressure'].at[t,'13']=-1
        analysis=engineering_summary(wn,raw,mapping,times,expected,task,[])
        self.assertIn('13',analysis['dense_constraint_diagnostics']['violation_node_ids'])
        node=next(n for n in analysis['dense_constraint_diagnostics']['node_extrema'] if n['node_id']=='13')
        self.assertEqual(node['min_pressure_time_s'],t)
        self.assertGreater(node['pressure_violation_duration_s'],0)

    def test_declared_roughness_source_and_storage_parameters_compile(self):
        task,plan=physical_case('epa-net1','normal')
        base,*_=compile_task(task,plan)
        task['physical_parameters'].update(roughness_multiplier=.8,reservoir_head_offset_m=-2,tank_level_offset_m=-.2,node_demand_multipliers={'13':1.2})
        rebind(task,plan);wn,*_=compile_task(task,plan)
        self.assertAlmostEqual(wn.get_link('12').roughness,base.get_link('12').roughness*.8)
        self.assertAlmostEqual(wn.get_node('9').base_head,base.get_node('9').base_head-2)
        self.assertAlmostEqual(wn.get_node('2').init_level,base.get_node('2').init_level-.2)
        self.assertAlmostEqual(wn.get_node('13').base_demand,base.get_node('13').base_demand*1.2)

    def test_background_leak_and_local_demand_are_not_counted_as_supply(self):
        task,plan=physical_case('epa-net1','normal')
        task['physical_parameters']['background_leaks']=[dict(node_id='13',area_m2=1e-5,discharge_coefficient=.75)]
        task['physical_parameters']['node_demand_multipliers']={'13':1.2}
        rebind(task,plan);result=self.adapter.simulate(task,plan)
        self.assertEqual(result['artifact']['status'],'SUCCEEDED',result['diagnostics'])
        frame=result['series']['frames'][0];node=next(n for n in frame['nodes'] if n['node_id']=='13')
        base=next(n for n in self.runs['normal'][2]['series']['frames'][0]['nodes'] if n['node_id']=='13')
        self.assertAlmostEqual(node['expected_demand_m3s'],base['expected_demand_m3s']*1.2)
        self.assertGreater(node['leak_flow_m3s'],0)
        self.assertAlmostEqual(node['delivered_demand_m3s'],node['expected_demand_m3s'],delta=1e-8)
        self.assertEqual(frame['leaks'][0]['node_id'],'13')

    def test_local_service_pressure_changes_actual_pdd_delivery(self):
        task,plan=physical_case('epa-net1','normal')
        task['physical_parameters']['node_pdd']={'13':dict(minimum_pressure_m=0,required_pressure_m=140,pressure_exponent=.5)}
        rebind(task,plan);result=self.adapter.simulate(task,plan)
        self.assertEqual(result['artifact']['status'],'SUCCEEDED',result['diagnostics'])
        node=next(n for n in result['series']['frames'][0]['nodes'] if n['node_id']=='13')
        self.assertLess(node['delivered_demand_m3s'],node['expected_demand_m3s'])
        self.assertAlmostEqual(node['delivered_demand_m3s']/node['expected_demand_m3s'],(node['pressure_m']/140)**.5,delta=1e-6)

    def test_unsupported_physics_is_explicitly_rejected(self):
        task,plan=physical_case(engine='epanet')
        task['fault']=None
        task['physical_parameters']['node_pdd']={'121':dict(minimum_pressure_m=0,required_pressure_m=30,pressure_exponent=.5)}
        rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'Per-node PDD'):compile_task(task,plan)
        task,plan=physical_case('epa-net1','normal')
        task['physical_parameters']['tank_level_offset_m']=10;rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'tank level'):compile_task(task,plan)
        task,plan=physical_case()
        task['hydraulic_options']['pressure_exponent']=.8;rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'smoothing'):compile_task(task,plan)


if __name__=='__main__':unittest.main()
