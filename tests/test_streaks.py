"""Winning / losing streaks in the run report (frontend/src/utils/summaryEngine.js)."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_streak_profile():
    r = subprocess.run(["node", "scripts/streaks.check.mjs"], cwd="frontend", capture_output=True, text=True)
    assert "streaks OK" in r.stdout, r.stdout + r.stderr


def test_the_run_report_shows_streaks():
    js = Path("frontend/src/components/RunReport.jsx").read_text()
    assert '<Panel title="Winning and losing streaks"' in js and "<Streaks trades={trades} />" in js
    assert "streaksBy(trades)" in js
