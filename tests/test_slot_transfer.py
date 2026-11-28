"""Moving slots between the Backtester, Settings and servers (demo -> live): the rules
that keep a copy from ever starting trading on its own (frontend/src/utils/slotTransfer.js)."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_slot_transfer_rules():
    r = subprocess.run(["node", "scripts/slotTransfer.check.mjs"], cwd="frontend", capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "slotTransfer OK" in r.stdout


def test_settings_and_backtester_use_the_one_rule_set():
    strat = Path("frontend/src/pages/Settings/Strategy.jsx").read_text()
    assert "<SlotTransfer" in strat and "slots={savedSlots}" in strat
    xfer = Path("frontend/src/components/SlotTransfer.jsx").read_text()
    assert "mergeSlots(remote.instrument_slots || [], slots)" in xfer
    assert "updateConfigOn(tgt.id, { config: slotsPayload(next) })" in xfer
    assert "isSignedIn(tgt.id)" in xfer and "window.confirm(" in xfer
    bt = Path("frontend/src/pages/Backtester.jsx").read_text()
    assert "mergeSlots(userCfg?.config?.instrument_slots || []" in bt
    util = Path("frontend/src/utils/slotTransfer.js").read_text()
    assert "enabled: false, ...slotSettings(src)" in util
