"""Small, public projections of recorded AGH tool events (never raw arguments/text)."""
from datetime import datetime, timezone
import json
import math
import re

IDENTIFIER = re.compile(r'^[\w:./-]{1,180}$')


def identifier(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def candidate_summary(plan):
    if not isinstance(plan, dict) or not identifier(plan.get('candidate_id')):
        return None
    actions = []
    for action in plan.get('actions', []):
        if not isinstance(action, dict):
            continue
        kind, link = identifier(action.get('kind')), identifier(action.get('link_id'))
        when = action.get('simulation_time_s')
        if kind and link and isinstance(when, (int, float)) and not isinstance(when, bool) and math.isfinite(when):
            actions.append({'kind': kind, 'link_id': link, 'simulation_time_s': when})
    return {'candidate_id': plan['candidate_id'], 'parent_candidate_id': identifier(plan.get('parent_candidate_id')), 'actions': actions}


def assessment_summary(report):
    if not isinstance(report, dict):
        return None
    admission = report.get('admission') or {}
    metrics = report.get('metrics') or {}
    count = report.get('violation_count')
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= len(report.get('violations') or []) else len(report.get('violations') or [])
    return {'validity': report.get('validity') if report.get('validity') in {'valid', 'invalid'} else None,
            'admission': {k: admission[k] for k in ('nominal_constraints_pass', 'required_stress_tests_pass', 'recommendation_eligible')
                          if admission.get(k) in {'pass', 'fail', 'pending'}},
            'metrics': {k: metrics[k] for k in ('min_pressure_m', 'supply_ratio', 'shortage_m3', 'min_node_time_pass_ratio', 'mean_node_time_pass_ratio')
                        if isinstance(metrics.get(k), (int, float)) and not isinstance(metrics[k], bool) and math.isfinite(metrics[k])},
            'violation_count': count}


def project_tool_event(event, sequence, session, calls):
    data = event.get('data', {})
    if not isinstance(data, dict) or event.get('type') not in {'tool/call', 'tool/result'}:
        return None
    use_id = identifier(data.get('toolUseId'))
    if not use_id:
        return None
    if event['type'] == 'tool/call':
        name = identifier(data.get('name'))
        if not name or not name.startswith('wg_'):
            return None
        args = data.get('args') if isinstance(data.get('args'), dict) else {}
        plan = args.get('plan') if isinstance(args.get('plan'), dict) else {}
        if isinstance(args.get('plan_json'), str):
            try:
                parsed = json.loads(args['plan_json'])
                if isinstance(parsed, dict):
                    plan = parsed
            except (ValueError, TypeError):
                pass
        calls[use_id] = {'tool_name': name, 'target_run_id': identifier(args.get('run_id')),
                         'candidate_id': identifier(plan.get('candidate_id') or args.get('candidate_id')),
                         'parent_candidate_id': identifier(plan.get('parent_candidate_id'))}
        if candidate_summary(plan):
            calls[use_id]['candidate_plan'] = candidate_summary(plan)
    context = calls.get(use_id)
    if not context:
        return None  # An orphan result cannot prove a call happened in this run.
    value = data.get('structured')
    rejected = isinstance(value, dict) and value.get('accepted') is False
    refusal_summary = {'INVALID_REVISION': '方案修订被拒绝 · INVALID_REVISION',
                       'INVALID_ACTION': '动作被拒绝 · INVALID_ACTION',
                       'REVISION_REQUIRED': '需要先完成失败方案修订'}.get(value.get('code'), '请求被服务端拒绝') if rejected else None
    row = dict(id=f"{event['type']}:{use_id}", sequence=sequence, occurred_at=event.get('ts'),
               native_tool_call_ref=f'agh:{session}:tool:{use_id}', **context,
               type='tool.started' if event['type'] == 'tool/call' else 'tool.completed',
               failed=bool(data.get('isError')) or rejected,
               summary=refusal_summary)
    if event['type'] != 'tool/result' or row['failed']:
        return row
    name = context['tool_name']
    if name == 'wg_jev_decide' and isinstance(value, dict):
        row['jev_configured'] = value.get('configured') is True
        choice = ((value.get('answers') or {}).get('next') or {}).get('choice')
        row['summary'] = ('决策：' + choice if identifier(choice) else '决策已返回') if row['jev_configured'] else '未连接 · Agnes 继续规划'
        if row['jev_configured'] and identifier(choice):
            row['jev_choice'] = choice
    elif name == 'wg_submit_plan' and isinstance(value, dict):
        row['target_run_id'] = identifier(value.get('run_id'))
        plan = candidate_summary(value.get('candidate_plan'))
        if plan:
            row.update(candidate_id=plan['candidate_id'], parent_candidate_id=plan['parent_candidate_id'], candidate_plan=plan)
    elif name == 'wg_generate_candidates' and isinstance(value, list):
        row['summary'] = f'返回 {len(value)} 个候选'
        row['candidates'] = [summary for plan in value if (summary := candidate_summary(plan))]
    elif name == 'wg_revise_plan' and isinstance(value, dict):
        plan = candidate_summary(value.get('plan'))
        if plan:
            row.update(candidate_id=plan['candidate_id'], parent_candidate_id=plan['parent_candidate_id'],
                       candidate_plan=plan, summary='基于实际失败修订动作')
    elif name == 'wg_diagnose_failure' and isinstance(value, dict):
        row['candidate_id'] = identifier(value.get('source_candidate_id'))
        row['summary'] = f"失效 {value.get('affected_node_count', 0)} 个节点 · {value.get('failed_node_seconds', 0)} 节点秒"
    elif name == 'wg_inspect_network' and isinstance(value, dict) and isinstance(value.get('scene'), dict):
        scene = value['scene']
        row['summary'] = f"{len(scene.get('nodes', []))} 个节点 · {len(scene.get('links', []))} 条管段"
    elif name == 'wg_simulate_candidate' and isinstance(value, dict):
        results = value.get('results') or []
        if results:
            row['candidate_id'] = identifier(results[0].get('plan', {}).get('candidate_id'))
            row['simulation_id'] = identifier((results[0].get('artifact') or {}).get('simulation_id'))
    elif name == 'wg_verify_candidate' and isinstance(value, list) and value:
        report = value[0]
        row['candidate_id'] = identifier(report.get('candidate_id'))
        row['simulation_id'] = identifier(report.get('simulation_id'))
        admission = report.get('admission') or {}
        row['summary'] = {'pass': '名义约束通过', 'fail': '名义约束未通过'}.get(admission.get('nominal_constraints_pass'), '验收报告已返回')
        row['assessment'] = assessment_summary(report)
    return row


def execution_trace(store, run_id):
    run = store.get_run(run_id)
    rows, calls = [], {}
    root = store._run_directory(run_id)
    if run['mode'] == 'live' and run['agh_session_ref']:
        paths = [p for p in (root / 'agh' / 'projection').glob('*.json') if p.stem.isdecimal()]
        for path in sorted(paths, key=lambda p: int(p.stem)):
            event = store._read_json(path)
            row = project_tool_event(event, int(path.stem), run['agh_session_ref'], calls)
            if row:
                rows.append(row)
    evidence = None
    manifest = root / 'evidence-manifest.json'
    if manifest.is_file() and run['status'] in {'COMPLETED', 'ERROR', 'CANCELLED', 'BUDGET_EXHAUSTED', 'SUPERSEDED'}:
        bundle = store.get_evidence(run_id)
        evidence = {'bundle_id': bundle['bundle_id'], 'occurred_at': datetime.fromtimestamp(manifest.stat().st_mtime, timezone.utc).isoformat()}
    return {'run_id': run_id, 'agh_session_ref': run['agh_session_ref'], 'events': rows, 'evidence': evidence}
