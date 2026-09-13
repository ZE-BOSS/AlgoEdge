#!/usr/bin/env python
"""
scripts/check_app_research_parity.py

Does the APP (the Backtester's own engine, via scripts/run_app_backtest.py
--dump) take the same trades the research measured? Compares entry bars and
direction trade by trade for the shipped books and prints what matched, what
the app took that research did not, and what research took that the app did not.

    python scripts/run_app_backtest.py --strategy ORB_v1 --symbol GBPJPY \
        --start 2026-01-01 --end 2026-09-12 --tp1-rr 3 --dump app_orb_gbpjpy.json
    python scripts/check_app_research_parity.py --dump app_orb_gbpjpy.json --book orb --symbol GBPJPY
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics import edge_lab as lab  # noqa: E402

BOOKS = {
    "orb": ("orb_break", "native|60", ("htf_trend",), "1:3"),
    "vwap_trend_xau": ("vwap_trend", "native", ("day_dir", "gap_dir", "early"), "1:5"),
    "vwap_trend_nas": ("vwap_trend", "native", ("day_dir", "rel_vol_open", "early"), "1:10"),
    "vwap_pullback_btc": ("vwap_pullback", "native", ("gap_dir", "early"), "1:5"),
}


# classic families: the research builder + simulator on the SAME MT5 H1 bars the app fetched
CLASSIC = {
    "donchian": ("_donchian", "Donchian_v1"),
    "ema_pullback": ("_ema_pullback", "EMAPullback_v1"),
    "vol_breakout": ("_vol_breakout", "VolBreakout_v1"),
}


def classic_research(book: str, symbol: str, lo: int, hi: int) -> dict[int, int]:
    from datetime import datetime, timedelta, timezone

    import MetaTrader5 as mt5

    from backend.analytics import strategy_search as ss
    from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults, get_synth_slot_params
    from backend.strategies.registry import get_strategy
    from backend.core.config_schema import UserConfigV2

    builder, sid = CLASSIC[book]
    assert mt5.initialize(), mt5.last_error()
    info = mt5.symbol_info(symbol)
    start = datetime.fromtimestamp(lo, timezone.utc) - timedelta(days=120)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, start, datetime.fromtimestamp(hi, timezone.utc))
    mt5.shutdown()
    b = ss.Bars.from_rates(symbol, "H1", rates, info.point)
    cfg = UserConfigV2()
    eng = get_strategy(sid)(cfg)
    for k, v in get_synth_slot_params(symbol, sid).items():
        setattr(eng.params, k, v)
    p = eng.research_params(symbol)
    sigs, aux = getattr(ss, builder)(b, p)
    # one position at a time, as the research simulator (and max_positions_per_symbol=1) trade it
    return {t.t_entry: t.direction for t in ss.simulate(b, sigs, aux) if lo <= t.t_entry < hi}


def epoch(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    return int(pd.Timestamp(v).tz_localize("UTC").timestamp()) if pd.Timestamp(v).tzinfo is None \
        else int(pd.Timestamp(v).timestamp())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--book", choices=list(BOOKS) + list(CLASSIC), required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-09-12")
    a = ap.parse_args()

    lo, hi = epoch(a.start), epoch(a.end)
    if a.book in CLASSIC:
        research = classic_research(a.book, a.symbol, lo, hi)
    else:
        fam_name, axis, gates, ex = BOOKS[a.book]
        fam = lab.FAMILY_BY_NAME[fam_name]
        cs = pickle.loads((ROOT / "data" / "edge_lab" / "cands" / f"{a.symbol.replace(' ', '_')}.pkl").read_bytes())[fam_name][axis]
        idx = lab.pick(cs, (lo, hi), tuple(fam.features.index(g) for g in gates), lab.EXITS.index(ex))
        research = {int(cs.t_entry[k]): int(cs.direction[k]) for k in idx}

    trades = json.loads(Path(a.dump).read_text())
    app = {}
    for t in trades:
        if t.get("tp_level") not in (None, 1):
            continue
        app[epoch(t["entry_time"])] = 1 if str(t["direction"]).upper().startswith("B") else -1

    both = sorted(set(research) & set(app))
    same_dir = sum(research[k] == app[k] for k in both)
    only_research = sorted(set(research) - set(app))
    only_app = sorted(set(app) - set(research))
    fmt = lambda ts: pd.Timestamp(ts, unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M")  # noqa: E731
    print(f"{a.book} {a.symbol}: research {len(research)} trades, app {len(app)} trades")
    print(f"  same entry bar: {len(both)} ({same_dir} same direction)")
    print(f"  research only: {len(only_research)}  {[fmt(x) for x in only_research[:12]]}")
    print(f"  app only:      {len(only_app)}  {[fmt(x) for x in only_app[:12]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
