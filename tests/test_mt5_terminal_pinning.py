"""
Two backends on one machine (demo + live, each with its own MT5 terminal) must
never cross over. A bare `mt5.initialize()` attaches to whichever terminal the
package finds first, so every reconnect has to reuse this process's terminal.
"""

import re
from pathlib import Path

from backend.mt5 import executor


class _FakeMT5:
    def __init__(self):
        self.paths = []

    def initialize(self, path=None):
        self.paths.append(path)
        return True


def test_reconnect_reuses_the_terminal_this_process_connected_with(monkeypatch):
    monkeypatch.setattr(executor, "_terminal_path", "")
    monkeypatch.setenv("MT5_PATH", r"C:\MT5-demo\terminal64.exe")
    f = _FakeMT5()
    assert executor.reinitialize(f) and f.paths[-1] == r"C:\MT5-demo\terminal64.exe"
    executor.remember_terminal(r"C:\MT5-live\terminal64.exe")
    executor.reinitialize(f)
    assert f.paths[-1] == r"C:\MT5-live\terminal64.exe", "the path actually used wins over the env"


def test_no_bare_initialize_left_in_the_live_paths():
    """Every reconnect in the code a running backend executes goes through
    reinitialize(); only offline research scripts may still call it bare."""
    offenders = []
    for p in Path("backend").rglob("*.py"):
        if "kronos_service" in p.parts or p.name == "executor.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bmt5\.initialize\(\s*\)", line) and not line.lstrip().startswith("#"):
                offenders.append(f"{p}:{i}: {line.strip()}")
    assert not offenders, "bare mt5.initialize():\n" + "\n".join(offenders)
