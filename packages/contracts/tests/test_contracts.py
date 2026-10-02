import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jsonschema.exceptions import ValidationError
from waterguard_contracts.core import (FIXTURES, FixtureAdapter, check_bundle, events_after,
    input_hash, load, plan_hash, resolve, task_hash, task_ref, validate, verify_fixture)
from waterguard_contracts.mock import make_server


class ContractsTests(unittest.TestCase):
    def setUp(self):
        self.task = load(FIXTURES/'task.json')
        self.plan = load(FIXTURES/'candidate-b.json')
        self.result = load(FIXTURES/'result-b.json')
        self.catalog = load(FIXTURES/'action-catalog.json')
        self.scene = load(FIXTURES/'scene.json')

    def report(self):
        return verify_fixture(self.task,self.plan,self.result,self.catalog,self.scene)

    def rebind(self):
        self.task['constraint_hash'] = task_hash(self.task)
        self.plan['task_ref'] = task_ref(self.task)
        self.plan['plan_hash'] = plan_hash(self.plan)
        self.result['artifact']['task_ref'] = task_ref(self.task)
        self.result['artifact']['input_hash'] = input_hash(self.task,self.plan,self.result['artifact']['engine_version'])

    def test_frozen_bundle_and_independently_known_metrics(self):
        a,b = check_bundle()
        self.assertAlmostEqual(a['metrics']['shortage_m3'], (0.003+0.001)*7200)
        self.assertAlmostEqual(a['metrics']['supply_ratio'], 1/3)
        self.assertEqual(b['metrics']['shortage_m3'],0)
        self.assertEqual(b['metrics']['min_pressure_m'],24)
        self.assertEqual(b['admission']['required_stress_tests_pass'],'pending')
        self.assertEqual(b['admission']['recommendation_eligible'],'fail')

    def test_unknown_fields_and_invalid_units_rejected(self):
        self.plan['approved']=True
        with self.assertRaises(ValidationError): validate('ActionPlan',self.plan)
        self.result['series']['units']['flow']='L/s'
        with self.assertRaises(ValidationError): validate('SimulationResult',self.result)

    def test_stale_revision_and_changed_constraint_rejected(self):
        self.plan['task_ref']['revision']=2
        self.plan['plan_hash']=plan_hash(self.plan)
        with self.assertRaisesRegex(ValueError,'revision'): self.report()
        self.task['hard_constraints']['min_pressure_m']=14
        with self.assertRaisesRegex(ValueError,'Task hash'): self.report()

    def test_unknown_facility_and_conflicting_actions_rejected(self):
        self.plan['actions'][0]['link_id']='UNKNOWN'
        self.rebind()
        with self.assertRaisesRegex(ValueError,'facility'): self.report()
        self.plan['actions'][0]['link_id']='P_BYPASS'
        self.plan['actions'].append(dict(kind='link_close',link_id='P_BYPASS',simulation_time_s=3600))
        self.rebind()
        with self.assertRaisesRegex(ValueError,'Conflicting'): self.report()

    def test_action_budget_and_outside_window_rejected(self):
        self.plan['actions']*=3
        self.rebind()
        with self.assertRaisesRegex(ValueError,'budget'): self.report()
        self.plan['actions']=self.plan['actions'][:1]
        self.plan['actions'][0]['simulation_time_s']=1800
        self.rebind()
        with self.assertRaisesRegex(ValueError,'window'): self.report()

    def test_nonfinite_and_negative_demand_rejected(self):
        self.result['series']['frames'][0]['nodes'][0]['pressure_m']=float('nan')
        with self.assertRaisesRegex(ValueError,'Non-finite'): self.report()
        self.result['series']['frames'][0]['nodes'][0]['pressure_m']=35
        self.result['series']['frames'][0]['nodes'][0]['expected_demand_m3s']=-1
        with self.assertRaises(ValidationError): self.report()

    def test_missing_frames_and_nodes_invalidate_result(self):
        self.result['series']['frames'].pop()
        self.assertEqual(self.report()['validity'],'invalid')
        self.result=load(FIXTURES/'result-b.json')
        self.result['series']['frames'][2]['nodes'].pop()
        report=self.report()
        self.assertEqual(report['admission']['simulation_valid'],'fail')
        self.assertEqual(report['admission']['nominal_constraints_pass'],'pending')

    def test_nonconvergence_is_not_nominal_failure_or_success(self):
        self.result['diagnostics']['converged']=False
        report=self.report()
        self.assertEqual(report['validity'],'invalid')
        self.assertEqual(report['admission']['nominal_constraints_pass'],'pending')

    def test_zero_demand_is_not_applicable_and_leak_not_supply(self):
        for frame in self.result['series']['frames']:
            for node in frame['nodes']:
                node['expected_demand_m3s']=0
                node['delivered_demand_m3s']=0
                node['leak_flow_m3s']=100
        report=self.report()
        self.assertIsNone(report['metrics']['supply_ratio'])
        self.assertEqual(report['metrics']['shortage_m3'],0)

    def test_isolated_users_remain_in_denominator(self):
        for frame in self.result['series']['frames'][2:]:
            for node in frame['nodes'][1:]:
                node['delivered_demand_m3s']=0
                node['leak_flow_m3s']=1
        report=self.report()
        self.assertAlmostEqual(report['metrics']['supply_ratio'],1/3)
        self.assertEqual(report['metrics']['shortage_m3'],28.8)

    def test_fixture_cannot_be_relabelled_live(self):
        self.result['mode']='live'
        with self.assertRaisesRegex(ValueError,'non-fixture'): self.report()

    def test_adapter_rejects_unseen_inputs_and_returns_independent_results(self):
        adapter=FixtureAdapter()
        first=adapter.simulate(self.task,self.plan)
        first['series']['frames'].clear()
        self.assertEqual(len(adapter.simulate(self.task,self.plan)['series']['frames']),6)
        self.plan['actions'][0]['simulation_time_s']=5400
        with self.assertRaisesRegex(ValueError,'No fixture'): adapter.simulate(self.task,self.plan)

    def test_series_filter_and_event_resume(self):
        series=FixtureAdapter().get_series('simulation-b','J2',3600,7200)
        self.assertEqual([f['time_s'] for f in series['frames']],[3600,5400])
        self.assertTrue(all(len(f['nodes'])==1 for f in series['frames']))
        events=[json.loads(line) for line in (FIXTURES/'events.jsonl').read_text().splitlines()]
        self.assertEqual([e['sequence'] for e in events_after(events,6)],[7,8])
        with self.assertRaises(ValueError): events_after(events,99)

    def test_reference_escape_rejected(self):
        with self.assertRaisesRegex(ValueError,'escapes'): resolve(FIXTURES,'../../schema/waterguard.schema.json')

    def test_event_timestamp_and_empty_series_query(self):
        event=json.loads((FIXTURES/'events.jsonl').read_text().splitlines()[0])
        event['occurred_at']='yesterday'
        with self.assertRaises(ValidationError): validate('RunEvent',event)
        series=FixtureAdapter().get_series('simulation-b',start_s=20000,end_s=21000)
        self.assertEqual(series['frames'],[])
        validate('SeriesPage',series)
        with self.assertRaises(ValidationError): validate('Series',series)

    def test_tampered_evidence_and_report_rejected(self):
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'day1'
            shutil.copytree(FIXTURES,root)
            report=load(root/'verification-b.json')
            report['metrics']['shortage_m3']=100
            (root/'verification-b.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'recomputation'): check_bundle(root)
            shutil.copyfile(FIXTURES/'verification-b.json',root/'verification-b.json')
            (root/'network.inp').write_text('changed')
            with self.assertRaisesRegex(ValueError,'Network file hash'): check_bundle(root)


class MockAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def fetch(self,path,**kwargs):
        return urlopen(Request(self.base+path,**kwargs),timeout=5)

    def test_routes_and_cors(self):
        for route in ['/api/fixture','/api/networks','/api/networks/wg-fixture-loop-v1/scene','/api/runs/run-day1-fixture','/api/runs/run-day1-fixture/candidates','/api/runs/run-day1-fixture/evidence','/api/artifacts/result-a.json']:
            with self.fetch(route,headers={'Origin':'http://localhost:5173'}) as response:
                self.assertEqual(response.status,200)
                self.assertEqual(response.headers['X-WaterGuard-Mode'],'fixture')
                self.assertEqual(response.headers['Access-Control-Allow-Origin'],'http://localhost:5173')
                json.load(response)

    def test_sse_resume_and_end(self):
        with self.fetch('/api/runs/run-day1-fixture/events',headers={'Last-Event-ID':'6'}) as response:
            text=response.read().decode()
            self.assertIn('id: 7\n',text)
            self.assertIn('id: 8\n',text)
            self.assertNotIn('id: 6\n',text)
        with self.fetch('/api/runs/run-day1-fixture/events',headers={'Last-Event-ID':'8'}) as response:
            self.assertEqual(response.status,204)

    def test_series_endpoint_and_invalid_requests(self):
        with self.fetch('/api/simulations/simulation-b/series?node_id=J2&start_s=3600&end_s=5400') as response:
            self.assertEqual(len(json.load(response)['frames']),1)
        for path,kwargs,code in [('/api/simulations/simulation-b/series?node_id=unknown',{},400),('/api/artifacts/../task.json',{},404),('/api/runs/run-day1-fixture/events',{'headers':{'Last-Event-ID':'garbage'}},400),('/api/tasks',{'method':'POST','data':b'{}'},405),('/api/simulations/missing/series',{},404)]:
            with self.assertRaises(HTTPError) as caught:
                self.fetch(path,**kwargs)
            self.assertEqual(caught.exception.code,code)
            caught.exception.close()


if __name__=='__main__':
    unittest.main()
