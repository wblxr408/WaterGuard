"""Process-isolated, immutable-input simulation adapter for student 1's delivery."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from uuid import uuid4

from .adapter import write
from .datasets import ROOT, DATA, NETWORKS, manifest
from waterguard_contracts.core import digest, file_hash, input_hash, load, plan_hash, require, resolve, task_hash, task_ref, validate


def engine_identity(task):
    paths = [Path(__file__).parent/name for name in ('adapter.py','datasets.py','compiler.py','physical.py','runtime.py','worker.py')]
    paths += [ROOT/'packages/contracts/waterguard_contracts/core.py',ROOT/'packages/contracts/schema/waterguard.schema.json']
    code = b''.join(p.read_bytes() for p in paths)
    # Normalize repository source newlines before hashing across hosts.
    implementation = hashlib.sha256(code.replace(b'\r\n',b'\n')).hexdigest()
    engine = 'WNTRSimulator' if task['hydraulic_options']['engine']=='wntr' else 'EPANET/2.2'
    return f'WNTR/{importlib.metadata.version("wntr")};{engine};adapter/0.3.0;code/{implementation}'


def finish(folder, *, series=None, errors=None, warnings=None, raw_converged=None, error_code=None, status=None):
    folder = Path(folder)
    request = load(folder/'request.json')
    task, plan, sid, engine = request['task'],request['plan'],folder.name,request['engine_version']
    errors = errors or []
    diag = dict(schema_version='0.3.0',simulation_id=sid,mode='benchmark',converged=raw_converged,
                raw_converged=raw_converged,errors=errors,warnings=warnings or [],error_code=error_code,
                provenance='Actual isolated engine process; inspect raw CSV and physical-checks.json. No AGH decision is asserted.')
    success = not errors and series is not None
    artifact = dict(schema_version='0.3.0',simulation_id=sid,candidate_id=plan['candidate_id'],task_ref=task_ref(task),perturbation_id=None,
        mode='benchmark',engine_version=engine,input_hash=input_hash(task,plan,engine),status=status or ('SUCCEEDED' if success else 'FAILED'),
        series_ref='series.json' if success else None,diagnostics_ref='diagnostics.json',time_axis=task['time_window'],
        entity_map_ref='entity-map.json',physical_checks_ref='physical-checks.json',input_manifest_ref='input-manifest.json')
    result = dict(schema_version='0.3.0',mode='benchmark',artifact=artifact,series=series if success else None,diagnostics=diag)
    validate('SimulationResult',result)
    if success:
        write(folder/'series.json',series)
    write(folder/'diagnostics.json',diag)
    write(folder/'result.json',result)
    return result


def seal(folder):
    folder = Path(folder)
    result = load(folder/'result.json')
    entries = [dict(ref=p.name,sha256=file_hash(p),kind=p.suffix.lstrip('.')) for p in sorted(folder.iterdir()) if p.is_file() and p.name!='evidence-manifest.json']
    evidence = dict(schema_version='0.3.0',bundle_id='bundle-'+folder.name,run_id=folder.name,mode='benchmark',native_trace_ref=None,association_ref=None,
        artifacts=entries,hashes={e['ref']:e['sha256'] for e in entries},redactions=[],missing_items=['native_agh_trace','business_verification','required_stress_tests'],live_export_allowed=False)
    validate('EvidenceManifest',evidence)
    write(folder/'evidence-manifest.json',evidence)


class PhysicalAdapter:
    def __init__(self, output_root=ROOT/'artifacts/physical'):
        self.output_root = Path(output_root).resolve()

    def simulate(self, task, plan, *, timeout_s=None, cancel_event=None):
        # Serialize a snapshot before starting a worker; never keep caller-owned objects.
        task,plan = json.loads(json.dumps([task,plan],allow_nan=False))
        validate('TaskSpec',task)
        validate('ActionPlan',plan)
        require(task['schema_version']==plan['schema_version']=='0.3.0','PhysicalAdapter requires 0.3.0')
        require(task_hash(task)==task['constraint_hash'] and plan_hash(plan)==plan['plan_hash'],'Input hash mismatch')
        require(plan['task_ref']==task_ref(task),'Plan/task binding mismatch')
        source = manifest(task['network_ref']['network_id'])
        maximum = task['budget']['max_wall_time_s']
        timeout = maximum if timeout_s is None else min(timeout_s,maximum)
        require(timeout>0,'Timeout must be positive')
        folder = self.output_root/('sim-'+uuid4().hex)
        folder.mkdir(parents=True,exist_ok=False)
        engine = engine_identity(task)
        write(folder/'request.json',dict(task=task,plan=plan,engine_version=engine))
        write(folder/'task.json',task)
        write(folder/'plan.json',plan)
        write(folder/'network-manifest.json',source)
        (folder/'source.inp').write_bytes((DATA/NETWORKS[source['network_id']]).read_bytes())
        (folder/'LICENSE-WNTR.md').write_bytes((DATA/'LICENSE.md').read_bytes())
        (folder/'runtime-overlay.json').write_bytes((ROOT/source['overlays'][0]['ref']).read_bytes())
        write(folder/'environment.json',dict(python=platform.python_version(),platform=platform.platform(),packages={n:importlib.metadata.version(n) for n in ('wntr','numpy','pandas','scipy','networkx')}))
        write(folder/'input-manifest.json',dict(input_hash=input_hash(task,plan,engine),engine_version=engine,
            source_sha256=file_hash(folder/'source.inp'),overlay_sha256=file_hash(folder/'runtime-overlay.json'),
            task_sha256=file_hash(folder/'task.json'),plan_sha256=file_hash(folder/'plan.json'),
            failure_model=task['fault'],hydraulic_options=task['hydraulic_options'],control_policy=task['control_policy']))
        # Explicit placeholders remain meaningful for cancellation before compilation.
        write(folder/'entity-map.json',dict(status='not_compiled'))
        write(folder/'physical-checks.json',dict(passed=False,status='not_evaluated'))
        started = time.monotonic()
        result = None
        worker = None
        if cancel_event is not None and cancel_event.is_set():
            result = finish(folder,errors=['Cancelled before engine start'],error_code='CANCELLED',status='CANCELLED')
        else:
            with (folder/'worker.stdout.txt').open('wb') as stdout, (folder/'worker.stderr.txt').open('wb') as stderr:
                # Reduce process-dependent numeric ordering; replay still reports roundoff.
                env={**os.environ,'PYTHONHASHSEED':'0','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
                try:
                    worker = subprocess.Popen([sys.executable,'-m','services.hydraulics.worker',str(folder)],cwd=ROOT,stdout=stdout,stderr=stderr,env=env)
                except OSError as exc:
                    result=finish(folder,errors=[f'Worker launch failed: {exc}'],error_code='WORKER_FAILED')
                while worker is not None and worker.poll() is None:
                    cancelled = cancel_event is not None and cancel_event.is_set()
                    expired = time.monotonic()-started >= timeout
                    if cancelled or expired:
                        worker.terminate()
                        try:
                            worker.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            worker.kill()
                            worker.wait()
                        result = finish(folder,errors=['Engine process cancelled' if cancelled else 'Engine wall-time budget exceeded'],
                            error_code='CANCELLED' if cancelled else 'TOOL_TIMEOUT',status='CANCELLED' if cancelled else 'FAILED')
                        break
                    time.sleep(0.025)
                if result is None:
                    if worker.returncode!=0 or not (folder/'result.json').exists():
                        result=finish(folder,errors=[f'Engine worker exited {worker.returncode}; see worker.stderr.txt'],error_code='WORKER_FAILED')
                    else:
                        result=load(folder/'result.json')
        write(folder/'execution.json',dict(elapsed_s=time.monotonic()-started,timeout_s=timeout,process_isolated=True,python_hash_seed=0,numerical_threads=1,
            worker_pid=None if worker is None else worker.pid,worker_exit_code=None if worker is None else worker.returncode))
        seal(folder)
        return result

    def get_series(self, simulation_id, node_id=None, start_s=None, end_s=None):
        require(simulation_id.startswith('sim-') and '/' not in simulation_id and '\\' not in simulation_id,'Invalid simulation ID')
        result = load(resolve(self.output_root,simulation_id+'/result.json'))
        require(result['artifact']['simulation_id']==simulation_id,'Simulation binding mismatch')
        require(result['artifact']['status']=='SUCCEEDED' and result['series'] is not None,'Simulation has no valid series')
        require(start_s is None or end_s is None or start_s<end_s,'Invalid series range')
        series=result['series']
        known={n['node_id'] for f in series['frames'] for n in f['nodes']}
        require(node_id is None or node_id in known,'Unknown original user node')
        series['frames']=[f for f in series['frames'] if (start_s is None or f['time_s']>=start_s) and (end_s is None or f['time_s']<end_s)]
        if node_id is not None:
            for f in series['frames']:
                f['nodes']=[n for n in f['nodes'] if n['node_id']==node_id]
        validate('SeriesPage',series)
        return series
