from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from waterguard_contracts.core import file_hash, load, require, validate

DATA = ROOT / 'data/networks/wntr-1.5.0'
COMMIT = '388958845c3209ace9a5bd1b105097977eec2d67'
BASE = f'https://raw.githubusercontent.com/USEPA/WNTR/{COMMIT}/'
SOURCES = {
    'Net1.inp': ('examples/networks/Net1.inp', 'sha256:607510a01287d60d27b280a39df31a001363175a438a5de1b39e749cec6ddbc8'),
    'Net3.inp': ('examples/networks/Net3.inp', 'sha256:ea3e825c4fef0b5cba47fb06301bc85253f18b6364dc96c44d9fb492c40faa52'),
    'LICENSE.md': ('LICENSE.md', 'sha256:1ea2266f0cd710165d06807307f86553ce0ac7f7b6755f5bd2a9929a88900dd1'),
}
NETWORKS = {'epa-net1': 'Net1.inp', 'epa-net3': 'Net3.inp'}
EPA_NETWORKS = tuple(NETWORKS)
CHINA_NETWORK = 'china-zhijiang'
NETWORKS[CHINA_NETWORK] = 'ZhiJiang.inp'
CHINA_DATA = ROOT / 'data/networks/zhijiang'
CHINA_COMMIT = '3a3e7921b2672de46ea5dee4ae3c0ee84695ccad'
CHINA_URL = f'https://raw.githubusercontent.com/WaterFutures/WaterBenchmarkHub/{CHINA_COMMIT}/docs/static/benchmarks/network-zhi-jiang/Zhi%20Jiang.inp'
CHINA_SHA256 = 'sha256:54da00cfe97832f7d94f1cbbe1697d56f95c21053c7fde9cc8afdcac9ecb6563'
CHINA_LICENSE_SHA256 = 'sha256:279f1fd450b056ad5e6d79331194ccd9e63da44cd15d78bb5c4988d778a2a0f9'


def network_path(network_id):
    require(network_id in NETWORKS, 'Unknown public network')
    return (CHINA_DATA if network_id == CHINA_NETWORK else DATA) / NETWORKS[network_id]


def license_files(network_id):
    """Carry the solver license and the selected model's separate data license."""
    files = {'LICENSE-WNTR.md': DATA / 'LICENSE.md'}
    if network_id == CHINA_NETWORK:
        files['LICENSE-ZhiJiang.md'] = CHINA_DATA / 'LICENSE.md'
    return files


def verify_sources():
    for name, (_, expected) in SOURCES.items():
        require(file_hash(DATA/name) == expected, 'Upstream bytes changed: ' + name)
    require(file_hash(network_path(CHINA_NETWORK)) == CHINA_SHA256, 'Upstream bytes changed: ZhiJiang.inp')
    require(file_hash(CHINA_DATA / 'LICENSE.md') == CHINA_LICENSE_SHA256, 'Zhi Jiang attribution/license changed')


def fetch_sources():
    """Explicit restore/download; simulation itself is offline."""
    from waterguard_contracts.core import digest_bytes
    DATA.mkdir(parents=True, exist_ok=True)
    for name, (relative, expected) in SOURCES.items():
        with urllib.request.urlopen(BASE + relative, timeout=60) as response:
            content = response.read()
        require(digest_bytes(content) == expected, 'Downloaded source hash mismatch: ' + name)
        (DATA/name).write_bytes(content)
    CHINA_DATA.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(CHINA_URL, timeout=60) as response:
        content = response.read()
    require(digest_bytes(content) == CHINA_SHA256, 'Downloaded source hash mismatch: ZhiJiang.inp')
    network_path(CHINA_NETWORK).write_bytes(content)
    verify_sources()


def manifest(network_id):
    require(network_id in NETWORKS, 'Unknown public network')
    verify_sources()
    value = load(ROOT/'data/manifests'/f'{network_id}.json')
    validate('NetworkManifest', value)
    require(value['sha256'] == file_hash(network_path(network_id)), 'Manifest network hash mismatch')
    for overlay in value['overlays']:
        require(overlay['sha256'] == file_hash(ROOT/overlay['ref']), 'Overlay hash mismatch')
    return value


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    if args.fetch:
        fetch_sources()
    verify_sources()
    print('Verified pinned Net1, Net3 and China Zhi Jiang source bytes.')
