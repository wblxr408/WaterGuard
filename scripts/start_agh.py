"""Start WaterGuard on its official AGH base and the local hydraulic API."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "vendor" / "agnes-harness"


def node_runtime():
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin" / ("node.exe" if os.name == "nt" else "node")
    for candidate in (os.environ.get("WG_AGH_NODE"), str(bundled), shutil.which("node")):
        if not candidate or not Path(candidate).is_file():
            continue
        version = subprocess.check_output([candidate, "--version"], text=True).strip()
        numbers = tuple(map(int, re.findall(r"\d+", version)[:2]))
        if numbers >= (24, 10):
            return candidate
    raise RuntimeError("AGH requires Node >=24.10; set WG_AGH_NODE to its executable path")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4180)
    parser.add_argument("--api-port", type=int, default=8000)
    # Windows npm may consume option names and forward only their values.
    parser.add_argument("ports", nargs="*", type=int, help="Optional AGH and API ports forwarded by npm")
    args = parser.parse_args()
    if len(args.ports) > 2:
        parser.error("At most two positional ports are supported")
    if args.ports:
        args.port = args.ports[0]
    if len(args.ports) == 2:
        args.api_port = args.ports[1]
    entry = SOURCE / "packages/cli/dist/local/agnes.mjs"
    if not entry.is_file():
        raise SystemExit("Build vendor/agnes-harness first; see docs/AGH基座与启动.md")
    node = node_runtime()
    env = {**os.environ, "AGH_HOME": str(ROOT / ".cache/agh-waterguard"), "AGNES_PROFILE": "local-dev",
           "WG_API_URL": f"http://127.0.0.1:{args.api_port}"}
    api = None
    try:
        try:
            with urlopen(env["WG_API_URL"] + "/healthz", timeout=2) as response:
                if json.load(response).get("status") != "ok":
                    raise RuntimeError("Unexpected API health response")
        except OSError:
            python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            api = subprocess.Popen([str(python) if python.is_file() else sys.executable, "-m", "uvicorn", "services.api.main:app",
                                    "--host", "127.0.0.1", "--port", str(args.api_port), "--no-access-log"], cwd=ROOT, env=env)
        print(f"WaterGuard: {env['WG_API_URL']}/; AGH inspection: http://127.0.0.1:{args.port}/", flush=True)
        raise SystemExit(subprocess.call([node, str(entry), "serve", "--profile", "local-dev", "--cwd", str(ROOT),
                                        "--data-dir", str(Path(env["AGH_HOME"]) / "data"), "--port", str(args.port)], cwd=ROOT, env=env))
    except KeyboardInterrupt:
        raise SystemExit(130)
    finally:
        if api is not None:
            api.terminate()
            try:
                api.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait()
