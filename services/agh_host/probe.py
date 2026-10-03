"""Collect AGH readiness facts without reading or logging secrets."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class AghInstallation:
    root: Path
    home: Path
    profile: str = "local-dev"
    node: str = "node"

    @property
    def cli(self) -> Path:
        return self.root / "packages" / "cli" / "dist" / "local" / "agnes.mjs"


class AghProbe:
    """Read-only probe; it never starts a model session or exposes key material."""

    def __init__(self, installation: AghInstallation, runner: Callable | None = None):
        self.installation = installation
        self.runner = runner or subprocess.run

    def _run(self, *arguments: str) -> dict:
        env = {**os.environ, "AGH_HOME": str(self.installation.home), "AGNES_PROFILE": self.installation.profile}
        process = self.runner([self.installation.node, str(self.installation.cli), *arguments], cwd=self.installation.root,
                              env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False)
        return {"argv": list(arguments), "returncode": process.returncode,
                "stdout": (process.stdout or "").strip(), "stderr": (process.stderr or "").strip()}

    def collect(self, mcp_server_id: str = "waterguard-mcp") -> dict:
        """Capture version, daemon and mounted-tool facts for a run evidence bundle."""
        if not self.installation.cli.is_file():
            return {"observed_at": utc_now(), "status": "unavailable", "code": "AGH_UNAVAILABLE",
                    "reason": "Local AGH CLI build is absent", "installation": str(self.installation.root)}
        version = self._run("--version")
        daemon = self._run("daemon", "status")
        mcp_status = self._run("mcp", "status", mcp_server_id)
        mcp_tools = self._run("mcp", "tools", mcp_server_id)
        auth_configured = bool(os.environ.get("AGH_API_KEY") or os.environ.get("AGNES_API_KEY"))
        return {"observed_at": utc_now(), "status": "ready" if daemon["returncode"] == 0 and mcp_status["returncode"] == 0 else "degraded",
                "installation": str(self.installation.root), "profile": self.installation.profile,
                "version": version, "daemon": daemon, "mcp": {"server_id": mcp_server_id, "status": mcp_status, "tools": mcp_tools},
                "authentication": {"configured": auth_configured, "reason": None if auth_configured else "No local model credential configured; no Agnes session was started."}}

    def write_snapshot(self, run_directory: Path, mcp_server_id: str = "waterguard-mcp") -> Path:
        """Store a redacted probe under the run's AGH association directory."""
        target = Path(run_directory) / "agh" / "probe.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.collect(mcp_server_id), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target
