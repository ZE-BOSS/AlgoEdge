#!/usr/bin/env python
"""
scripts/run_final_book.py

The configurations shipped on 2026-09-13, turned into money on the last 8 months
(2026-01-01..2026-09-12, never used to choose anything) and summarised in R on
every window. Reads the saved edge-lab candidates and the classic-family book;
no MT5 connection needed.

    python scripts/run_final_book.py
"""

from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics import edge_lab as lab  # noqa: E402
from backend.analytics.money_sim import AccountRules, shuffled_paths, simulate_account  # noqa: E402
from scripts.run_edge_lab import contracts, load_cands  # noqa: E402

OUT = ROOT / "data" / "edge_lab"
MAIN = ["US Tech 100", "XAUUSD", "BTCUSD", "GBPJPY"]
OTHERS = ["EURJPY", "USDJPY", "GBPUSD", "EURUSD", "AUDUSD", "USDCAD", "XAGUSD", "ETHUSD",
          "US SP 500", "Wall Street 30", "Germany 40", "UK 100", "Japan 225"]

# name -> (family, axis, gates, exit, markets)
BOOKS = {
    "ORB_v1 M5 trend (shared)": ("orb_break", "native|60", ("htf_trend",), "1:3", MAIN),
    "VWAP_v1 SESSION_TREND XAUUSD": ("vwap_trend", "native", ("day_dir", "gap_dir", "early"), "1:5", ["XAUUSD"]),
    "VWAP_v1 SESSION_TREND US Tech 100": ("vwap_trend", "native", ("day_dir", "rel_vol_open", "early"), "1:10", ["US Tech 100"]),
    "VWAP_v1 SESSION_PULLBACK BTCUSD": ("vwap_pullback", "native", ("gap_dir", "early"), "1:5", ["BTCUSD"]),
}
GRID = list(product([350.0, 10000.0], [1.0, 1.8], [False, True]))


def money(legs, cons):
    return [{"capital": cap, "risk_pct": risk, "compounding": comp,
             **simulate_account(legs, cons, AccountRules(cap, risk, compounding=comp, daily_dd_cap_pct=10.0))}
            for cap, risk, comp in GRID]


def line(m):
    return (f"${m['net_pnl']:>9,.0f} {m['return_pct']:>6.1f}%  DD {m['max_dd_pct']:>5.1f}%  PF {m['profit_factor'] or 0:>4.2f}  "
            f"exp {m['expectancy_r'] or 0:+.3f}R/${m['expectancy_usd'] or 0:>6.2f}  Sharpe {m['sharpe'] or 0:>5.2f}  "
            f"Sortino {m['sortino'] or 0:>5.2f}  n {m['trades']:>3}  +mo {m['positive_months']}/{m['months']}  "
            f"unsizable {m['skipped_unsizable']}")


def main() -> int:
    blobs = load_cands(MAIN + OTHERS)
    cons = contracts()
    oos = lab.WINDOWS["last_8m_2026"]
    report: dict = {"windows": lab.WINDOWS, "books": {}}
    portfolio = []
    for name, (fam_name, axis, gates, ex, markets) in BOOKS.items():
        fam = lab.FAMILY_BY_NAME[fam_name]
        use = tuple(fam.features.index(g) for g in gates)
        e = lab.EXITS.index(ex)
        data = {m: blobs[m][fam_name] for m in markets}
        cfg = lab.evaluate(fam, data, axis, use, e, lab.WINDOWS)
        book = {"family": fam_name, "axis": axis, "gates": gates, "exit": ex,
                "r_by_window": cfg.per_market, "pooled": cfg.pooled, "money": {}}
        print(f"\n=== {name}: {fam_name} {axis} gates={gates} exit={ex}")
        legs_all = []
        for m in markets:
            legs = lab.legs_for(data[m][axis], oos, use, e)
            legs_all += legs
            rows = money(legs, cons)
            book["money"][m] = rows
            print(f"  {m}")
            for r in rows:
                print(f"    ${r['capital']:>6.0f} {r['risk_pct']}% {'compound' if r['compounding'] else 'fixed   '} | {line(r)}")
        if len(markets) > 1:
            rows = money(legs_all, cons)
            book["money"]["ALL"] = rows
            print("  all markets together")
            for r in rows:
                print(f"    ${r['capital']:>6.0f} {r['risk_pct']}% {'compound' if r['compounding'] else 'fixed   '} | {line(r)}")
        portfolio += legs_all
        if fam_name == "orb_break":
            others = {m: blobs[m][fam_name] for m in OTHERS if m in blobs}
            book["others"] = lab.asdict_cfg(lab.evaluate(fam, others, axis, use, e, lab.WINDOWS))
        report["books"][name] = book

    rows = money(portfolio, cons)
    report["portfolio"] = rows
    report["portfolio_shuffled"] = shuffled_paths(portfolio, cons, AccountRules(10000.0, 1.0, daily_dd_cap_pct=10.0), n=300, seed=11)
    print("\n=== PORTFOLIO: ORB M5 trend on 4 markets + the three VWAP session books")
    for r in rows:
        print(f"    ${r['capital']:>6.0f} {r['risk_pct']}% {'compound' if r['compounding'] else 'fixed   '} | {line(r)}")
    print(f"  reshuffled ($10k, 1%): {report['portfolio_shuffled']}")
    for r in rows:
        if r["capital"] == 10000.0 and r["risk_pct"] == 1.0 and not r["compounding"]:
            print(f"  monthly %: {r['monthly_pct']}")

    classic = json.loads((OUT / "classic_book.json").read_text())
    keep = {"donchian": ["XAUUSD", "BTCUSD", "US Tech 100"], "ema_pullback": ["XAUUSD", "BTCUSD"],
            "vol_breakout": ["XAUUSD", "BTCUSD"], "orb": ["US Tech 100", "XAUUSD", "GBPJPY"]}
    report["classic"] = {f: {m: classic["families"][f]["per_market"][m] for m in ms} for f, ms in keep.items()}
    path = OUT / "final_book.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\nsaved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
