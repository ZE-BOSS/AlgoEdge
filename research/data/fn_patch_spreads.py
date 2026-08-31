"""Patch the spread field on the FundedNext cache.

The first pull read `symbol_info().spread` before the symbol had been selected
into Market Watch, so 11 of 18 symbols cached spread == 0. Bars are unaffected;
only the spread field is wrong.

This is the same failure mode as the retracted B8 claim on Deriv - a lookup that
silently produced zero cost - so it is fixed rather than reported as a finding.
"""
import numpy as np
from pathlib import Path
import MetaTrader5 as mt5
from fn_probe import connect
from fn_fetch_cache import FN_SYMBOLS, CACHE

TFS = ("M5", "M15", "H1", "H4", "D1")

if __name__ == "__main__":
    if not connect():
        raise SystemExit(f"connect failed: {mt5.last_error()}")
    fixed = 0
    print(f"{'symbol':10s}{'was':>8s}{'now':>8s}")
    for s in FN_SYMBOLS:
        mt5.symbol_select(s, True)
        info = mt5.symbol_info(s)
        if info is None:
            print(f"{s:10s}  no info")
            continue
        spread = float(info.spread)
        for tf in TFS:
            f = CACHE / f"{s}__{tf}.npz"
            if not f.exists():
                continue
            z = dict(np.load(f))
            old = float(z.get("spread", 0))
            if old == spread:
                continue
            z["spread"] = np.array(spread)
            np.savez_compressed(f, **z)
            fixed += 1
        print(f"{s:10s}{old:8.0f}{spread:8.0f}")
    print(f"\npatched {fixed} files")
    mt5.shutdown()
