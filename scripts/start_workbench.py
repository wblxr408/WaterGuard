"""Launch the task API; apps/web-gpt is the sole WaterGuard frontend."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start WaterGuard API at http://127.0.0.1:8010/")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("port_number", nargs="?", type=int, help="Optional port forwarded by npm on Windows")
    args = parser.parse_args()
    local_python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = str(local_python) if local_python.is_file() else sys.executable
    try:
        raise SystemExit(subprocess.call([python, "-m", "uvicorn", "services.api.main:app", "--reload",
                        "--host", "127.0.0.1", "--port", str(args.port_number or args.port)], cwd=ROOT))
    except KeyboardInterrupt:
        raise SystemExit(130)
