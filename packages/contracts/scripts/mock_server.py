import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waterguard_contracts.mock import make_server

parser = argparse.ArgumentParser(description='Read-only day-1 fixture API')
parser.add_argument('--port', type=int, default=8000)
args = parser.parse_args()
with make_server(args.port) as server:
    print(f'FIXTURE only: http://127.0.0.1:{server.server_port}/api/fixture', flush=True)
    print('No AGH, hydraulic engine, task creation or cancellation. Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
