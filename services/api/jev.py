"""TypeSafe Jev decisions from server-owned hydraulic state."""
import json
import math
import os
import threading
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .runtime import ROOT, ConflictError

CONFIG = ROOT / '.cache' / 'jev' / 'credentials.json'
LOCK = threading.RLock()
ENDPOINT = 'https://api.typesafe.ai/v1/systemone'

def credentials():
    value = json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.is_file() else {}
    return os.environ.get('TYPESAFE_API_KEY') or value.get('api_key', '')

def status():
    return {'configured': bool(credentials()), 'model': 'jev-latest'}

def unit(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1

def evaluate(state, criteria, key=None):
    key = key or credentials()
    if not key:
        raise ValueError('请在 Jev 决策面板配置 TypeSafe API Key。')
    payload = {'model': 'jev-latest', 'state': json.dumps(state, ensure_ascii=False), 'questions': {
        'next': {'type': 'choice', 'instructions': 'Choose the next candidate to simulate using frozen constraints, actual independent verification and remaining budget. Finish when a verified candidate passes or budget is exhausted.', 'criteria': criteria},
        'risk': {'type': 'noul', 'instructions': 'Do independently verified hydraulic results show unresolved pressure or supply violations? Missing results imply uncertainty, not proof of safety.'}}}
    request = Request(ENDPOINT, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
    started = time.monotonic()
    try:
        with urlopen(request, timeout=35) as response:
            value = json.load(response)
    except HTTPError as error:
        raise ValueError(f'Jev 连接失败（HTTP {error.code}），请检查账号或稍后重试。') from None
    except (URLError, TimeoutError):
        raise ValueError('Jev 连接超时，请稍后重试。') from None
    try:
        answers = value['answers']
        choice, risk = answers['next'], answers['risk']
        probabilities = choice['probabilities']
        if (choice['type'] != 'choice' or choice['choice'] not in criteria or set(probabilities) != set(criteria)
                or not all(unit(p) for p in probabilities.values()) or abs(sum(probabilities.values()) - 1) > .015
                or not unit(choice['confidence']) or risk['type'] != 'noul' or not unit(risk['noul'])
                or not isinstance(value['model'], str) or not value['model'].startswith('jev-')):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('Jev 返回的结构化决策格式无效。') from None
    return {'model': value['model'], 'answers': answers, 'latency_ms': round((time.monotonic() - started) * 1000), 'usage': value.get('usage', {}), 'criteria': criteria}

def configure(key):
    if not key.strip() or '\n' in key or '\r' in key:
        raise ValueError('请输入有效的 TypeSafe API Key。')
    evaluate({'connection_test': True}, {'connected': 'Successful account connection', 'retry': 'Connection not established'}, key.strip())
    with LOCK:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG.with_suffix('.tmp')
        temporary.write_text(json.dumps({'api_key': key.strip()}), encoding='utf-8')
        temporary.replace(CONFIG)
    return status()

def decisions(store, run_id):
    store.get_run(run_id)
    return [store._read_json(path) for path in sorted((store._run_directory(run_id) / 'jev').glob('decision-*.json'))]

def decide(store, parent):
    from .workbench import benchmark_plans, network_details, run_results
    with LOCK:
        current = store.get_run(parent['run_id'])
        if current['status'] != 'RUNNING':
            raise ConflictError('运行已结束。')
        task = store.get_task(current['task_ref']['task_id'], current['task_ref']['revision'])
        from .workflow_guard import workflow_hint
        hint = workflow_hint(store, current)
        if not credentials():
            return {'configured': False, 'decisions': [], 'host_workflow': hint}
        existing = decisions(store, current['run_id'])
        if len(existing) >= 2 * task['budget']['max_simulations'] + 2:
            raise ConflictError('Jev 决策次数已用完。')
        plans = benchmark_plans(task, network_details(task['network_ref']['network_id'])['catalog'])
        from .planning import revision_records
        plans.extend(row['plan'] for row in revision_records(store, current))
        rows = run_results(store, current['run_id'])
        submitted = {row['plan']['candidate_id'] for row in rows}
        remaining = task['budget']['max_simulations'] - current['budget_usage']['simulations']
        available = [plan for plan in plans if plan['candidate_id'] not in submitted] if remaining > 0 else []
        criteria = {plan['candidate_id']: json.dumps({'actions': plan['actions'], 'baseline': not plan['actions']}) for plan in available}
        criteria['finish'] = 'Summarize verified results; choose if a verified result meets constraints or no simulation budget remains.'
        state = {'task_ref': current['task_ref'], 'fault': task['fault'], 'constraints': task['hard_constraints'], 'remaining_simulations': remaining,
                 'results': [{'candidate_id': row['plan']['candidate_id'], 'verification':
                     {key: row['verification'][key] for key in ('validity', 'admission', 'metrics')}
                     | {'violation_count': len(row['verification']['violations'])}
                     if row['verification'] else None} for row in rows], 'host_workflow': hint}
        result = evaluate(state, criteria)
        if store.get_run(current['run_id'])['status'] != 'RUNNING':
            raise ConflictError('运行已结束。')
        result.update({'id': uuid.uuid4().hex, 'sequence': len(existing) + 1, 'run_id': current['run_id'], 'task_ref': current['task_ref'], 'created_at': time.time(), 'state': state})
        store._write_json(f"{current['run_id']}/jev/decision-{result['sequence']:04d}.json", result)
        store.log(current['run_id'], 'Jev 决策：' + result['answers']['next']['choice'], tool_name='wg_jev_decide', event_type='jev.completed')
        return {'configured': True, **result, 'host_workflow': hint, 'candidate': next((plan for plan in available if plan['candidate_id'] == result['answers']['next']['choice']), None)}
