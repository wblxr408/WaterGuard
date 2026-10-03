"""Acceptance checks against physical invariants and cross-engine behavior."""
import contextlib
import copy
import io
import math
from pathlib import Path
import tempfile
import threading
import unittest

import pandas as pd

from services.hydraulics.acceptance import run
from services.hydraulics.compiler import compile_task
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.scenarios import case, rebind
from services.hydraulics.replay import replay, verify_evidence, compare_frames
from services.hydraulics.datasets import verify_sources
from waterguard_contracts.core import load, require, task_hash, validate


class PhysicalAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        cls.adapter=PhysicalAdapter(cls.root)
        with contextlib.redirect_stdout(io.StringIO()):
            cls.index=run(cls.root)
        cls.cases={(c['network_id'],c['case'],c['engine']):c for c in cls.index['cases']}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def folder(self,kind,network='epa-net1',engine='wntr'):
        return self.root/self.cases[(network,kind,engine)]['simulation_id']

    def result(self,kind,network='epa-net1',engine='wntr'):
        return load(self.folder(kind,network,engine)/'result.json')

    def test_all_22_engine_runs_are_real_and_physically_checked(self):
        self.assertEqual(len(self.index['cases']),22)
        verify_sources()
        for c in self.index['cases']:
            result=load(self.root/c['result_ref'])
            self.assertEqual(result['artifact']['status'],'SUCCEEDED',result['diagnostics'])
            self.assertTrue(c['physical_checks']['passed'])
            self.assertEqual(c['admission']['recommendation_eligible'],'fail')
            execution=load(self.root/c['simulation_id']/'execution.json')
            self.assertTrue(execution['process_isolated'])
            self.assertEqual(execution['worker_exit_code'],0)
            self.assertIsNotNone(execution['worker_pid'])

    def test_leak_law_discharge_is_separate_from_user_supply(self):
        for network in ('epa-net1','epa-net3'):
            frames=self.result('leak',network)['series']['frames']
            self.assertEqual(len(frames[4]['leaks']),1)
            for f in frames:
                leak=f['leaks'][0]
                q=leak['leak_flow_m3s']
                if 3600<=f['time_s']<7200:
                    expected=leak['discharge_coefficient']*leak['area_m2']*math.sqrt(2*9.81*max(leak['pressure_m'],0))
                    self.assertAlmostEqual(q,expected,delta=1e-7)
                    self.assertGreater(q,0)
                    link=next(l for l in f['links'] if l['link_id']==leak['source_link_id'])
                    self.assertAlmostEqual(link['flow_m3s']-link['downstream_flow_m3s'],q,delta=2e-6)
                else:
                    self.assertEqual(q,0)
                self.assertTrue(all(n['node_id']!=leak['node_id'] for n in f['nodes']))

    def test_rupture_disconnects_two_ends_and_leaks_at_both_ends(self):
        folder=self.folder('rupture')
        raw=pd.read_csv(folder/'raw-link-flowrate.csv',index_col='time_s')
        mapping=load(folder/'entity-map.json')
        bridge=mapping['bridge']['link_id']
        self.assertLess(raw.loc[3600:6300,bridge].abs().max(),1e-10)
        for f in self.result('rupture')['series']['frames'][4:8]:
            self.assertEqual(len(f['leaks']),2)
            self.assertTrue(all(n['leak_flow_m3s']>0 for n in f['leaks']))
            self.assertEqual(next(l for l in f['links'] if l['link_id']=='12')['status'],'broken')
        self.assertTrue((raw.loc[0:2700,bridge].abs()>0).all())
        self.assertTrue((raw.loc[7200:,bridge].abs()>0).all())

    def test_closure_and_isolation_have_no_invented_leaks(self):
        for network in ('epa-net1','epa-net3'):
            for kind in ('closure','isolation'):
                r=self.result(kind,network)
                self.assertTrue(all(f['leaks']==[] for f in r['series']['frames']))
            mapping=load(self.folder('isolation',network)/'entity-map.json')
            isolated=set(mapping['isolated_node_ids'])
            self.assertTrue(isolated)
            frames=self.result('isolation',network)['series']['frames']
            for f in frames[4:8]:
                users=[n for n in f['nodes'] if n['node_id'] in isolated]
                self.assertTrue(any(n['expected_demand_m3s']>0 for n in users))
                self.assertTrue(all(n['delivered_demand_m3s']==0 for n in users))
            self.assertTrue(any(n['delivered_demand_m3s']>0 for n in frames[8]['nodes'] if n['node_id'] in isolated))

    def test_prewarm_is_equivalent_and_tank_history_is_not_reset(self):
        for network in ('epa-net1','epa-net3'):
            normal=self.result('normal',network)['series']['frames']
            for kind in ('closure','isolation','leak','rupture'):
                frames=self.result(kind,network)['series']['frames']
                for a,b in zip(normal[:4],frames[:4]):
                    for x,y in zip(a['nodes'],b['nodes']):
                        self.assertAlmostEqual(x['pressure_m'],y['pressure_m'],delta=0.002)
                    for x,y in zip(a['tanks'],b['tanks']):
                        self.assertAlmostEqual(x['volume_m3'],y['volume_m3'],delta=0.02)
                # Restoring the pipe does not restore the original tank state.
                if kind=='leak':
                    self.assertGreater(max(abs(x['volume_m3']-y['volume_m3']) for x,y in zip(normal[8]['tanks'],frames[8]['tanks'])),0.01)

    def test_fixed_status_actions_and_restoration(self):
        for network,pump,pipe in [('epa-net1','9','12'),('epa-net3','335','123')]:
            for kind,link in [('pump-cycle',pump),('link-cycle',pipe)]:
                for engine in ('wntr','epanet'):
                    frames=self.result(kind,network,engine)['series']['frames']
                    for f in frames[4:8]:
                        q=next(l for l in f['links'] if l['link_id']==link)
                        self.assertEqual(q['status'],'closed')
                        self.assertLess(abs(q['flow_m3s']),1e-7)
                    self.assertEqual(next(l for l in frames[8]['links'] if l['link_id']==link)['status'],'open')

    def test_two_engines_agree_under_shared_supported_assumptions(self):
        differences=[]
        for network in ('epa-net1','epa-net3'):
            for kind in ('normal','closure','pump-cycle','link-cycle'):
                a=self.result(kind,network,'wntr')['series']['frames']
                b=self.result(kind,network,'epanet')['series']['frames']
                p=max(abs(x['pressure_m']-y['pressure_m']) for f,g in zip(a,b) for x,y in zip(f['nodes'],g['nodes']))
                q=max(abs(x['flow_m3s']-y['flow_m3s']) for f,g in zip(a,b) for x,y in zip(f['links'],g['links']))
                differences.append((network,kind,p,q))
                self.assertLess(p,0.5,(network,kind,p))
                self.assertLess(q,0.001,(network,kind,q))

    def test_evidence_is_complete_and_replay_recomputes_identical_numbers(self):
        for c in self.index['cases']:
            verify_evidence(self.root/c['simulation_id'])
        result=replay(self.folder('leak'),self.root/'replayed')
        self.assertTrue(result['numerically_equivalent'],result)
        self.assertTrue(result['input_hash_identical'])
        self.assertNotEqual(result['simulation_id'],result['source_simulation_id'])

    def test_exact_control_acknowledgment_required(self):
        task,plan=case(kind='pump-cycle')
        task['control_policy']['overrides']=[]
        rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'Native control conflict'):compile_task(task,plan)
        task,plan=case(kind='pump-cycle')
        task['control_policy']['overrides'][0]['control_names'].pop()
        rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'exact sorted'):compile_task(task,plan)

    def test_invalid_physical_parameters_and_engine_substitution_rejected(self):
        task,plan=case(kind='leak',engine='epanet')
        with self.assertRaisesRegex(ValueError,'require WNTR'):compile_task(task,plan)
        task,plan=case(kind='leak')
        task['fault']['leak_area_m2']=100
        rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'cross-section'):compile_task(task,plan)
        task,plan=case(kind='isolation')
        task['fault']['boundary_link_ids']=['121']
        rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'does not isolate'):compile_task(task,plan)

    def test_boundaries_missing_inputs_and_conflicting_controls(self):
        task,plan=case(kind='closure')
        task['fault']['start_s']=3601
        task['time_window']['evaluation_start_s']=4500
        task['action_limits']['earliest_s']=4500
        rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'reporting boundaries'):compile_task(task,plan)
        task,plan=case(kind='link-cycle')
        plan['actions'][1]['simulation_time_s']=3600
        rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'Conflicting'):compile_task(task,plan)
        task,plan=case()
        task['hydraulic_options']['minimum_pressure_m']=30
        rebind(task,plan)
        with self.assertRaisesRegex(ValueError,'PDD'):compile_task(task,plan)

    def test_timeout_and_cancellation_leave_diagnostics_and_stop_worker(self):
        task,plan=case()
        timeout=self.adapter.simulate(task,plan,timeout_s=0.01)
        self.assertEqual(timeout['diagnostics']['error_code'],'TOOL_TIMEOUT')
        self.assertEqual(timeout['artifact']['status'],'FAILED')
        execution=load(self.root/timeout['artifact']['simulation_id']/'execution.json')
        self.assertIsNotNone(execution['worker_exit_code'])
        event=threading.Event()
        timer=threading.Timer(0.1,event.set)
        timer.start()
        cancelled=self.adapter.simulate(task,plan,cancel_event=event)
        timer.join()
        self.assertEqual(cancelled['artifact']['status'],'CANCELLED')
        self.assertIsNone(cancelled['series'])
        self.assertIsNone(cancelled['diagnostics']['converged'])

    def test_two_concurrent_candidates_use_distinct_processes_and_artifacts(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(self.adapter.simulate,*case(kind=kind)) for kind in ('normal','closure')]
            a,b=[job.result(timeout=120) for job in jobs]
        self.assertNotEqual(a['artifact']['simulation_id'],b['artifact']['simulation_id'])
        for result,kind in [(a,'normal'),(b,'closure')]:
            comparison=compare_frames(result['series']['frames'],self.result(kind)['series']['frames'])
            self.assertTrue(comparison['numerically_equivalent'],comparison)

    def test_filtered_series_and_fault_entity_mapping(self):
        sid=self.result('leak')['artifact']['simulation_id']
        page=self.adapter.get_series(sid,'13',3600,4500)
        self.assertEqual(len(page['frames']),1)
        self.assertEqual(page['frames'][0]['nodes'][0]['node_id'],'13')
        self.assertEqual(page['frames'][0]['leaks'][0]['source_link_id'],'12')
        with self.assertRaises(ValueError):self.adapter.get_series('../escape')

    def test_perturbed_demand_really_changes_engine_input_and_output(self):
        task,plan=case()
        task['hydraulic_options']['demand_multiplier']=1.1
        rebind(task,plan)
        result=self.adapter.simulate(task,plan)
        self.assertEqual(result['artifact']['status'],'SUCCEEDED',result['diagnostics'])
        a=self.result('normal')['series']['frames'][0]['nodes']
        b=result['series']['frames'][0]['nodes']
        for x,y in zip(a,b):
            self.assertAlmostEqual(y['expected_demand_m3s'],x['expected_demand_m3s']*1.1)
        self.assertNotEqual(self.result('normal')['artifact']['input_hash'],result['artifact']['input_hash'])

    def test_invalid_worker_input_and_launch_failure_retain_evidence(self):
        from unittest.mock import patch
        task,plan=case(kind='leak',engine='epanet')
        result=self.adapter.simulate(task,plan)
        self.assertEqual(result['artifact']['status'],'FAILED')
        self.assertEqual(result['diagnostics']['error_code'],'INPUT_INVALID')
        self.assertIsNone(result['series'])
        with patch('services.hydraulics.runtime.subprocess.Popen',side_effect=OSError('launch unavailable')):
            result=self.adapter.simulate(*case())
        folder=self.root/result['artifact']['simulation_id']
        self.assertEqual(result['diagnostics']['error_code'],'WORKER_FAILED')
        self.assertTrue((folder/'evidence-manifest.json').exists())

    def test_corrupted_evidence_and_material_numeric_changes_are_detected(self):
        import shutil
        folder=self.root/'corrupted'/self.folder('leak').name
        shutil.copytree(self.folder('leak'),folder)
        with (folder/'source.inp').open('ab') as f:f.write(b'\n; tampered\n')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):verify_evidence(folder)
        frames=self.result('leak')['series']['frames']
        changed=copy.deepcopy(frames)
        changed[0]['nodes'][0]['pressure_m']+=0.001
        self.assertFalse(compare_frames(frames,changed)['numerically_equivalent'])

    def test_v3_read_api_exposes_fault_mapping_and_actual_series(self):
        import json
        from urllib.request import urlopen
        from services.hydraulics.server import make_server
        server=make_server(self.root,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            sid=self.result('rupture')['artifact']['simulation_id']
            for endpoint in ('mapping','physics','series?start_s=3600&end_s=4500'):
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/simulations/{sid}/{endpoint}') as response:
                    self.assertEqual(response.headers['X-WaterGuard-Mode'],'benchmark')
                    body=json.load(response)
                if endpoint=='physics':self.assertTrue(body['passed'])
                if endpoint=='mapping':self.assertEqual(len(body['leaks']),2)
                if endpoint.startswith('series'):self.assertEqual(len(body['frames']),1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_same_task_feedback_pair_improves_without_claiming_recommendation(self):
        from services.hydraulics.pair import run as run_pair
        index=run_pair(self.root/'feedback')
        a,b=index['cases']
        self.assertGreater(a['metrics']['shortage_m3']-b['metrics']['shortage_m3'],100)
        self.assertTrue(index['prewarm_comparison']['numerically_equivalent'])
        self.assertEqual(b['admission']['nominal_constraints_pass'],'fail')
        self.assertEqual(b['admission']['recommendation_eligible'],'fail')


if __name__=='__main__':
    unittest.main()
