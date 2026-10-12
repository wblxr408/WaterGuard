"""Evidence-backed scenario diagnosis and equal-budget strategy comparison.

Consumes actual archived AGH runs. It never edits runs, changes acceptance
thresholds, invents jury scores, or treats an improving action as a safe one.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from waterguard_contracts.core import digest_bytes, require, task_hash

LABELS = {'normal': '正常保供', 'boundary': '中国管网断裂', 'hydraulic_failure': '水力失败与修正',
          'invalid_action': '非法动作恢复', 'holdout_leak': '留出泄漏', 'holdout_isolation': '留出隔离'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def episodes(violations):
    """Merge only adjacent intervals of the same node, constraint and threshold."""
    groups = defaultdict(list)
    for violation in violations:
        groups[(violation['node_id'], violation['code'], violation['threshold'], violation['unit'])].append(violation)
    merged = []
    for (node, code, threshold, unit), rows in sorted(groups.items()):
        current = None
        for row in sorted(rows, key=lambda r: (r['start_s'], r['end_s'])):
            require(row['end_s'] > row['start_s'], 'Nonpositive violation interval')
            if current is not None and row['start_s'] < current['end_s']:
                raise ValueError('Overlapping violation intervals cannot be double-counted')
            if current is not None and row['start_s'] == current['end_s']:
                current['end_s'] = row['end_s']
                current['worst_observed'] = min(current['worst_observed'], row['observed'])
                current['duration_s'] += row['end_s'] - row['start_s']
                current['interval_count'] += 1
            else:
                current = dict(node_id=node, code=code, threshold=threshold, unit=unit,
                    start_s=row['start_s'], end_s=row['end_s'], duration_s=row['end_s']-row['start_s'],
                    worst_observed=row['observed'], interval_count=1)
                merged.append(current)
    require(sum(row['duration_s'] for row in merged) ==
            sum(row['end_s']-row['start_s'] for row in violations), 'Violation duration not conserved')
    return sorted(merged, key=lambda r: (-r['duration_s'], r['code'], r['worst_observed'], r['node_id']))


def dominated(a, b):
    """Whether b strictly dominates a on observed shortage, pressure and action cost."""
    axes = [('shortage_m3', 1), ('min_pressure_m', -1), ('action_cost', 1)]
    if any(a.get(k) is None or b.get(k) is None for k, _ in axes):
        return False
    return all(sign * b[k] <= sign * a[k] for k, sign in axes) and any(sign * b[k] < sign * a[k] for k, sign in axes)


def inspect_case(case, folder):
    live = case['live']
    payload = (folder / (live['run_id'] + '.zip')).read_bytes()
    require(digest_bytes(payload) == live['evidence_sha256'], 'Archived run differs from audited ZIP')
    with zipfile.ZipFile(folder / (live['run_id'] + '.zip')) as archive:
        require(archive.testzip() is None, 'ZIP integrity failure')
        fetch = lambda name: json.loads(archive.read(name))
        task = fetch('task.json')
        require(task_hash(task) == case['task_ref'] == task['constraint_hash'], 'Task freeze mismatch')
        require(task['hard_constraints']['min_pressure_m'] == 20 and
                task['hard_constraints']['min_supply_ratio'] == .95, 'Unexpected engineering gate')
        manifest = fetch('evidence-manifest.json')
        for item in manifest['artifacts']:
            require(digest_bytes(archive.read(item['ref'].removeprefix('artifact:'))) == item['sha256'], 'Archive file hash mismatch')
        candidates = []
        for observed in (row for row in live['candidates'] if row['perturbation'] is None):
            cid = observed['candidate_id']
            report = fetch(f'candidates/{cid}/verification.json')
            plan = fetch(f'candidates/{cid}/plan.json')
            require(report['metrics'] == observed['metrics'], 'Archive/live metric disagreement')
            merged = episodes(report['violations'])
            by_node = defaultdict(lambda: dict(pressure_s=0, supply_s=0))
            for row in merged:
                by_node[row['node_id']]['pressure_s' if row['code'] == 'MIN_PRESSURE' else 'supply_s'] += row['duration_s']
            candidates.append(dict(candidate_id=cid, actions=plan['actions'], plan_hash=plan['plan_hash'],
                simulation_id=observed['simulation_id'], metrics=report['metrics'], admission=report['admission'],
                blockers=report['recommendation_blockers'], validity=report['validity'],
                violation_intervals=len(report['violations']), violation_episodes=len(merged),
                total_constraint_node_time_s=sum(r['duration_s'] for r in merged),
                worst_episodes=merged[:10], affected_nodes=dict(sorted(by_node.items()))))
        feasible = [c for c in candidates if c['admission']['recommendation_eligible'] == 'pass']
        require(bool(feasible) == (live['conclusion'] == 'FEASIBLE_FOUND'), 'Host/diagnosis eligibility disagreement')
        # Pareto is descriptive over tested, physically valid nominal reports only.
        valid = [c for c in candidates if c['validity'] == 'valid']
        pareto = [c['candidate_id'] for c in valid if not any(dominated(c['metrics'], b['metrics']) for b in valid)]
        best = min(valid, key=lambda c: (c['metrics']['shortage_m3'], c['metrics']['action_cost'], c['candidate_id']))
        base = next((c for c in candidates if not c['actions']), None)
        improvement = None if base is None else dict(
            best_candidate_id=best['candidate_id'],
            shortage_reduction_m3=base['metrics']['shortage_m3']-best['metrics']['shortage_m3'],
            worst_pressure_change_m=best['metrics']['min_pressure_m']-base['metrics']['min_pressure_m'],
            action_cost_change=best['metrics']['action_cost']-base['metrics']['action_cost'],
            recommendation_eligible=best['admission']['recommendation_eligible'] == 'pass')
    fixed = read(folder / (case['name'] + '-fixed/comparison.json'))
    nominal_fixed = [r for r in fixed['records'] if r['perturbation'] is None and r['report']['validity'] == 'valid']
    fixed_best = min(nominal_fixed, key=lambda r: (r['report']['metrics']['shortage_m3'], r['report']['metrics']['action_cost']))
    require(fixed['simulations'] <= task['budget']['max_simulations'] and
            live['budget_usage']['simulations'] <= task['budget']['max_simulations'], 'Unequal or exceeded simulation cap')
    delta = fixed_best['report']['metrics']['shortage_m3']-best['metrics']['shortage_m3']
    return dict(name=case['name'], label=LABELS[case['name']], run_id=live['run_id'], task_hash=case['task_ref'],
        network_id=task['network_ref']['network_id'], fault=task['fault'], window=task['time_window'],
        frozen_constraints=task['hard_constraints'], budget=task['budget'], conclusion=live['conclusion'],
        selected_candidate_id=live['selected_candidate_id'], model=live['model'],
        candidate_count=len(candidates), pareto_tested_nominal=pareto, candidates=candidates,
        action_improvement=improvement, best_shortage_m3=best['metrics']['shortage_m3'],
        fixed_best_shortage_m3=fixed_best['report']['metrics']['shortage_m3'], shortage_gain_over_fixed_m3=delta,
        fixed_selected_candidate_id=fixed['selected_candidate_id'],
        agh_simulations=live['budget_usage']['simulations'], fixed_simulations=fixed['simulations'],
        agh_wall_time_s=live['budget_usage']['wall_time_s'], fixed_wall_time_s=fixed['wall_time_s'],
        model_calls=live['budget_usage']['model_calls'], evidence_files=live['evidence_files'],
        interpretation='Observed finite test set only. Pareto membership does not imply safety or global optimality.')


def markdown(report):
    rows = ['# WaterGuard 场景验收实验室', '',
        '本报告从实际 AGH ZIP 逐文件核查并提取诊断，冻结工程阈值为 20 m / 95%。'
        '每种策略每场景只有一次运行，结果是描述性对照；不计算显著性、实测概率或评委分数。', '',
        f"完整场景矩阵：{'已齐全' if report['complete_scenario_matrix'] else '尚未齐全'}。"
        '动作改善与推荐准入分别列示，有限候选内未达标不等于全局无解。', '',
        '| 场景 | AGH 结论 | 最佳缺供 m³ | 固定策略缺供 m³ | AGH/固定仿真数 | AGH/固定用时 s |',
        '|---|---|---:|---:|---:|---:|']
    for case in report['cases']:
        rows.append(f"| {case['label']} | {case['conclusion']} | {case['best_shortage_m3']:.3f} | "
                    f"{case['fixed_best_shortage_m3']:.3f} | {case['agh_simulations']}/{case['fixed_simulations']} | "
                    f"{case['agh_wall_time_s']:.1f}/{case['fixed_wall_time_s']:.1f} |")
    rows += ['', '固定策略与 AGH 使用相同冻结任务、候选目录、仿真次数上限和时间预算。'
             '实际用时包含不同的模型/程序开销，表中同时披露。若两者质量一致，不宣称 AI 质量更高。', '',
             '![真实结果与同预算对照](comparison.png)', '', '## 具体实例与失败见证', '']
    for case in report['cases']:
        rows += [f"### {case['label']}", '', f"运行 `{case['run_id']}`；网络 `{case['network_id']}`；"
                 f"任务哈希 `{case['task_hash']}`；实际模型 `{case['model']['model']}`，调用 {case['model_calls']} 次。", '']
        improvement = case['action_improvement']
        if improvement:
            rows += [f"同任务无动作基线到最佳已测方案 `{improvement['best_candidate_id']}`："
                f"缺供减少 {improvement['shortage_reduction_m3']:.3f} m³，最低压力变化 {improvement['worst_pressure_change_m']:+.3f} m。"
                f"该方案{'已满足推荐准入' if improvement['recommendation_eligible'] else '仍不具备推荐准入'}。", '']
        for candidate in case['candidates']:
            action = '无动作' if not candidate['actions'] else '；'.join(
                f"{a['simulation_time_s']/3600:g}h {a['kind']}({a['link_id']})" for a in candidate['actions'])
            rows += [f"候选 `{candidate['candidate_id']}`：{action}。最低压力 {candidate['metrics']['min_pressure_m']:.3f} m；"
                     f"供需比 {candidate['metrics']['supply_ratio']:.3%}；"
                     f"{candidate['violation_intervals']} 个内部违规区间归并为 {candidate['violation_episodes']} 段，"
                     f"涉及 {len(candidate['affected_nodes'])} 个节点。阻断项："
                     f"{', '.join(candidate['blockers']) or '无'}。", '']
            if candidate['worst_episodes']:
                rows += ['| 节点 | 约束 | 时间 h | 持续 s | 最差值 | 冻结阈值 |', '|---|---|---|---:|---:|---:|']
                for e in candidate['worst_episodes'][:5]:
                    rows.append(f"| {e['node_id']} | {e['code']} | {e['start_s']/3600:.3f}–{e['end_s']/3600:.3f} | "
                                f"{e['duration_s']} | {e['worst_observed']:.4f} {e['unit']} | {e['threshold']} {e['unit']} |")
                rows.append('')
    rows += ['## 使用边界', '', '此模块归并已验收结果，不修改原始报告。总约束节点时间会分别计入压力和供水违规，'
             '不能解释成事故持续时间。Pareto 只比较已测候选的缺供、最低压力和动作成本；不合规方案即使位于前沿仍被拒绝。'
             '公开模型与数值验证不提供现场校准、实时硬件控制或连续水锤验证。', '']
    return '\n'.join(rows)


def figure(report, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    available = {font.name for font in font_manager.fontManager.ttflist}
    cjk = next((name for name in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC') if name in available), 'DejaVu Sans')
    plt.rcParams.update({'font.family': cjk, 'font.size': 10, 'axes.unicode_minus': False,
                         'svg.fonttype': 'none', 'axes.spines.top': False, 'axes.spines.right': False})
    cases = report['cases']
    fig, axes = plt.subplots(1, 3, figsize=(12, max(3.8, len(cases)*.53+1.8)), layout='constrained')
    panels = [('best_shortage_m3', 'fixed_best_shortage_m3', '最佳已测缺供 / m³'),
              ('agh_simulations', 'fixed_simulations', '实际仿真次数'),
              ('agh_wall_time_s', 'fixed_wall_time_s', '实际用时 / s')]
    for ax, (left, right, label) in zip(axes, panels):
        for i, case in enumerate(cases):
            ax.plot([case[left], case[right]], [i, i], color='#a7adb2', linewidth=1.4)
        ax.scatter([case[left] for case in cases], range(len(cases)), color='#176e65', marker='o', label='真实 AGH', zorder=3, s=42, clip_on=False)
        ax.scatter([case[right] for case in cases], range(len(cases)), color='#d88b21', marker='x', label='固定策略', zorder=4, s=42, clip_on=False)
        ax.set_yticks(range(len(cases)), [case['label'] for case in cases] if ax is axes[0] else [])
        ax.invert_yaxis()
        ax.set_xlabel(label)
        ax.set_xlim(left=0)
        ax.grid(axis='x', color='#e7ebed', linewidth=.7)
    axes[0].legend(loc='lower left', bbox_to_anchor=(0, 1.01), ncol=2, frameon=False)
    fig.suptitle('同任务、同预算的实际对照 · 每场景每策略一次运行', fontsize=13)
    for suffix in ('png', 'svg', 'pdf'):
        fig.savefig(output / ('comparison.' + suffix), dpi=300)
    save(output / 'figure-spec.json', dict(schema_version='1.0.0', question='Observed paired strategy outcomes',
        experimental_unit='Frozen task scenario', pairing='Identical task hash and action catalogue',
        samples_per_scenario_per_strategy=1, inference='descriptive; no error bars or significance claims',
        chart='Paired individual points; linear scales from zero', panels=panels, font=cjk,
        renderer='matplotlib/' + matplotlib.__version__, input_hash=report['source_summary_sha256']))
    return fig


def run(summary, output, allow_partial=False):
    source = read(summary)
    require(bool(source['cases']), 'No actually audited scenario')
    complete = {case['name'] for case in source['cases']} == set(LABELS)
    require(complete or allow_partial, 'Incomplete scenario matrix; do not publish it as final')
    output.mkdir(parents=True, exist_ok=True)
    report = dict(schema_version='1.0.0', created_at=datetime.now(timezone.utc).isoformat(),
        module_sha256=digest_bytes(Path(__file__).read_bytes()), source_summary_sha256=digest_bytes(summary.read_bytes()),
        complete_scenario_matrix=complete, score_interpretation='Evidence coverage, not a jury score',
        cases=[inspect_case(case, summary.parent) for case in source['cases']])
    save(output / 'diagnosis.json', report)
    (output / 'report.md').write_text(markdown(report), encoding='utf-8')
    figure(report, output)
    print(json.dumps({'complete': complete, 'scenarios': len(report['cases']), 'output': str(output)}, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--summary', type=Path, default=ROOT / 'artifacts/contest-acceptance-v4/summary.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/scenario-lab')
    parser.add_argument('--allow-partial', action='store_true', help='Development inspection; output explicitly marked incomplete')
    args = parser.parse_args()
    run(args.summary, args.output, args.allow_partial)
