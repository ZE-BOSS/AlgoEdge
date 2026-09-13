#!/usr/bin/env python
"""
scripts/run_classic_book.py

The eight strategy families from the 2026-09-11 report (strategy_search.FAMILIES:
Donchian, EMA pullback, session VWAP pullback, tick-volume breakout, RSI(2),
Bollinger fade, ORB, daily TSMOM), re-run on the main markets with the SAME
walk-forward windows as the edge lab, and turned into money.

  * bars are resampled from the cached M5 history (data/edge_lab/bars), so no
    MT5 connection is needed and every family sees identical prices;
  * settings are chosen on 2024-01-22..2025-12-31 only, two ways:
      per-market  — the best in-sample setting on that market (the old report's rule)
      shared      — ONE setting for all markets, positive in-sample on all but one
  * reported unchanged on 2026-01-01..2026-09-12 and on the 2022-2023 holdout.

    python scripts/run_classic_book.py
    python scripts/run_classic_book.py --markets "US Tech 100" XAUUSD BTCUSD GBPJPY --families orb donchian
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics import edge_lab as lab  # noqa: E402
from backend.analytics.money_sim import AccountRules, Leg, simulate_account  # noqa: E402
from backend.analytics.strategy_search import FAMILIES, Bars, simulate, summarize  # noqa: E402
from scripts.run_edge_lab import MAIN, contracts, load_bars, print_money  # noqa: E402

OUT = ROOT / "data" / "edge_lab"
RULE = {"M15": "15min", "H1": "1h", "D1": "1D"}


def resample(b5: Bars, tf: str) -> Bars:
    df = pd.DataFrame({"open": b5.open, "high": b5.high, "low": b5.low, "close": b5.close,
                       "spread": b5.spread, "vol": b5.volume},
                      index=pd.to_datetime(b5.time, unit="s"))
    g = df.resample(RULE[tf], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "spread": "first", "vol": "sum"}).dropna()
    t = (g.index.asi8 // 10**9).astype(np.int64)
    return Bars(b5.symbol, tf, t, g["open"].to_numpy(), g["high"].to_numpy(), g["low"].to_numpy(),
                g["close"].to_numpy(), g["spread"].to_numpy(), g["vol"].to_numpy())


def split(trades, span):
    lo, hi = span
    return [t for t in trades if lo <= t.t_entry < hi and t.t_exit < hi]


def score(tr) -> float:
    if not tr:
        return -math.inf
    s = summarize(tr)
    return s["avg_r"] * math.sqrt(s["n"])


def cell(s: dict) -> str:
    if not s or not s.get("n"):
        return f"{'-':>4} {'':>8} {'':>5}"
    return f"{s['n']:>4} {s['avg_r']:+.3f}R {s['profit_factor'] or 0:>5.2f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="+", default=MAIN)
    ap.add_argument("--families", nargs="*", default=None)
    a = ap.parse_args()
    cons = contracts()
    oos = lab.WINDOWS["last_8m_2026"]
    report: dict = {"windows": lab.WINDOWS, "families": {}}

    bars = {sym: load_bars(sym) for sym in a.markets}
    for fam in FAMILIES:
        if a.families and fam.name not in a.families:
            continue
        runs: dict[str, list] = {}          # sym -> [(params, trades)]
        for sym, b5 in bars.items():
            b = b5 if fam.timeframe == "M5" else resample(b5, fam.timeframe)
            runs[sym] = []
            for p in fam.grid:
                sigs, aux = fam.build(b, p)
                runs[sym].append((p, simulate(b, sigs, aux)))
        res = {"description": fam.description, "timeframe": fam.timeframe, "per_market": {}, "shared": None}
        print(f"\n=== {fam.name} ({fam.timeframe}) — {fam.description} | {len(fam.grid)} settings")
        print(f"  {'market':<12} {'rule':<7} {'setting':<48} " + " | ".join(f"{w:^19}" for w in lab.WINDOWS))

        # per-market pick
        for sym, rows in runs.items():
            elig = [(p, tr) for p, tr in rows
                    if len(split(tr, lab.WINDOWS[lab.SELECT])) >= fam.min_is_trades
                    and summarize(split(tr, lab.WINDOWS[lab.SELECT]))["avg_r"] > 0]
            if not elig:
                print(f"  {sym:<12} {'market':<7} no setting profitable in selection window")
                res["per_market"][sym] = None
                continue
            p, tr = max(elig, key=lambda x: score(split(x[1], lab.WINDOWS[lab.SELECT])))
            win = {w: summarize(split(tr, span)) for w, span in lab.WINDOWS.items()}
            print(f"  {sym:<12} {'market':<7} {json.dumps(p):<48} " + " | ".join(cell(win[w]) for w in lab.WINDOWS))
            legs = [Leg(sym, t.t_entry, t.t_exit, t.r, t.stop_dist, group=f"{sym}|{t.t_entry}") for t in split(tr, oos)]
            res["per_market"][sym] = {"params": p, "windows": win, "legs": len(legs)}
            res["per_market"][sym]["money"] = [
                {"capital": cap, "risk_pct": risk, "compounding": comp,
                 **simulate_account(legs, cons, AccountRules(cap, risk, compounding=comp, daily_dd_cap_pct=10.0))}
                for cap, risk, comp in product([350.0, 10000.0], [1.0, 1.8], [False, True])]

        # shared pick: one setting across every market
        best = None
        for gi, p in enumerate(fam.grid):
            parts, pos, ok = [], 0, True
            for sym in runs:
                tr = split(runs[sym][gi][1], lab.WINDOWS[lab.SELECT])
                if len(tr) < 15:
                    ok = False
                    break
                rs = [t.r for t in tr]
                pos += np.mean(rs) > 0
                parts += rs
            if not ok or pos < len(runs) - 1 or np.mean(parts) <= 0:
                continue
            t_stat = np.mean(parts) / (np.std(parts, ddof=1) / math.sqrt(len(parts)))
            if best is None or t_stat > best[0]:
                best = (t_stat, gi, p)
        if best is None:
            print(f"  {'ALL':<12} {'shared':<7} no single setting profitable on {len(runs) - 1}+ markets in selection")
        else:
            _, gi, p = best
            shared = {"params": p, "per_market": {}}
            book = []
            for sym in runs:
                tr = runs[sym][gi][1]
                win = {w: summarize(split(tr, span)) for w, span in lab.WINDOWS.items()}
                shared["per_market"][sym] = win
                print(f"  {sym:<12} {'shared':<7} {json.dumps(p):<48} " + " | ".join(cell(win[w]) for w in lab.WINDOWS))
                book += [Leg(sym, t.t_entry, t.t_exit, t.r, t.stop_dist, group=f"{sym}|{t.t_entry}") for t in split(tr, oos)]
            rows = [{"capital": cap, "risk_pct": risk, "compounding": comp,
                     **simulate_account(book, cons, AccountRules(cap, risk, compounding=comp, daily_dd_cap_pct=10.0))}
                    for cap, risk, comp in product([350.0, 10000.0], [1.0, 1.8], [False, True])]
            shared["money_all_markets"] = rows
            print(f"  $ shared setting, all markets together, last 8 months ({len(book)} trades)")
            print_money(rows)
            res["shared"] = shared
        report["families"][fam.name] = res

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "classic_book.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\nsaved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
