from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from services.agh_host.probe import AghInstallation, AghProbe


class AghProbeTests(unittest.TestCase):
    def test_reports_missing_cli_without_running_model(self):
        installation = AghInstallation(Path("missing-agh"), Path("missing-home"))
        report = AghProbe(installation).collect()
        self.assertEqual(report["code"], "AGH_UNAVAILABLE")

    def test_collects_redacted_mocked_facts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli = root / "packages" / "cli" / "dist" / "local"
            cli.mkdir(parents=True)
            (cli / "agnes.mjs").write_text("", encoding="utf-8")
            def runner(*_args, **_kwargs):
                return subprocess.CompletedProcess([], 0, "ok", "")
            report = AghProbe(AghInstallation(root, root / "home"), runner=runner).collect()
            self.assertEqual(report["status"], "ready")
            self.assertFalse(report["authentication"]["configured"])
