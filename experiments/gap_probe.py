"""Inspect the residual service gap of a delivered first-aid plan using observed flow evidence."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packages/contracts'))
from services.api.hydraulic_evidence import observed_failure_effects
from services.api.workbench import network_details
from services.verifier.service import verify

folder = Path(sys.argv[1])
candidate = sys.argv[2]
task = json.loads((folder / 'task.json').read_text(encoding='utf-8'))
details = network_details(task['network_ref']['network_id'])
for sim in sorted((folder / 'simulations').iterdir()):
    plan = json.loads((sim / 'plan.json').read_text(encoding='utf-8'))
    if plan['candidate_id'] != candidate:
        continue
    result = json.loads((sim / 'result.json').read_text(encoding='utf-8'))
    report = verify(task, plan, result, details['catalog'], details['scene'], artifact_dir=sim)
    rows = report['metrics']['node_availability']
    unserved = sorted(r['node_id'] for r in rows if r['emergency_supply_duration_s'] < r['assessed_duration_s'])
    print('candidate', candidate, 'actions', plan['actions'])
    print('unserved nodes', len(unserved), unserved)
    never = [r['node_id'] for r in rows if r['emergency_supply_duration_s'] == 0]
    print('never served', len(never), never)
    ids = [f['link_id'] for f in details['catalog']['facilities'] if f['link_id'] in task['action_limits']['allowed_link_ids']]
    effects = observed_failure_effects(task, report, sim, details['scene'], details['catalog'], ids)
    for e in effects:
        print(e['link_id'], e['direction'], 'share=%.2f' % e['downstream_failed_demand_share'], 'feeds_fault=', e['feeds_fault'], len(e['downstream_failed_nodes']))
    break
