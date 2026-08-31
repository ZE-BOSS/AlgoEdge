"""Stage F feasibility probe: is MT5 reachable, and does it expose tick data
fine enough to detect a Crash-index spike forming?

`connect()` is imported by every other script here, so it resolves .env from the
repo root rather than the current working directory.
"""
import sys
from pathlib import Path
import MetaTrader5 as mt5

REPO = Path(__file__).resolve().parents[2]


def env():
    d = {}
    f = REPO / ".env"
    if not f.exists():
        return d
    for l in f.read_text().splitlines():
        if '=' in l and not l.strip().startswith('#'):
            k, v = l.split('=', 1)
            d[k.strip()] = v.strip()
    return d


def connect():
    """Returns a label for how the connection was made, or None."""
    if mt5.initialize():
        return "default"
    e = env()
    for pfx in ('DERIV_MT5', 'MT5'):
        a = e.get(f'{pfx}_ACCOUNT')
        if not a:
            continue
        try:
            ok = mt5.initialize(login=int(a), password=e.get(f'{pfx}_PASSWORD'),
                                server=e.get(f'{pfx}_SERVER'))
        except Exception:
            ok = False
        if ok:
            return pfx
    return None


if __name__ == "__main__":
    who = connect()
    print("connected via:", who, "| last_error:", mt5.last_error())
    if not who:
        sys.exit(1)
    ti, ai = mt5.terminal_info(), mt5.account_info()
    print(f"terminal connected={getattr(ti,'connected',None)} maxbars={getattr(ti,'maxbars',None)}")
    print(f"account server={getattr(ai,'server',None)} symbols={mt5.symbols_total()}")
    for sym in ("Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
                "Jump 100 Index", "Volatility 75 Index", "XAUUSD"):
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"  {sym:22s} NOT FOUND"); continue
        if not info.visible:
            mt5.symbol_select(sym, True)
        t = mt5.symbol_info_tick(sym)
        m1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 10)
        print(f"  {sym:22s} spread={info.spread:6d} pt={info.point:<12g} "
              f"tick={'ok' if t else 'none'} m1_bars={len(m1) if m1 is not None else 0}")
    mt5.shutdown()
