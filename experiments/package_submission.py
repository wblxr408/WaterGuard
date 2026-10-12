"""Package a reviewable working snapshot and matched, actually verified evidence.

This creates a local archive. It does not submit a contest entry or sign team
declarations. Model credentials, environments and Git internals are excluded.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from waterguard_contracts.core import digest_bytes, require


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd)


def sources():
    raw = git('ls-files', '--cached', '--others', '--exclude-standard', '-z')
    candidates = [ROOT / item for item in raw.decode('utf-8').split('\0') if item]
    vendor = ROOT / 'vendor/agnes-harness'
    candidates += [vendor / item for item in git('ls-files', '-z', cwd=vendor).decode('utf-8').split('\0') if item]
    selected = []
    for path in sorted(set(candidates)):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        require(path.resolve().is_relative_to(ROOT.resolve()), 'Source path escapes workspace')
        if any(part in {'.git', '.cache', '.venv', 'node_modules', '__pycache__', 'artifacts'} for part in relative.parts):
            continue
        if path.name == '.env' or path.suffix in {'.sqlite3', '.db', '.pem', '.key'}:
            continue
        selected.append(('source/' + relative.as_posix(), path))
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--acceptance', type=Path, default=ROOT / 'artifacts/contest-acceptance-v4/summary.json')
    parser.add_argument('--lab', type=Path, default=ROOT / 'artifacts/scenario-lab/diagnosis.json')
    parser.add_argument('--physics', type=Path, default=ROOT / 'artifacts/contest-physics-v2-summary.json')
    parser.add_argument('--physics-zip', type=Path, default=ROOT / 'artifacts/contest-physics-v2.zip')
    parser.add_argument('--video', type=Path, default=ROOT / 'artifacts/contest-video/WaterGuard-demonstration.mp4')
    parser.add_argument('--ui-verification', type=Path, default=ROOT / 'artifacts/contest-ui-revalidation/verification.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/WaterGuard-contest-submission.zip')
    args = parser.parse_args()
    acceptance, lab, physics = read(args.acceptance), read(args.lab), read(args.physics)
    require({c['name'] for c in acceptance['cases']} ==
        {'normal', 'boundary', 'hydraulic_failure', 'invalid_action', 'holdout_leak', 'holdout_isolation'}, 'Six actual cases required')
    require(lab['complete_scenario_matrix'] and lab['source_summary_sha256'] == digest_bytes(args.acceptance.read_bytes()), 'Diagnosis uses different runs')
    require(not physics['invalid_cases'] and physics['numerical_resolution_assessment']['passed'], 'Physical validation incomplete')
    ui = read(args.ui_verification)
    require(ui['passed'] and len(ui['cases']) == 6 and
            ui['acceptance_summary_sha256'] == digest_bytes(args.acceptance.read_bytes()), 'Missing matched UI revalidation')
    require(ui['observations_sha256'] == digest_bytes((args.ui_verification.parent/'observations.json').read_bytes()), 'Browser proof changed')
    require(set(ui['changed_sources']) == {'apps/web-gpt/src/App.tsx', 'apps/web-gpt/src/Pages.tsx'}, 'UI exception expanded into execution changes')
    for name, expected in acceptance['source_hashes'].items():
        current = digest_bytes((ROOT / name).read_bytes())
        patch = ui['changed_sources'].get(name)
        require(current == expected or (patch and patch['executed_sha256'] == expected and
                patch['verified_sha256'] == current), 'Executed source changed without targeted validation: ' + name)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
        '-of', 'json', str(args.video)], text=True))
    duration = float(probe['format']['duration'])
    require(180 <= duration <= 300, 'Demonstration must last 3–5 minutes')
    require(any(s['codec_type'] == 'video' for s in probe['streams']), 'Missing actual video stream')
    render = read(args.video.parent/'render.json')
    recording = read(args.video.parent/'video-capture.json')
    require(render['video_sha256'] == digest_bytes(args.video.read_bytes()) and
            render['recording_sha256'] == digest_bytes((args.video.parent/'video-capture.json').read_bytes()), 'Video differs from original recording receipt')
    require(abs(render['encoded_duration_s']-duration)<.04, 'Video duration receipt mismatch')
    actual_ids = {c['live']['run_id'] for c in acceptance['cases']}
    require(all(any(run_id in part['url'] for run_id in actual_ids) for part in recording['parts']), 'Video depicts unaudited runs')
    for path in [args.physics_zip, *(args.acceptance.parent / (c['live']['run_id'] + '.zip') for c in acceptance['cases'])]:
        with zipfile.ZipFile(path) as archive:
            require(archive.testzip() is None, 'Evidence ZIP CRC failure')
    entries = sources()
    entries += [('evidence/physics/summary.json', args.physics), ('evidence/physics/verified-results.zip', args.physics_zip),
                ('video/WaterGuard-demonstration.mp4', args.video)]
    for folder, prefix in [(args.acceptance.parent, 'evidence/acceptance'), (args.lab.parent, 'evidence/scenario-lab'),
                           (args.ui_verification.parent, 'verification/browser-routes'),
                           (ROOT/'artifacts/contest-budget-probe', 'verification/budget'),
                           (args.video.parent, 'video')]:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path != args.video and (folder != args.video.parent or path.suffix in {'.json', '.ass', '.txt', '.jpg'}):
                entries.append((prefix + '/' + path.relative_to(folder).as_posix(), path))
    for log in ('contest-api-tests-v4.log', 'contest-hydraulics-tests.log', 'contest-live-tests-v4.log',
                'contest-auxiliary-tests.log', 'contest-contract-tests.log'):
        path = ROOT / 'artifacts' / log
        if path.is_file():
            entries.append(('verification/' + log, path))
    require(len({name for name, _ in entries}) == len(entries), 'Duplicate archive entry')
    manifest = dict(schema_version='1.0.0', created_at=datetime.now(timezone.utc).isoformat(),
        git_base_sha=git('rev-parse', 'HEAD').decode().strip(), code_state='uncommitted working snapshot; file hashes are authoritative',
        agh_source_sha=git('rev-parse', 'HEAD', cwd=ROOT / 'vendor/agnes-harness').decode().strip(),
        acceptance_summary_sha256=digest_bytes(args.acceptance.read_bytes()),
        frontend_patch_validation=ui,
        video_duration_s=duration, technical_evidence_ready=True, submission_ready=False,
        demonstration_method=render['interpretation'],
        outstanding_team_items=['Actual group, team name, members, school/major and contribution confirmation',
                                'All-member independent completion statement and actual AI assistance disclosure'],
        artifacts={name: dict(sha256=digest_bytes(path.read_bytes()), bytes=path.stat().st_size) for name, path in entries})
    quickstart = f'''# WaterGuard 赛事交付快照

本包保留已执行源码快照、六个真实 AGH 场景、26 个物理验证案例、诊断对照和实际操作视频。
源码以 submission-manifest.json 的文件哈希为准，Git 基线为 {manifest['git_base_sha']}。
正式运行 ID 与视频内容见 evidence/acceptance/summary.json 和 video/video-capture.json。
source/benchmarks 内已有资料包含历史版本，正式赛事复核使用 evidence/acceptance 下本轮记录。
六例核心执行源码冻结后，仅修复运行链接初始加载和补充完整 ZIP 下载；AGH、API、求解与验证源码保持执行哈希。
这两处页面改进已用六例实际浏览器记录和实际 ZIP 下载复核，见 verification/browser-routes。

## 安装与运行

需要 Python 3.11、Node >=24.10、Git、pnpm。依赖下载与模型请求需要网络，离线数值复核使用包中原始数据。
解压后进入 source，按 docs/AGH基座与启动.md 安装 Python、前端与 AGH 依赖并构建官方基座。
vendor/agnes-harness 包含官方源码快照；若需要 Git 身份记录，可在该目录执行 git init，
再从 https://github.com/AgnesAI-Labs/agnes-harness.git 获取固定提交 {manifest['agh_source_sha']}，
使用 git reset --mixed FETCH_HEAD 建立对应索引，保留已提供的源码文件。
根目录若没有 Git 元数据，可 git init 后按源码快照建立本地版本；当前试验源码身份仍按清单哈希校验。

分别运行 .venv/Scripts/python.exe scripts/start_agh.py 与 npm run start:fullstack。
首次在模型账号界面配置自己的有效账号。交付包不含账号 Key，也不自动发送或提交参赛材料。
工作台 http://127.0.0.1:5173/；API http://127.0.0.1:8010/；AGH http://127.0.0.1:4180/。
重新执行真实矩阵：.venv/Scripts/python.exe scripts/contest_acceptance.py --output artifacts/new-acceptance。
重新诊断：.venv/Scripts/python.exe experiments/scenario_lab.py --summary artifacts/new-acceptance/summary.json。
复核文档位于 source/docs/赛事逐项验收与评分分析.md 和 source/docs/参赛提交说明.md。

## 完成边界

技术证据已打包；参赛组别、队名、成员学校专业、真实贡献、全员声明仍需团队填写确认。
诊断证明有限动作域中的结果，不保证评委分数、全局最优、现场校准或水锤控制。
'''
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.building.zip')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, path in entries:
            data = path.read_bytes()
            require(digest_bytes(data) == manifest['artifacts'][name]['sha256'], 'File changed while packaging: ' + name)
            archive.writestr(name, data)
        archive.writestr('submission-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('README.md', quickstart)
    with zipfile.ZipFile(temporary) as archive:
        require(archive.testzip() is None, 'Submission ZIP CRC failure')
    temporary.replace(args.output)
    receipt = dict(path=str(args.output), sha256=digest_bytes(args.output.read_bytes()),
                   files=len(entries)+2, bytes=args.output.stat().st_size, video_duration_s=duration,
                   technical_evidence_ready=True, submission_ready=False, outstanding_team_items=manifest['outstanding_team_items'])
    args.output.with_suffix('.receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
