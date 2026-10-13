"""Summarize a headroom sweep: fixed-order quality at small budgets vs best tested plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def frozen_key(row):
    return (-(row['min_node_time_pass_ratio'] or 0), -(row['mean_node_time_pass_ratio'] or 0), row['shortage_m3'])


def livelihood_key(row):
    return (-(row['emergency_min_node_time_pass_ratio'] or 0), -(row['nodes_reaching_emergency_service'] or 0),
            -(row['emergency_mean_node_time_pass_ratio'] or 0), -(row['mean_node_time_pass_ratio'] or 0), row['shortage_m3'])


def analyse(folder: Path):
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    if summary.get('skipped'):
        return dict(id=folder.name, skipped=summary['skipped'])
    rows = [r for r in json.loads((folder / 'rows.json').read_text(encoding='utf-8')) if r['validity'] == 'valid']
    if not rows:
        return dict(id=folder.name, skipped='no_valid_rows')
    fixed = [r for r in rows if r['origin'] == 'fixed']
    base = rows[0]

    def best(sub, key=frozen_key):
        return min(sub, key=key)
    out = dict(id=folder.name, base=base, n=len(rows))
    for k in (4, 8, 16):
        out[f'fixed{k}'] = best(fixed[:k])
    out['fixed_all'] = best(fixed)
    out['oracle'] = best(rows)
    out['oracle_l'] = best(rows, livelihood_key)
    out['fixed4_l'] = best(fixed[:4], livelihood_key)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folders', nargs='+', type=Path)
    parser.add_argument('--threshold', type=float, default=0.01)
    args = parser.parse_args()
    results = []
    for root in args.folders:
        for folder in sorted(p for p in root.iterdir() if (p / 'summary.json').exists()):
            results.append(analyse(folder))
    print('scenario | baseline | fixed@4 | fixed@8 | fixed@all | best | shortage base/fixed4/best | n')
    headroom = []
    for r in results:
        if r.get('skipped'):
            print(f"{r['id']:28} SKIP {r['skipped']}")
            continue
        gap = r['oracle']['mean_node_time_pass_ratio'] - r['fixed4']['mean_node_time_pass_ratio']
        lgap = r['oracle_l']['nodes_reaching_emergency_service'] - r['fixed4_l']['nodes_reaching_emergency_service']
        flag = ''
        if gap > args.threshold or lgap > 0:
            flag = f"  <== headroom mean+{gap:.3f} emergency_nodes+{lgap}"
            headroom.append(r['id'])
        print(f"{r['id']:28} {r['base']['mean_node_time_pass_ratio']:.3f} {r['fixed4']['mean_node_time_pass_ratio']:.3f} "
              f"{r['fixed8']['mean_node_time_pass_ratio']:.3f} {r['fixed_all']['mean_node_time_pass_ratio']:.3f} "
              f"{r['oracle']['mean_node_time_pass_ratio']:.3f} | {r['base']['shortage_m3']:.2f}/{r['fixed4']['shortage_m3']:.2f}/"
              f"{r['oracle']['shortage_m3']:.2f} | {r['n']}{flag}")
    valid = [r for r in results if not r.get('skipped')]
    print(f"\nvalid scenarios {len(valid)}, with headroom {len(headroom)}: {headroom}")


if __name__ == '__main__':
    main()
