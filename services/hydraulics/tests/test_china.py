"""Chinese model integration: provenance, topology, fault mapping and license evidence."""
import tempfile
from pathlib import Path
import unittest

from services.hydraulics.compiler import assets, compile_task
from services.hydraulics.datasets import CHINA_NETWORK, CHINA_SHA256, network_path, verify_sources
from services.hydraulics.runtime import PhysicalAdapter
from services.hydraulics.scenarios import physical_case, case
from waterguard_contracts.core import file_hash, load, verify_benchmark


class ChinaNetworkTests(unittest.TestCase):
    def test_source_topology_and_fault_ids(self):
        verify_sources()
        wn, source, scene, catalog = assets(CHINA_NETWORK)
        self.assertEqual(file_hash(network_path(CHINA_NETWORK)), CHINA_SHA256)
        self.assertEqual((wn.num_junctions, wn.num_reservoirs, wn.num_links), (113, 1, 164))
        self.assertEqual((wn.num_pumps, wn.num_tanks), (0, 0))
        self.assertIn('CC BY-NC 4.0', source['license'])
        self.assertEqual(len(scene['nodes']), 114)
        self.assertEqual(len(catalog['facilities']), 164)
        for kind in ('normal', 'closure', 'leak', 'rupture', 'isolation'):
            task, plan = physical_case(CHINA_NETWORK, kind)
            model, _, _, _, mapping = compile_task(task, plan)
            self.assertEqual(task['network_ref']['sha256'], CHINA_SHA256)
            if task['fault']:
                self.assertIn(task['fault']['link_id'], wn.pipe_name_list)
            if kind == 'isolation':
                self.assertTrue(mapping['isolated_node_ids'])
        with self.assertRaisesRegex(ValueError, 'no pump'):
            case(CHINA_NETWORK, 'pump-cycle')

    def test_real_result_includes_model_license_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter = PhysicalAdapter(Path(temporary))
            task, plan = physical_case(CHINA_NETWORK, 'closure')
            result = adapter.simulate(task, plan)
            self.assertEqual(result['artifact']['status'], 'SUCCEEDED', result['diagnostics'])
            _, _, scene, catalog = assets(CHINA_NETWORK)
            self.assertEqual(verify_benchmark(task, plan, result, catalog, scene)['validity'], 'valid')
            sid = result['artifact']['simulation_id']
            folder = Path(temporary) / sid
            self.assertEqual(file_hash(folder / 'source.inp'), CHINA_SHA256)
            self.assertTrue((folder / 'LICENSE-ZhiJiang.md').is_file())
            self.assertTrue(load(folder / 'physical-checks.json')['passed'])
            self.assertEqual(len(adapter.get_series(sid)['frames'][0]['nodes']), 113)
            (folder / 'LICENSE-ZhiJiang.md').write_text('Changed license', encoding='utf-8')
            with self.assertRaises(ValueError):
                adapter.get_series(sid)
