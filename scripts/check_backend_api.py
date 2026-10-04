"""Exercise the running backend over HTTP, including actual hydraulic workers.

Run with .venv/Scripts/python.exe scripts/check_backend_api.py after npm start.
The report and downloaded evidence remain under artifacts/api-acceptance/.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))
from waterguard_contracts.core import task_hash, validate


class Acceptance:
    def __init__(self, base_url: str, output: Path):
        self.client = httpx.Client(base_url=base_url, timeout=60, trust_env=False)
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.report = {"base_url": base_url, "requests": [], "runs": [], "checks": [], "status": "RUNNING"}

    def request(self, method, path, expected=200, **kwargs):
        response = self.client.request(method, path, **kwargs)
        self.report["requests"].append({"method": method, "path": path, "status": response.status_code})
        assert response.status_code == expected, f"{method} {path}: {response.status_code}: {response.text[:1000]}"
        return response

    def check(self, label, condition):
        assert condition, label
        self.report["checks"].append(label)

    def task(self, **proposal):
        task = self.request("POST", "/api/tasks/from-proposal", 201, json=proposal).json()
        validate("TaskSpec", task)
        self.check("Frozen task hash", task_hash(task) == task["constraint_hash"])
        return task

    def start(self, task, **options):
        return self.request("POST", f"/api/tasks/{task['task_id']}/runs", 202, json={"mode": "benchmark", **options}).json()

    def finish(self, run, expected="COMPLETED"):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = self.request("GET", f"/api/runs/{run['run_id']}").json()
            if current["status"] in {"COMPLETED", "ERROR", "CANCELLED", "BUDGET_EXHAUSTED"}:
                self.check(f"Terminal status {expected}", current["status"] == expected)
                # Terminal metadata can precede the final worker's atomic packaging.
                while time.monotonic() < deadline:
                    evidence = self.client.get(f"/api/runs/{run['run_id']}/evidence")
                    if evidence.status_code == 200:
                        self.report["runs"].append(current)
                        print(current["run_id"], current["mode"], current["status"], flush=True)
                        return current
                    assert evidence.status_code == 404, evidence.text
                    time.sleep(.1)
            time.sleep(.25)
        raise AssertionError("Run did not finish and publish evidence within 180 seconds")

    def evidence(self, run):
        rid = run["run_id"]
        manifest = self.request("GET", f"/api/runs/{rid}/evidence").json()
        validate("EvidenceManifest", manifest)
        self.check("Benchmark/fixture/replay cannot export Live attestation", not manifest["live_export_allowed"])
        response = self.request("GET", f"/api/runs/{rid}/evidence/download")
        (self.output / f"{rid}.zip").write_bytes(response.content)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            for entry in manifest["artifacts"]:
                name = entry["ref"].removeprefix("artifact:")
                digest = "sha256:" + hashlib.sha256(archive.read(name)).hexdigest()
                assert digest == entry["sha256"] == manifest["hashes"][entry["ref"]], name
            self.check("Downloaded ZIP exactly matches the manifest", json.loads(archive.read("evidence-manifest.json")) == manifest)
            events = [json.loads(line) for line in archive.read("events.jsonl").decode().splitlines()]
            self.check("Events are contiguous and bound to their run", [e["sequence"] for e in events] == list(range(1, len(events)+1)) and all(e["run_id"] == rid for e in events))
            for name in archive.namelist():
                if name.endswith("execution.json"):
                    execution = json.loads(archive.read(name))
                    self.check("Spawned worker exited and was reaped", execution["worker_pid"] is not None and execution["worker_exit_code"] is not None)
            if run["status"] == "CANCELLED":
                self.check("Cancellation exercised a real worker", any(n.endswith("execution.json") for n in archive.namelist()))
        stream = self.request("GET", f"/api/runs/{rid}/events")
        streamed = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
        self.check("SSE matches persisted events", streamed == events)
        resumed = self.request("GET", f"/api/runs/{rid}/events", headers={"Last-Event-ID": "2"})
        self.check("SSE resumes after Last-Event-ID", [json.loads(l[6:]) for l in resumed.text.splitlines() if l.startswith("data: ")] == events[2:])
        self.request("GET", f"/api/runs/{rid}/events", 422, headers={"Last-Event-ID": "invalid"})
        self.request("POST", f"/api/runs/{rid}/answers", 409, json={"answer": "yes"})

    def results(self, run):
        rid = run["run_id"]
        plans = self.request("GET", f"/api/runs/{rid}/candidates").json()
        results = self.request("GET", f"/api/runs/{rid}/results").json()
        self.check("Candidate/result counts agree", len(plans) == len(results) == run["budget_usage"]["simulations"])
        for result in results:
            artifact, report = result["artifact"], result["verification"]
            validate("SimulationArtifact", artifact)
            validate("VerificationReport", report)
            self.check("Actual simulation succeeded and independently verified", artifact["status"] == "SUCCEEDED" and report["admission"]["simulation_valid"] == "pass")
            sid = artifact["simulation_id"]
            series = self.request("GET", f"/api/simulations/{sid}/series").json()
            node = series["frames"][0]["nodes"][0]["node_id"]
            frame = series["frames"][0]
            filtered = self.request("GET", f"/api/simulations/{sid}/series", params={"node_id": node, "start_s": frame["time_s"], "end_s": frame["time_s"]+frame["duration_s"]}).json()
            self.check("Series time/node filters", len(filtered["frames"]) == 1 and [n["node_id"] for n in filtered["frames"][0]["nodes"]] == [node])
            self.request("GET", f"/api/simulations/{sid}/series", 404, params={"node_id": "missing-node"})
            self.request("GET", f"/api/simulations/{sid}/series", 422, params={"start_s": 100, "end_s": 0})
            if run["mode"] == "replay":
                comparison = result["replay_comparison"]
                self.check("Replay recomputes equivalent frozen inputs", comparison["recomputed"] and comparison["input_hash_identical"] and comparison["numerically_equivalent"])
        return results

    def execute(self):
        self.request("GET", "/healthz")
        self.request("GET", "/openapi.json")
        self.request("GET", "/")
        networks = self.request("GET", "/api/networks").json()
        self.check("Both public networks available", {n["network_id"] for n in networks} == {"epa-net1", "epa-net3"})
        for network in networks:
            self.request("GET", f"/api/networks/{network['network_id']}")
        self.request("GET", "/api/networks/unknown", 404)
        self.request("GET", "/api/tasks/unknown", 404)
        self.request("GET", "/api/runs/run-unknown", 404)
        self.request("GET", "/api/simulations/sim-unknown/series", 404)
        self.request("POST", "/api/task-proposals", json={"candidate_limit": 1})
        self.request("POST", "/api/tasks/from-proposal", 422, json={"link_id": "9"})
        self.request("POST", "/api/tasks/from-proposal", 422, json={"candidate_limit": 0})
        source = None
        for network in ("epa-net1", "epa-net3"):
            for fault in ("normal", "closure", "leak", "rupture", "isolation"):
                task = self.task(network_id=network, fault_kind=fault, candidate_limit=3 if fault == "closure" else 1)
                self.request("GET", f"/api/tasks/{task['task_id']}")
                key = uuid.uuid4().hex
                run = self.finish(self.start(task, idempotency_key=key))
                self.results(run)
                self.evidence(run)
                retry = self.start(task, idempotency_key=key)
                self.check("Idempotent retry returns current same run", retry["run_id"] == run["run_id"] and retry["status"] == "COMPLETED")
                self.request("POST", f"/api/runs/{run['run_id']}/cancel", 409)
                self.request("GET", f"/api/runs/{run['run_id']}/error", 404)
                self.request("POST", f"/api/tasks/{task['task_id']}/runs", 409,
                             json={"mode": "replay", "source_run_id": run["run_id"], "idempotency_key": key})
                if source is None:
                    source = task, run
        task, original = source
        replay = self.finish(self.start(task, mode="replay", source_run_id=original["run_id"]))
        self.results(replay)
        self.evidence(replay)
        self.request("POST", f"/api/tasks/{task['task_id']}/runs", 422, json={"mode": "replay"})
        self.request("POST", f"/api/tasks/{task['task_id']}/runs", 422, json={"mode": "fixture"})
        live = self.request("POST", f"/api/tasks/{task['task_id']}/runs", 503, json={"mode": "live"})
        self.check("Unconfigured Live rejected explicitly", live.json()["detail"]["code"] in {"AGH_UNAVAILABLE", "MODEL_NOT_CONFIGURED"})
        fixture_task = self.request("GET", "/api/fixtures").json()["task"]
        self.request("POST", "/api/tasks", 201, json=fixture_task)
        fixture = self.finish(self.start(fixture_task, mode="fixture", idempotency_key=uuid.uuid4().hex))
        self.results(fixture)
        self.evidence(fixture)
        self.request("POST", f"/api/tasks/{fixture_task['task_id']}/runs", 409, json={"mode": "replay", "source_run_id": fixture["run_id"]})
        revised = copy.deepcopy(task)
        revised["revision"] += 1
        revised["constraint_hash"] = task_hash(revised)
        self.request("POST", f"/api/tasks/{task['task_id']}/revisions", 201, json=revised)
        self.check("Old revision remains immutable", self.request("GET", f"/api/tasks/{task['task_id']}", params={"revision": 1}).json() == task)
        self.check("Latest revision selected", self.request("GET", f"/api/tasks/{task['task_id']}").json() == revised)
        bad = copy.deepcopy(revised); bad["revision"] += 2; bad["constraint_hash"] = task_hash(bad)
        self.request("POST", f"/api/tasks/{task['task_id']}/revisions", 409, json=bad)
        bad = copy.deepcopy(revised); bad["hard_constraints"]["min_pressure_m"] = 33
        self.request("POST", "/api/tasks", 422, json=bad)
        bad["constraint_hash"] = task_hash(bad)
        self.request("POST", "/api/tasks", 409, json=bad)
        bad["task_id"] = "../escape"; bad["constraint_hash"] = task_hash(bad)
        self.request("POST", "/api/tasks", 422, json=bad)
        cancel_task = self.task(candidate_limit=6, network_id="epa-net3")
        key = uuid.uuid4().hex
        path = f"/api/tasks/{cancel_task['task_id']}/runs"
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.client.post(path, json={"mode": "benchmark", "idempotency_key": key}), range(4)))
        self.check("Concurrent duplicate requests create one run", all(r.status_code == 202 for r in replies) and len({r.json()["run_id"] for r in replies}) == 1)
        pending = replies[0].json()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = self.request("GET", f"/api/runs/{pending['run_id']}").json()
            if current["phase"] == "SIMULATING":
                time.sleep(.4)
                break
            assert current["status"] not in {"COMPLETED", "ERROR"}, current
            time.sleep(.05)
        self.request("POST", f"/api/runs/{pending['run_id']}/cancel")
        cancelled = self.finish(pending, "CANCELLED")
        self.request("POST", f"/api/runs/{pending['run_id']}/cancel")
        self.evidence(cancelled)
        timeout_task = self.task(candidate_limit=1)
        timeout_task["task_id"] = "timeout-" + uuid.uuid4().hex
        timeout_task["budget"]["max_wall_time_s"] = 1
        timeout_task["constraint_hash"] = task_hash(timeout_task)
        self.request("POST", "/api/tasks", 201, json=timeout_task)
        exhausted = self.finish(self.start(timeout_task), "BUDGET_EXHAUSTED")
        self.request("GET", f"/api/runs/{exhausted['run_id']}/error")
        self.evidence(exhausted)
        invalid_task = self.task(candidate_limit=1)
        invalid_task["task_id"] = "invalid-" + uuid.uuid4().hex
        invalid_task["network_ref"]["sha256"] = "sha256:" + "0"*64
        invalid_task["constraint_hash"] = task_hash(invalid_task)
        self.request("POST", "/api/tasks", 201, json=invalid_task)
        failed = self.finish(self.start(invalid_task), "ERROR")
        self.request("GET", f"/api/runs/{failed['run_id']}/error")
        self.evidence(failed)
        self.request("GET", "/api/runs")
        self.report["status"] = "PASSED"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "api-acceptance")
    args = parser.parse_args()
    acceptance = Acceptance(args.base_url, args.output)
    try:
        acceptance.execute()
    except Exception as error:
        acceptance.report.update(status="FAILED", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        acceptance.client.close()
        (args.output / "report.json").write_text(json.dumps(acceptance.report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(acceptance.report["status"], len(acceptance.report["runs"]), "runs;", len(acceptance.report["requests"]), "HTTP requests", flush=True)
