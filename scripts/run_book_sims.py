#!/usr/bin/env python
"""
scripts/run_book_sims.py

Dollars for a PRE-REGISTERED book, not for the winners of a 144-per-market search.

Why: scripts/run_orb_research.py searches 2 sessions x 3 ranges x 2 entries x 12
exits per market. Its in-sample best is mostly selection noise — on GBPJPY it
picked a 15-minute retest that lost on the 2022-23 holdout, and a portfolio of
those picks loses out of sample. So this script takes a FIXED list of
configurations, each named here in advance with the reason it is on the list, and
reports every capital / risk / compounding / pyramiding / daily-cap combination.

Reads data/orb_research/setups*.pkl; writes data/orb_research/book_sims.json.
"""

from __future__ import annotations

import json
import pickle
import statistics
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.money_sim import AccountRules, Contract, Leg, shuffled_paths, simulate_account  # noqa: E402
from backend.analytics.orb_research import in_window, rs  # noqa: E402

DATA = ROOT / "data" / "orb_research"

# symbol, (session, range, entry mode), exit variant, why it is here
SHIPPED = [("GBPJPY", ("london", 60, "break"), "1:3",
            "ORB_v1's shipped configuration: chosen in a 12-point grid, positive in 2022-23, "
            "2024-25 and the last 8 months, and confirmed on tick replay (+0.18R vs +0.21R)")]
CANDIDATES = [
    ("EURGBP", ("london", 15, "retest"), "1:1 hold", "positive in all three periods (+0.13/+0.12/+0.13R)"),
    ("Germany 40", ("ny", 15, "retest"), "1:3 hold", "strongest index in-sample (+0.32R) and last 8m (+0.16R); no pre-2024 data"),
    ("UK 100", ("ny", 15, "retest"), "1:1 hold", "in-sample +0.06R, last 8m +0.14R; no pre-2024 data"),
    ("Wall Street 30", ("london", 60, "break"), "1:2 hold", "in-sample +0.08R, last 8m +0.11R; no pre-2024 data"),
    ("US SP 500", ("london", 15, "retest"), "1:4 hold", "in-sample +0.10R, last 8m +0.13R; no pre-2024 data"),
]


def legs_for(blob, entries, lo, hi, tag):
    legs = []
    for sym, cfg, key, _why in entries:
        setups = blob["setups"].get(sym, {}).get(cfg)
        if not setups:
            print(f"  ! {sym} {cfg} not in the saved research")
            continue
        for s in in_window(setups, lo, hi):
            r, t_exit, add_r, t_add, t_add_exit = s.outcomes[key]
            g = f"{tag}|{sym}|{s.day}"
            legs.append(Leg(sym, s.t_entry, t_exit, r, s.stop_dist, group=g))
            if add_r is not None:
                legs.append(Leg(sym, t_add, t_add_exit, add_r, s.stop_dist, group=g, is_add=True))
    return legs


def table(name, legs, contracts, out):
    print(f"\n== {name} | {sum(not l.is_add for l in legs)} trades ==")
    print(f"{'capital':>8} {'risk':>5} {'comp':>5} {'pyr':>4} {'dayDD':>6} | {'net $':>9} {'ret%':>7} {'maxDD%':>7} {'PF':>5} "
          f"{'exp $':>7} {'exp R':>7} {'trades':>6} {'avg mo%':>8} {'worst mo%':>9} {'+mo':>6} {'skipped':>7}")
    for cap, risk, comp, pyr, dd in product([350.0, 10000.0], [1.0, 1.8], [False, True], [False, True], [10.0, 20.0]):
        s = simulate_account(legs, contracts, AccountRules(cap, risk, compounding=comp, pyramiding=pyr, daily_dd_cap_pct=dd))
        out.append({"book": name, "capital": cap, "risk_pct": risk, "compounding": comp,
                    "pyramiding": pyr, "daily_dd_cap_pct": dd, **s})
        print(f"{cap:>8.0f} {risk:>5} {str(comp)[0]:>5} {str(pyr)[0]:>4} {dd:>6.0f} | {s['net_pnl']:>9,.0f} {s['return_pct']:>7.1f} "
              f"{s['max_dd_pct']:>7.1f} {s['profit_factor'] or 0:>5.2f} {s['expectancy_usd'] or 0:>7.2f} {s['expectancy_r'] or 0:>+7.3f} "
              f"{s['trades']:>6} {s['avg_month_pct'] or 0:>8.2f} {s['worst_month_pct'] or 0:>9.2f} "
              f"{s['positive_months']:>2}/{s['months']:<3} {s['skipped_unsizable']:>7}")


def main() -> int:
    blob = {"setups": {}, "specs": {}}
    for part in sorted(DATA.glob("setups*.pkl")):
        with open(part, "rb") as f:
            p = pickle.load(f)
        blob["setups"].update(p["setups"])
        blob["specs"].update(p["specs"])
        blob["windows"] = p["windows"]
    W = blob["windows"]
    contracts = {s: Contract(v["value_per_price_per_lot"], v["min_lot"], v["lot_step"], v["max_lot"])
                 for s, v in blob["specs"].items()}

    print("BOOK")
    for sym, cfg, key, why in SHIPPED + CANDIDATES:
        print(f"  {sym:<15} {cfg[0]:<7}{cfg[1]:>3}m {cfg[2]:<7}{key:<10} {why}")
    for label, (lo, hi) in (("last 8 months", W["oos"]), ("2024-01..2026-01 (in-sample)", W["is"]),
                            ("2022-09..2024-01 (holdout)", W["early"])):
        print(f"\n\n############ {label} ############")
        out = []
        table(f"GBPJPY only (shipped ORB_v1) | {label}", legs_for(blob, SHIPPED, lo, hi, "s"), contracts, out)
        table(f"shipped + candidates | {label}", legs_for(blob, SHIPPED + CANDIDATES, lo, hi, "b"), contracts, out)
        (DATA / f"book_sims_{label.split()[0].replace('.', '')}.json").write_text(
            json.dumps(out, indent=1, default=str), encoding="utf-8")

    # what the monthly targets would take, on the shipped config + candidates
    legs = legs_for(blob, SHIPPED + CANDIDATES, *W["oos"], "b")
    print("\n== risk needed for a monthly target ($10,000, compounding, 20% daily cap, last 8 months) ==")
    scan = []
    for risk in (1.0, 1.8, 3.0, 5.0, 8.0, 12.0):
        rules = AccountRules(10000.0, risk, compounding=True, daily_dd_cap_pct=20.0)
        s = simulate_account(legs, contracts, rules)
        mc = shuffled_paths(legs, contracts, rules, n=200, seed=1)
        scan.append({"risk_pct": risk, **{k: s[k] for k in ("return_pct", "avg_month_pct", "worst_month_pct", "max_dd_pct")}, "shuffled": mc})
        print(f"  risk {risk:>5}% | avg month {s['avg_month_pct']:>7.2f}%  worst month {s['worst_month_pct']:>8.2f}%  maxDD {s['max_dd_pct']:>6.1f}%"
              f" | reshuffled: return p5 {mc['return_pct_p5']:>7}%  p50 {mc['return_pct_p50']:>7}%  DD p95 {mc['max_dd_pct_p95']:>6}%  P(loss) {mc['prob_loss']}")
    (DATA / "book_risk_scan.json").write_text(json.dumps(scan, indent=1, default=str), encoding="utf-8")
    print(f"\nsaved {DATA}/book_sims_*.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
