"""JSON file boundary for backend integration; no candidate generation or AGH emulation."""
import argparse
import json
from pathlib import Path
from .runtime import PhysicalAdapter
from .adapter import write
from .scenarios import case
from waterguard_contracts.core import load


def main():
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest='command',required=True)
    example=commands.add_parser('example')
    example.add_argument('--network',choices=['epa-net1','epa-net3'],default='epa-net1')
    example.add_argument('--kind',choices=['normal','closure','leak','rupture','isolation','pump-cycle','link-cycle'],default='leak')
    example.add_argument('--engine',choices=['wntr','epanet'],default='wntr')
    example.add_argument('--output',type=Path,required=True)
    simulate=commands.add_parser('simulate')
    simulate.add_argument('--task',type=Path,required=True)
    simulate.add_argument('--plan',type=Path,required=True)
    simulate.add_argument('--output',type=Path,required=True)
    simulate.add_argument('--timeout',type=float)
    args=parser.parse_args()
    if args.command=='example':
        task,plan=case(args.network,args.kind,args.engine)
        write(args.output/'task.json',task)
        write(args.output/'plan.json',plan)
        return 0
    result=PhysicalAdapter(args.output).simulate(load(args.task),load(args.plan),timeout_s=args.timeout)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result['artifact']['status']=='SUCCEEDED' else 1


if __name__=='__main__':
    raise SystemExit(main())
