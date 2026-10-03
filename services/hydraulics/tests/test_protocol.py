"""Parent trust boundary: corrupt actual engine outputs and cross process boundaries."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import pandas as pd

from services.hydraulics import runtime
from services.hydraulics.integrity import validate_output,verify_inventory,validate_boundaries
from services.hydraulics.scenarios import physical_case,case
from services.hydraulics.storage import write
from waterguard_contracts.core import load


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_temp=tempfile.TemporaryDirectory();cls.source=Path(cls.source_temp.name)
        cls.original=runtime.PhysicalAdapter(cls.source).simulate(*physical_case('epa-net1','normal'))
        if cls.original['artifact']['status']!='SUCCEEDED':raise AssertionError(cls.original['diagnostics'])
        cls.source_folder=cls.source/cls.original['artifact']['simulation_id']

    @classmethod
    def tearDownClass(cls):cls.source_temp.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.folder=self.root/self.source_folder.name;shutil.copytree(self.source_folder,self.folder)
        self.adapter=runtime.PhysicalAdapter(self.root)

    def tearDown(self):self.temp.cleanup()

    def test_all_result_bindings_and_false_success_are_rejected(self):
        changes=[('candidate_id','wrong'),('simulation_id','wrong'),('input_hash','sha256:'+'0'*64),
            ('engine_version','invented'),('perturbation_id','invented')]
        for field,value in changes:
            with self.subTest(field=field):
                r=copy.deepcopy(self.original);r['artifact'][field]=value;write(self.folder/'result.json',r)
                with self.assertRaises(ValueError):validate_output(self.folder)
        r=copy.deepcopy(self.original);r['diagnostics']['converged']=False
        write(self.folder/'diagnostics.json',r['diagnostics']);write(self.folder/'result.json',r)
        with self.assertRaisesRegex(ValueError,'success without convergence'):validate_output(self.folder)

    def test_version_and_time_and_sidecars_cannot_disagree(self):
        for mutate in ('version','time','diagnostic','series','manifest'):
            with self.subTest(mutate=mutate):
                for name in ('result.json','diagnostics.json','series.json','input-manifest.json'):
                    shutil.copyfile(self.source_folder/name,self.folder/name)
                r=copy.deepcopy(self.original)
                if mutate=='version':r['schema_version']='0.3.0'
                elif mutate=='time':r['artifact']['time_axis']['end_s']+=900
                elif mutate=='diagnostic':r['diagnostics']['warnings'].append('disagreement')
                elif mutate=='series':r['series']['frames'].pop()
                else:
                    m=load(self.folder/'input-manifest.json');m['hydraulic_options']['demand_multiplier']=1.1;write(self.folder/'input-manifest.json',m)
                write(self.folder/'result.json',r)
                with self.assertRaises(ValueError):validate_output(self.folder)

    def test_coherent_result_and_series_changes_still_disagree_with_raw(self):
        r=copy.deepcopy(self.original);r['series']['frames'][0]['nodes'][0]['pressure_m']+=1
        write(self.folder/'series.json',r['series']);write(self.folder/'result.json',r)
        with self.assertRaisesRegex(ValueError,'Raw/sidecar mismatch'):validate_output(self.folder)

    def test_forged_pass_flag_does_not_override_raw_physics(self):
        path=self.folder/'raw-node-head.csv';table=pd.read_csv(path,index_col='time_s',float_precision='round_trip')
        table.at[0,'13']+=1;table.to_csv(path,index_label='time_s',float_format='%.17g')
        self.assertTrue(load(self.folder/'physical-checks.json')['passed'])
        with self.assertRaisesRegex(ValueError,'head/pressure/elevation|Independent raw physics'):validate_output(self.folder)

    def test_raw_head_pressure_and_fixed_source_boundaries_are_not_independent_claims(self):
        path=self.folder/'raw-node-pressure.csv';table=pd.read_csv(path,index_col='time_s',float_precision='round_trip')
        table.at[0,'13']+=1;table.to_csv(path,index_label='time_s',float_format='%.17g')
        with self.assertRaisesRegex(ValueError,'head/pressure/elevation'):validate_output(self.folder)
        shutil.copyfile(self.source_folder/'raw-node-pressure.csv',path)
        path=self.folder/'raw-node-head.csv';table=pd.read_csv(path,index_col='time_s',float_precision='round_trip')
        # Preserve pipe/pump head differences by shifting every head equally.
        table+=1;table.to_csv(path,index_label='time_s',float_format='%.17g')
        pressure=self.folder/'raw-node-pressure.csv';p=pd.read_csv(pressure,index_col='time_s',float_precision='round_trip')
        for name in load(self.folder/'entity-map.json')['compiled_junction_ids']:p[name]+=1
        p.to_csv(pressure,index_label='time_s',float_format='%.17g')
        with self.assertRaisesRegex(ValueError,'source head boundary'):validate_output(self.folder)

    def test_compiled_mapping_and_dense_metrics_are_independently_bound(self):
        for name,mutate in [('entity-map.json',lambda d:d['original_junction_ids'].pop()),
            ('compiled-network.json',lambda d:d['options']['hydraulic'].update(demand_multiplier=2)),
            ('hydraulic-analysis.json',lambda d:d['dense_metrics'].update(shortage_m3=1000))]:
            with self.subTest(name=name):
                data=load(self.folder/name);mutate(data);write(self.folder/name,data)
                with self.assertRaises(ValueError):validate_output(self.folder)
                shutil.copyfile(self.source_folder/name,self.folder/name)

    def test_isolation_zero_sentinel_requires_source_disconnection_and_zero_supply(self):
        import wntr
        from types import SimpleNamespace
        wn=wntr.network.WaterNetworkModel();wn.add_reservoir('r',base_head=50)
        wn.add_junction('j',elevation=10);wn.add_pipe('p','r','j',length=100,diameter=.1,roughness=100)
        raw=SimpleNamespace(node={k:pd.DataFrame({'r':[50 if k=='head' else 0],'j':[0]},index=[0],dtype=float) for k in ('head','pressure','demand','leak_demand')},
            link={k:pd.DataFrame({'p':[0]},index=[0],dtype=float) for k in ('status','flowrate')})
        validate_boundaries(wn,raw,'wntr',[0])
        for field in ('demand','leak_demand'):
            raw.node[field].at[0,'j']=.001
            with self.assertRaisesRegex(ValueError,'isolated junction sentinel'):validate_boundaries(wn,raw,'wntr',[0])
            raw.node[field].at[0,'j']=0
        raw.link['flowrate'].at[0,'p']=.001
        with self.assertRaisesRegex(ValueError,'isolated junction sentinel'):validate_boundaries(wn,raw,'wntr',[0])
        raw.link['flowrate'].at[0,'p']=0;raw.link['status'].at[0,'p']=1
        with self.assertRaisesRegex(ValueError,'head/pressure/elevation'):validate_boundaries(wn,raw,'wntr',[0])

    def test_checkout_path_in_model_name_does_not_change_physical_identity(self):
        data=load(self.folder/'compiled-network.json');data['name']='/another/checkout/Net1.inp';write(self.folder/'compiled-network.json',data)
        self.assertEqual(validate_output(self.folder)['artifact']['status'],'SUCCEEDED')

    def test_missing_raw_fields_and_unreported_result_size_are_rejected(self):
        (self.folder/'raw-node-head.csv').unlink()
        with self.assertRaisesRegex(ValueError,'Missing raw'):validate_output(self.folder)
        shutil.copyfile(self.source_folder/'raw-node-head.csv',self.folder/'raw-node-head.csv')
        req=load(self.folder/'request.json');req['execution_limits']['max_result_values']=1;write(self.folder/'request.json',req)
        with self.assertRaisesRegex(ValueError,'Actual result size'):validate_output(self.folder)

    def test_inventory_binds_run_and_rejects_extra_directories_and_duplicates(self):
        m=load(self.folder/'evidence-manifest.json');m['run_id']='wrong';write(self.folder/'evidence-manifest.json',m)
        with self.assertRaisesRegex(ValueError,'bundle binding'):verify_inventory(self.folder)
        shutil.copyfile(self.source_folder/'evidence-manifest.json',self.folder/'evidence-manifest.json')
        m=load(self.folder/'evidence-manifest.json');m['artifacts'].append(m['artifacts'][0]);write(self.folder/'evidence-manifest.json',m)
        with self.assertRaisesRegex(ValueError,'index mismatch'):verify_inventory(self.folder)
        (self.folder/'unexpected').mkdir()
        with self.assertRaisesRegex(ValueError,'Unsafe evidence'):verify_inventory(self.folder)

    def test_manifest_symlink_is_not_a_trusted_inventory(self):
        target=self.root/'outside.json';shutil.copyfile(self.folder/'evidence-manifest.json',target)
        (self.folder/'evidence-manifest.json').unlink()
        try:os.symlink(target,self.folder/'evidence-manifest.json')
        except OSError as exc:self.skipTest('Host cannot create symlinks: '+str(exc))
        with self.assertRaisesRegex(ValueError,'Unsafe evidence'):verify_inventory(self.folder)

    def test_completed_child_with_mutated_request_is_quarantined(self):
        real=subprocess.Popen
        def damaged(command,**kwargs):
            script="from pathlib import Path;import sys;from services.hydraulics.worker import compute;p=Path(sys.argv[1]);compute(p);(p/'request.json').write_text('{broken',encoding='utf-8')"
            return real([sys.executable,'-c',script,command[-1]],**kwargs)
        with patch('services.hydraulics.runtime.subprocess.Popen',side_effect=damaged):r=self.adapter.simulate(*case())
        folder=self.root/r['artifact']['simulation_id']
        self.assertEqual(r['diagnostics']['error_code'],'WORKER_PROTOCOL_ERROR')
        self.assertEqual((folder/'rejected-worker-request.json').read_text(encoding='utf-8'),'{broken')
        self.assertEqual(load(folder/'request.json')['task'],case()[0]);verify_inventory(folder)
        self.assertIsNone(r['series']);self.assertIsNotNone(load(folder/'execution.json')['worker_exit_code'])

    def test_late_cancel_and_deadline_during_validation_are_not_success(self):
        real=runtime.validate_output;event=threading.Event()
        def cancelled(folder,**kwargs):
            result=real(folder,**kwargs);event.set();return result
        with patch('services.hydraulics.runtime.validate_output',side_effect=cancelled):r=self.adapter.simulate(*case(),cancel_event=event)
        self.assertEqual(r['artifact']['status'],'CANCELLED');self.assertIsNone(r['series'])
        def expired(folder,**kwargs):
            result=real(folder,**kwargs)
            # Inject the clock only after a real result has been validated.
            mocked_clock.return_value=1e20
            return result
        import time
        with patch('services.hydraulics.runtime.time.monotonic',side_effect=None,wraps=time.monotonic) as mocked_clock:
            with patch('services.hydraulics.runtime.validate_output',side_effect=expired):r=self.adapter.simulate(*case())
        self.assertEqual(r['diagnostics']['error_code'],'TOOL_TIMEOUT');self.assertIsNone(r['series'])

    def test_final_seal_growth_cannot_return_success(self):
        real=runtime.seal
        def enlarged(folder,**kwargs):
            (folder/'extra.raw').write_bytes(b'x'*400_000);real(folder,**kwargs)
        adapter=runtime.PhysicalAdapter(self.root,limits=runtime.ExecutionLimits(max_artifact_bytes=350_000))
        with patch('services.hydraulics.runtime.seal',side_effect=enlarged):r=adapter.simulate(*case())
        self.assertEqual(r['diagnostics']['error_code'],'ARTIFACT_LIMIT');self.assertIsNone(r['series'])
        folder=self.root/r['artifact']['simulation_id'];verify_inventory(folder)
        self.assertEqual(load(folder/'lifecycle.json')['status'],'FAILED')

    def test_native_epanet_evidence_is_required_and_warning_cannot_be_hidden(self):
        r=self.adapter.simulate(*physical_case('epa-net3','closure','epanet'))
        self.assertEqual(r['artifact']['status'],'SUCCEEDED',r['diagnostics'])
        folder=self.root/r['artifact']['simulation_id'];path=folder/'engine.rpt';original=path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError,'Missing native'):validate_output(folder)
        path.write_bytes(original+b'\nWARNING: injected unbalanced warning\n')
        with self.assertRaisesRegex(ValueError,'report warning'):validate_output(folder)

    @unittest.skipIf(os.name=='nt','Windows terminates directly; POSIX signal escalation tested on Linux CI')
    def test_real_posix_child_ignoring_terminate_is_killed_and_reaped(self):
        script="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('ready',flush=True);time.sleep(30)"
        child=subprocess.Popen([sys.executable,'-c',script],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'ready')
            runtime.stop_worker(child);self.assertEqual(child.returncode,-9)
        finally:
            if child.poll() is None:child.kill();child.wait()
            child.stdout.close()

    def test_interrupt_survives_metadata_write_failure_and_reaps_child(self):
        launched=[];real_popen=subprocess.Popen;real_write=runtime.write
        def launch(*args,**kwargs):
            p=real_popen(*args,**kwargs);launched.append(p);return p
        def denied(path,value):
            if Path(path).name=='execution.json':raise OSError('injected final storage failure')
            return real_write(path,value)
        class Interrupt:
            calls=0
            def is_set(self):
                self.calls+=1
                if self.calls>1:raise KeyboardInterrupt('original interrupt')
                return False
        with patch('services.hydraulics.runtime.subprocess.Popen',side_effect=launch),patch('services.hydraulics.runtime.write',side_effect=denied):
            with self.assertRaisesRegex(KeyboardInterrupt,'original interrupt'):self.adapter.simulate(*case(),cancel_event=Interrupt())
        self.assertEqual(len(launched),1);self.assertIsNotNone(launched[0].poll())

    def test_lifecycle_retains_ordered_parent_phases(self):
        events=load(self.folder/'lifecycle.json')['events']
        self.assertEqual([e['status'] for e in events],['PREPARING','RUNNING','VALIDATING','SUCCEEDED'])
        self.assertIsNone(events[0]['worker_pid'])
        self.assertTrue(all(e['worker_pid']==events[1]['worker_pid'] for e in events[1:]))
        self.assertEqual([e['updated_at'] for e in events],sorted(e['updated_at'] for e in events))


if __name__=='__main__':unittest.main()
