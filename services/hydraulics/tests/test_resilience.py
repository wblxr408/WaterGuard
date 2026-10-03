"""Failure injection at actual filesystem and subprocess boundaries."""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from services.hydraulics.storage import write
from services.hydraulics.runtime import PhysicalAdapter, ExecutionLimits, engine_identity, runtime_environment
from services.hydraulics.scenarios import case
from services.hydraulics.integrity import validate_output, verify_inventory
from waterguard_contracts.core import load


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.adapter=PhysicalAdapter(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_interrupted_atomic_publish_preserves_old_document(self):
        target=self.root/'result.json'
        write(target,{'status':'old'})
        with patch('services.hydraulics.storage.os.replace',side_effect=OSError('injected rename failure')):
            with self.assertRaises(OSError):write(target,{'status':'new'})
        self.assertEqual(load(target),{'status':'old'})
        self.assertEqual([p.name for p in self.root.iterdir()],['result.json'])

    def test_nonfinite_timeout_rejected_before_allocating_run(self):
        for value in (float('nan'),float('inf'),-1,0,True):
            with self.assertRaisesRegex(ValueError,'finite and positive'):
                self.adapter.simulate(*case(),timeout_s=value)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_temporary_windows_replace_denial_retries_and_persistent_denial_preserves_old(self):
        from services.hydraulics import storage
        target=self.root/'result.json';write(target,{'status':'old'})
        denied=PermissionError('injected Windows denial');denied.winerror=5
        replace=storage.os.replace
        with patch('services.hydraulics.storage.os.replace',side_effect=[denied,None]) as attempted,patch('services.hydraulics.storage.time.sleep'):
            # Second call performs the real atomic replacement.
            attempted.side_effect=None
            def flaky(source,destination):
                if attempted.call_count==1:raise denied
                return replace(source,destination)
            attempted.side_effect=flaky
            write(target,{'status':'new'})
            self.assertEqual(attempted.call_count,2)
        self.assertEqual(load(target),{'status':'new'})
        with patch('services.hydraulics.storage.os.replace',side_effect=denied) as attempted,patch('services.hydraulics.storage.time.sleep'):
            with self.assertRaises(PermissionError):write(target,{'status':'blocked'})
            self.assertEqual(attempted.call_count,6)
        self.assertEqual(load(target),{'status':'new'})
        self.assertEqual([p.name for p in self.root.iterdir()],['result.json'])

    def test_numeric_dependencies_and_native_binaries_bind_runtime(self):
        task,_=case()
        before=engine_identity(task)
        env=runtime_environment()
        self.assertGreaterEqual(len(env['native_binaries']),3)
        env['packages']['numpy']='changed-version'
        with patch('services.hydraulics.runtime.runtime_environment',return_value=env):
            self.assertNotEqual(before,engine_identity(task))

    def test_crashed_native_process_produces_failed_record(self):
        real_popen=subprocess.Popen
        def crash(command,**kwargs):
            return real_popen([sys.executable,'-c','import os; os._exit(23)'],**kwargs)
        with patch('services.hydraulics.runtime.subprocess.Popen',side_effect=crash):
            result=self.adapter.simulate(*case())
        folder=self.root/result['artifact']['simulation_id']
        self.assertEqual(result['diagnostics']['error_code'],'WORKER_FAILED')
        self.assertEqual(load(folder/'execution.json')['worker_exit_code'],23)
        self.assertEqual(load(folder/'lifecycle.json')['status'],'FAILED')
        verify_inventory(folder)

    def test_truncated_worker_json_is_quarantined_and_rejected(self):
        real_popen=subprocess.Popen
        def broken(command,**kwargs):
            folder=command[-1]
            script="from pathlib import Path; import sys; (Path(sys.argv[1])/'result.json').write_text('{broken')"
            return real_popen([sys.executable,'-c',script,folder],**kwargs)
        with patch('services.hydraulics.runtime.subprocess.Popen',side_effect=broken):
            result=self.adapter.simulate(*case())
        folder=self.root/result['artifact']['simulation_id']
        self.assertEqual(result['diagnostics']['error_code'],'WORKER_PROTOCOL_ERROR')
        self.assertEqual((folder/'rejected-worker-result.json').read_text(),'{broken')
        verify_inventory(folder)

    def test_supervisor_exception_reaps_real_worker(self):
        class BrokenCancellation:
            calls=0
            def is_set(self):
                self.calls+=1
                if self.calls>1:raise RuntimeError('injected cancellation provider failure')
                return False
        result=self.adapter.simulate(*case(),cancel_event=BrokenCancellation())
        execution=load(self.root/result['artifact']['simulation_id']/'execution.json')
        self.assertEqual(result['diagnostics']['error_code'],'WORKER_FAILED')
        self.assertIsNotNone(execution['worker_pid'])
        self.assertIsNotNone(execution['worker_exit_code'])

    def test_artifact_budget_and_result_size_are_enforced(self):
        adapter=PhysicalAdapter(self.root,limits=ExecutionLimits(max_artifact_bytes=1))
        result=adapter.simulate(*case())
        self.assertEqual(result['diagnostics']['error_code'],'ARTIFACT_LIMIT')
        adapter=PhysicalAdapter(self.root,limits=ExecutionLimits(max_result_values=1))
        result=adapter.simulate(*case())
        self.assertEqual(result['artifact']['status'],'FAILED')
        self.assertIn('Result size exceeds',str(result['diagnostics']['errors']))

    def test_keyboard_interrupt_is_reraised_after_worker_cleanup(self):
        class Interrupt:
            calls=0
            def is_set(self):
                self.calls+=1
                if self.calls>1:raise KeyboardInterrupt()
                return False
        with self.assertRaises(KeyboardInterrupt):
            self.adapter.simulate(*case(),cancel_event=Interrupt())
        folder=next(self.root.glob('sim-*'))
        self.assertEqual(load(folder/'result.json')['diagnostics']['error_code'],'SUPERVISOR_INTERRUPTED')
        self.assertIsNotNone(load(folder/'execution.json')['worker_exit_code'])
        verify_inventory(folder)

    def test_read_path_detects_tampered_result_and_inconsistent_manifest(self):
        result=self.adapter.simulate(*case())
        sid=result['artifact']['simulation_id']
        folder=self.root/sid
        self.assertEqual(result['artifact']['status'],'SUCCEEDED',result['diagnostics'])
        modified=copy.deepcopy(result)
        modified['artifact']['candidate_id']='wrong-candidate'
        write(folder/'result.json',modified)
        with self.assertRaisesRegex(ValueError,'binding mismatch'):validate_output(folder)
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.adapter.get_series(sid)
        write(folder/'result.json',result)
        manifest=load(folder/'evidence-manifest.json')
        manifest['artifacts'].pop()
        write(folder/'evidence-manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'index mismatch'):verify_inventory(folder)


if __name__=='__main__':unittest.main()
