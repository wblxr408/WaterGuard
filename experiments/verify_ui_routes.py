"""Reconcile a UI-only routing repair with unchanged executed AGH evidence.

Browser observations are captured through cua_repl, not invented by this script.
The original execution freeze and original archives stay immutable.
"""
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.contest_acceptance import ROOT, source_hashes, load, write, require, digest_bytes


def run():
    acceptance_path = ROOT / 'artifacts/contest-acceptance-v4/summary.json'
    observations_path = ROOT / 'artifacts/contest-ui-revalidation/observations.json'
    original = load(acceptance_path)
    observations = load(observations_path)
    current = source_hashes()
    changed = {name for name in current if current[name] != original['source_hashes'].get(name)}
    require(changed == {'apps/web-gpt/src/App.tsx', 'apps/web-gpt/src/Pages.tsx'}, 'Revalidation only permits the observed UI routing and evidence export repairs')
    require(set(current) == set(original['source_hashes']), 'Execution source inventory changed')
    require({o['name'] for o in observations} == {c['name'] for c in original['cases']} and
            len(observations) == len(original['cases']) == 6, 'Six distinct actual browser observations required')
    verified = []
    for case in original['cases']:
        observed = next(o for o in observations if o['name'] == case['name'])
        expected_id = case['live']['run_id']
        require(observed['requested_run_id'] == expected_id and
                parse_qs(urlparse(observed['url']).query)['run'] == [expected_id], 'Browser loaded a different run')
        text = observed['snapshot']
        require('Description: 数据源, Value: 任务运行结果' in text, 'Browser fell back to a benchmark')
        require('压力 ≥ 20.00 m · 供需比 ≥ 95.00%' in text, 'Displayed frozen thresholds changed')
        nominal = [r for r in case['live']['candidates'] if r['perturbation'] is None]
        require(text.count('button 查看验收报告') == len(nominal), 'Browser omitted a nominal candidate')
        for row in nominal:
            metrics = row['metrics']
            expected = (f"缺供体积 {metrics['shortage_m3']:,.2f} m³ 最低节点压力 "
                        f"{metrics['min_pressure_m']:,.2f} m 总体供需比 {metrics['supply_ratio']*100:,.2f} %")
            require(expected in text, 'Browser metrics disagree with raw verification: ' + row['candidate_id'])
        eligible = case['live']['conclusion'] == 'FEASIBLE_FOUND'
        require(('验收接口允许推荐' if eligible else '验收尚未允许推荐') in text, 'Browser/host recommendation mismatch')
        screenshot = Path(observed['screenshot'])
        require(screenshot.resolve().is_relative_to((ROOT/'artifacts').resolve()) and screenshot.is_file(), 'Missing local browser proof')
        verified.append(dict(name=case['name'], run_id=expected_id, observed_at=observed['observed_at'],
                             nominal_candidates=len(nominal), conclusion=case['live']['conclusion'],
                             screenshot_sha256=digest_bytes(screenshot.read_bytes())))
    downloaded = load(observations_path.parent/'download-observation.json')
    normal = next(c for c in original['cases'] if c['name'] == 'normal')
    require(downloaded['run_id'] == normal['live']['run_id'] and
            'link Description: 下载完整证据包' in downloaded['snapshot'], 'No actual browser ZIP download')
    archive = Path(downloaded['downloaded_path'])
    audited = acceptance_path.parent/(normal['live']['run_id']+'.zip')
    require(archive.is_file() and digest_bytes(audited.read_bytes()) == normal['live']['evidence_sha256'], 'Missing audited download reference')
    with zipfile.ZipFile(archive) as browser_zip, zipfile.ZipFile(audited) as original_zip:
        require(browser_zip.testzip() is None and set(browser_zip.namelist()) == set(original_zip.namelist()), 'Browser ZIP is incomplete')
        require(all(digest_bytes(browser_zip.read(name)) == digest_bytes(original_zip.read(name))
                    for name in original_zip.namelist()), 'Browser archive contents differ from audited evidence')
    result = dict(schema_version='1.0.0', passed=True,
        acceptance_summary_sha256=digest_bytes(acceptance_path.read_bytes()),
        observations_sha256=digest_bytes(observations_path.read_bytes()),
        interpretation='AGH/API/verifier/physics execution sources are unchanged. Initial route hydration is independently revalidated against six actual archived runs; all actual browser ZIP entries are byte-identical to audited evidence. ZIP container timestamps can differ.',
        audited_container_sha256=normal['live']['evidence_sha256'],
        download_evidence_sha256=digest_bytes(archive.read_bytes()),
        changed_sources={name: dict(executed_sha256=original['source_hashes'][name],
                                   verified_sha256=current[name]) for name in changed},
        unchanged_execution_source_count=len(current)-len(changed), cases=verified)
    write(ROOT/'artifacts/contest-ui-revalidation/verification.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    run()
