import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from jsonschema.exceptions import ValidationError

from services.hydraulics.adapter import EpanetAdapter, network_assets
from services.hydraulics.datasets import NETWORKS, verify_sources
from services.hydraulics.demo import make_case, run
from services.hydraulics.server import make_server
from waterguard_contracts.core import file_hash, load, plan_hash, validate


class HydraulicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            cls.index=run(cls.root)
        cls.cases={(c['network_id'],c['case']):c for c in cls.index['cases']}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def result(self,network,case):
        return load(self.root/self.cases[(network,case)]['result_ref'])

    def test_public_sources_contracts_and_real_convergence(self):
        verify_sources()
        for case in self.index['cases']:
            result=load(self.root/case['result_ref'])
            validate('SimulationResult',result)
            self.assertTrue(result['diagnostics']['converged'],result['diagnostics'])
            self.assertEqual(result['mode'],'benchmark')
            self.assertEqual(len(result['series']['frames']),12)
            self.assertEqual(case['admission']['recommendation_eligible'],'fail')
        task,_,_,_=make_case('epa-net1')
        task['schema_version']='0.1.0'
        with self.assertRaises(ValidationError):
            validate('TaskSpec',task)  # A 0.1 caller must not silently accept a null fault.

    def test_identical_prewarm_history_and_fault_mapping(self):
        for network in NETWORKS:
            normal=self.result(network,'normal')['series']['frames']
            closed=self.result(network,'closure')['series']['frames']
            self.assertEqual(normal[:4],closed[:4])
            link=self.cases[(network,'closure')]['fault_link_id']
            for frame in closed[4:]:
                fault=next(l for l in frame['links'] if l['link_id']==link)
                self.assertEqual(fault['status'],'closed')
                self.assertLess(abs(fault['flow_m3s']),1e-8)

    def test_junction_mass_balance_and_demand_separation(self):
        for case in self.index['cases']:
            scene=load(self.root/case['simulation_id']/'scene.json')
            topology={link['id']:link for link in scene['links']}
            for frame in self.result(case['network_id'],case['case'])['series']['frames']:
                balances={n['node_id']:-n['delivered_demand_m3s'] for n in frame['nodes']}
                for link in frame['links']:
                    endpoints=topology[link['link_id']]
                    if endpoints['from_node'] in balances:
                        balances[endpoints['from_node']]-=link['flow_m3s']
                    if endpoints['to_node'] in balances:
                        balances[endpoints['to_node']]+=link['flow_m3s']
                self.assertLess(max(abs(v) for v in balances.values()),2e-6)
                self.assertTrue(all(n['leak_flow_m3s']==0 for n in frame['nodes']))

    def test_repeat_run_independent_state_and_source_bytes(self):
        task,plan,_,_=make_case('epa-net1')
        result=EpanetAdapter(self.root).simulate(task,plan)
        baseline=self.result('epa-net1','normal')
        self.assertNotEqual(result['artifact']['simulation_id'],baseline['artifact']['simulation_id'])
        self.assertEqual(result['series']['frames'],baseline['series']['frames'])
        verify_sources()

    def test_timed_action_is_compiled_without_changing_original_controls(self):
        task,plan,_,_=make_case('epa-net1')
        plan['actions']=[dict(kind='link_close',link_id='12',simulation_time_s=3600)]
        plan['plan_hash']=plan_hash(plan)
        result=EpanetAdapter(self.root).simulate(task,plan)
        self.assertEqual(result['series']['frames'],self.result('epa-net1','closure')['series']['frames'])

    def test_source_control_target_and_stale_input_rejected(self):
        task,plan,_,_=make_case('epa-net1')
        plan['actions']=[dict(kind='pump_off',link_id='9',simulation_time_s=3600)]
        plan['plan_hash']=plan_hash(plan)
        with self.assertRaises(ValueError): EpanetAdapter(self.root).simulate(task,plan)
        task['revision']=2
        with self.assertRaisesRegex(ValueError,'hash'): EpanetAdapter(self.root).simulate(task,plan)

    def test_engine_failure_retains_diagnostics_without_success(self):
        task,plan,_,_=make_case('epa-net1')
        with patch('wntr.sim.EpanetSimulator.run_sim',side_effect=RuntimeError('engine failed')):
            result=EpanetAdapter(self.root).simulate(task,plan)
        self.assertEqual(result['artifact']['status'],'FAILED')
        self.assertIsNone(result['series'])
        self.assertFalse(result['diagnostics']['converged'])
        self.assertTrue((self.root/result['artifact']['simulation_id']/'diagnostics.json').exists())

    def test_raw_evidence_and_file_hashes(self):
        for case in self.index['cases']:
            folder=self.root/case['simulation_id']
            evidence=load(folder/'evidence-manifest.json')
            self.assertIn('engine.inp',evidence['hashes'])
            self.assertIn('engine.rpt',evidence['hashes'])
            self.assertIn('raw-node-pressure.csv',evidence['hashes'])
            for ref,expected in evidence['hashes'].items():
                self.assertEqual(file_hash(folder/ref),expected)

    def test_read_api_reports_benchmark_and_serves_actual_series(self):
        server=make_server(self.root,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        sid=self.cases[('epa-net3','closure')]['simulation_id']
        try:
            for path in ['/api/networks','/api/networks/epa-net3/scene','/api/networks/epa-net3/actions','/api/benchmarks/current',f'/api/simulations/{sid}',f'/api/simulations/{sid}/verification',f'/api/simulations/{sid}/evidence']:
                with urlopen(base+path,timeout=5) as response:
                    self.assertEqual(response.headers['X-WaterGuard-Mode'],'benchmark')
                    json.load(response)
            with urlopen(base+f'/api/simulations/{sid}/series?node_id=123&start_s=3600&end_s=4500',timeout=5) as response:
                page=json.load(response)
                self.assertEqual(len(page['frames']),1)
                self.assertEqual(page['frames'][0]['nodes'][0]['node_id'],'123')
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base+'/api/tasks',method='POST',data=b'{}'),timeout=5)
            self.assertEqual(caught.exception.code,405)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__=='__main__':
    unittest.main()
