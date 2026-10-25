"""AlgoEdge backend package.

ALGOEDGE_MT5_BRIDGE=host:port: on Linux the MetaTrader5 package does not
exist, so the stand-in from backend/mt5/bridge_client.py is installed as
`MetaTrader5` here - before any backend module imports it - and every MT5 call
goes to the Wine bridge (deploy/linux/). Unset (Windows), nothing happens.
"""

import os as _os
import sys as _sys


def _bridge_setting_from_dotenv() -> None:
    """`.env` is loaded later than this package is imported, and the bridge has
    to be installed before anything imports MetaTrader5 - so these two keys are
    read here. Without it a bridge configured only in .env would silently never
    install and the backend would fall back to MOCK mode. Each key is filled
    independently: the bridge address may come from the environment (systemd)
    while the key file is named only in .env."""
    keys = ("ALGOEDGE_MT5_BRIDGE", "ALGOEDGE_MT5_BRIDGE_KEY_FILE")
    if all(_os.environ.get(k) for k in keys):
        return
    from pathlib import Path as _Path
    for env in (_Path.cwd() / ".env", _Path(__file__).resolve().parent.parent / ".env"):
        try:
            lines = env.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            k, sep, v = line.strip().partition("=")
            k = k.strip()
            if sep and k in keys and not _os.environ.get(k):
                _os.environ[k] = v.strip().strip('"').strip("'")


_bridge_setting_from_dotenv()

if _os.environ.get("ALGOEDGE_MT5_BRIDGE") and not hasattr(_sys.modules.get("MetaTrader5"), "bridge_ping"):
    from backend.mt5.bridge_client import install as _install_mt5_bridge
    _install_mt5_bridge()
