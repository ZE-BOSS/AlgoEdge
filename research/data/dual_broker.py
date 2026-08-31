"""Connect to BOTH MT5 terminals and prove the feeds are genuinely different.

The MetaTrader5 python package holds one terminal connection per process, so the
two brokers are visited sequentially: initialize(path=...) -> pull -> shutdown().
Passing `path` is what pins each call to the right terminal; without it the
package attaches to whichever terminal it finds first, which is exactly how you
end up silently comparing a broker against itself.

Verification built in: the same symbol is pulled from both and compared bar for
bar. Identical closes would mean the path pinning failed.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import MetaTrader5 as mt5

CACHE = Path(__file__).parent

BROKERS = {
    "deriv": dict(
        path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
        login_env="DERIV_MT5_ACCOUNT", pw_env="DERIV_MT5_PASSWORD",
        srv_env="DERIV_MT5_SERVER", cache="cache_live_deriv"),
    "fundednext": dict(
        path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
        login_env="MT5_ACCOUNT", pw_env="MT5_PASSWORD",
        srv_env="MT5_SERVER", cache="cache_live_fn"),
}


def env_file(p=".env"):
    d = {}
    root = Path(__file__).resolve().parents[2]
    for line in (root / p).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def connect(name: str, cfg: dict, env: dict) -> bool:
    """Attach to one specific terminal by executable path."""
    mt5.shutdown()
    login = int(env[cfg["login_env"]])
    pw = env[cfg["pw_env"]]
    srv = env[cfg["srv_env"]]
    ok = mt5.initialize(path=cfg["path"], login=login, password=pw, server=srv,
                        timeout=60000)
    if not ok:
        # terminal may already be running and logged in
        ok = mt5.initialize(path=cfg["path"], timeout=60000)
        if ok:
            ok = mt5.login(login, password=pw, server=srv)
    return ok


def whoami() -> dict:
    ai, ti = mt5.account_info(), mt5.terminal_info()
    return {
        "login": getattr(ai, "login", None),
        "server": getattr(ai, "server", None),
        "company": getattr(ai, "company", None),
        "balance": getattr(ai, "balance", None),
        "path": getattr(ti, "path", None),
        "symbols": mt5.symbols_total(),
    }


def spread_now(sym: str):
    if not mt5.symbol_select(sym, True):
        return None
    info = mt5.symbol_info(sym)
    t = mt5.symbol_info_tick(sym)
    if info is None or t is None:
        return None
    return {
        "spread_pts": info.spread,
        "point": info.point,
        "spread_px": (t.ask - t.bid),
        "bid": t.bid, "ask": t.ask,
        "tick_time": datetime.fromtimestamp(t.time, tz=timezone.utc)
        if t.time else None,
    }


if __name__ == "__main__":
    env = env_file()
    probe = "XAUUSD"
    seen = {}
    for name, cfg in BROKERS.items():
        if not connect(name, cfg, env):
            print(f"{name}: CONNECT FAILED {mt5.last_error()}")
            continue
        who = whoami()
        sp = spread_now(probe)
        r = mt5.copy_rates_from_pos(probe, mt5.TIMEFRAME_M15, 0, 200)
        seen[name] = dict(who=who, spread=sp,
                          closes=r["close"].astype(float) if r is not None else None,
                          times=r["time"].astype(np.int64) if r is not None else None)
        print(f"\n=== {name}")
        print(f"  terminal : {who['path']}")
        print(f"  account  : {who['login']} @ {who['server']} ({who['company']})")
        print(f"  balance  : {who['balance']:,.2f} | symbols {who['symbols']}")
        if sp:
            print(f"  {probe} bid/ask {sp['bid']} / {sp['ask']} "
                  f"| spread {sp['spread_pts']} pts = {sp['spread_px']:.5f} px")
            print(f"  last tick: {sp['tick_time']}")
        mt5.shutdown()

    if len(seen) == 2:
        a, b = seen["deriv"], seen["fundednext"]
        print("\n=== ARE THE FEEDS DIFFERENT? ===")
        if a["closes"] is None or b["closes"] is None:
            print("  could not compare — missing bars")
        else:
            ta, tb = a["times"], b["times"]
            common = np.intersect1d(ta, tb)
            ia = {int(t): i for i, t in enumerate(ta)}
            ib = {int(t): i for i, t in enumerate(tb)}
            diffs = np.array([abs(a["closes"][ia[t]] - b["closes"][ib[t]])
                              for t in common])
            print(f"  overlapping {probe} M15 bars : {len(common)}")
            print(f"  identical closes            : {int((diffs == 0).sum())}")
            print(f"  mean |close difference|     : {diffs.mean():.5f}")
            print(f"  max  |close difference|     : {diffs.max():.5f}")
            verdict = ("SAME FEED — path pinning FAILED"
                       if len(common) and (diffs == 0).all()
                       else "GENUINELY DIFFERENT FEEDS")
            print(f"  => {verdict}")
