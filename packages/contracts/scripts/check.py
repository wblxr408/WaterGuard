import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waterguard_contracts import check_bundle

reports = check_bundle()
print('PASS: schemas, references, hashes, event ordering, provenance and independent metric recomputation.')
for report in reports:
    print(report['candidate_id'], report['metrics'], report['admission'])
