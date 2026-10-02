import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waterguard_contracts import FixtureAdapter, check_bundle, load, verify_fixture
from waterguard_contracts.core import FIXTURES

check_bundle()
task, catalog, scene = [load(FIXTURES / name) for name in ('task.json','action-catalog.json','scene.json')]
adapter = FixtureAdapter()
print('WaterGuard day-1 / FIXTURE / no AGH or hydraulic execution')
for name in ('a', 'b'):
    plan = load(FIXTURES / f'candidate-{name}.json')
    result = adapter.simulate(task, plan)
    report = verify_fixture(task, plan, result, catalog, scene)
    print(json.dumps(dict(candidate_id=plan['candidate_id'], metrics=report['metrics'], admission=report['admission']), ensure_ascii=False, indent=2))
print('Flow complete. Recommendation remains blocked: real simulation, stress tests and native AGH trace are missing.')
