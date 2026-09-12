#!/usr/bin/env python
"""
scripts/run_account_sims.py

Dollars, not R: every portfolio in the ORB research, plus the non-ORB families
that held up on the 2022-23 holdout, run through the account simulator under
each combination of capital, risk, compounding, pyramiding and daily cap.

Reads data/orb_research/setups.pkl (scripts/run_orb_research.py). Writes
data/orb_research/account_sims.json.
"""

from __future__ import annotations

import json
import math
import pickle
import statistics
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.money_sim import AccountRules, Contract, Leg, shuffled_paths, simulate_account  # noqa: E402
from backend.analytics.orb_research import choose_config_robust, choose_filter, in_window, rs  # noqa: E402

DATA = ROOT / "data" / "orb_research"

# Non-ORB families that were positive on BOTH the 2022-23 holdout and the last 8 months
# (settings chosen on 2024-01..2026-01; scripts/run_strategy_search.py).
OTHER = [
    ("BTCUSD", "vol_breakout", "H1", {"n": 20, "m": 2.0, "side": "both"}),
    ("BTCUSD", "donchian", "H1", {"n": 55, "k": 2.0, "exit": "channel", "side": "long"}),
    ("EURUSD", "rsi2", "H1", {"th": 5, "hold": 24, "side": "long"}),
    ("XAUUSD", "ema_pullback", "H1", {"emas": (50, 200), "rr": 10.0, "side": "long"}),
]


def t_stat(r):
    if len(r) < 3 or statistics.stdev(r) == 0:
        return 0.0
    return statistics.mean(r) / (statistics.stdev(r) / math.sqrt(len(r)))


def orb_legs(setups, key, flt, lo, hi, tag):
    legs = []
    for s in in_window(setups, lo, hi):
        if flt and s.features[flt[0]] is not flt[1]:
            continue
        r, t_exit, add_r, t_add, t_add_exit = s.outcomes[key]
        g = f"{tag}|{s.symbol}|{s.day}"
        legs.append(Leg(s.symbol, s.t_entry, t_exit, r, s.stop_dist, group=g))
        if add_r is not None:
            legs.append(Leg(s.symbol, t_add, t_add_exit, add_r, s.stop_dist, group=g, is_add=True))
    return legs


def other_legs(lo, hi):
    import MetaTrader5 as mt5

    from backend.analytics.strategy_search import FAMILIES, Bars, simulate
    mt5.initialize()
    out, specs = {}, {}
    for sym, fam_name, tf, params in OTHER:
        fam = next(f for f in FAMILIES if f.name == fam_name)
        info = mt5.symbol_info(sym)
        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, datetime(2022, 1, 1, tzinfo=timezone.utc),
                                     datetime(2026, 9, 12, tzinfo=timezone.utc))
        b = Bars.from_rates(sym, tf, rates, info.point)
        sigs, aux = fam.build(b, dict(params))
        trades = simulate(b, sigs, aux)
        out[f"{sym} {fam_name}"] = [Leg(sym, t.t_entry, t.t_exit, t.r, t.stop_dist, group=f"{fam_name}|{sym}|{t.t_entry}")
                                    for t in trades]
        specs[sym] = Contract(info.trade_tick_value / info.trade_tick_size, info.volume_min, info.volume_step, info.volume_max)
    mt5.shutdown()
    return out, specs


def main() -> int:
    blob = {"setups": {}, "specs": {}}
    for part in sorted(DATA.glob("setups*.pkl")):          # parallel batches merge here
        with open(part, "rb") as f:
            p = pickle.load(f)
        blob["setups"].update(p["setups"])
        blob["specs"].update(p["specs"])
        blob["windows"] = p["windows"]
    W = blob["windows"]
    contracts = {s: Contract(v["value_per_price_per_lot"], v["min_lot"], v["lot_step"], v["max_lot"])
                 for s, v in blob["specs"].items()}

    # ── which ORB markets make the book: decided on IN-SAMPLE evidence only ──
    # Robust (neighbourhood) selection, then one confluence, both on in-sample only.
    picks = {}
    for sym, by_cfg in blob["setups"].items():
        pick = choose_config_robust(by_cfg, *W["is"])
        if not pick:
            continue
        cfg, key = pick
        flt = choose_filter(by_cfg[cfg], key, *W["is"])
        ins = rs(in_window(by_cfg[cfg], *W["is"]), key)
        if len(ins) >= 60 and statistics.mean(ins) > 0 and t_stat(ins) >= 1.5:
            picks[sym] = (cfg, key, flt)
    print("ORB book (robust pick, in-sample t >= 1.5):")
    for s, (c, k, f) in picks.items():
        print(f"  {s:<14} {c[0]:<6} {c[1]}m {c[2]:<6} {k:<10} confluence: {f[0] + '=' + str(f[1]) if f else '-'}")

    other, other_specs = other_legs(*W["is"])
    contracts.update({k: v for k, v in other_specs.items() if k not in contracts})

    def book(lo, hi, use_filter: bool, include_other: bool):
        legs = []
        for sym, (cfg, key, flt) in picks.items():
            legs += orb_legs(blob["setups"][sym][cfg], key, flt if use_filter else None, lo, hi, "orb")
        if include_other:
            for name, ls in other.items():
                legs += [l for l in ls if lo <= l.t_entry < hi]
        return legs

    windows = {"last 8 months": W["oos"], "2024-01..2026-09": (W["is"][0], W["oos"][1]), "2022-09..2024-01 (FX/metals/crypto only)": W["early"]}
    books = {"ORB book": (False, False), "ORB book + best confluence": (True, False), "ORB book + other families": (False, True)}
    single = {"GBPJPY only": "GBPJPY"} if "GBPJPY" in picks else {}

    results = {"picks": {s: [list(c), k, list(f) if f else None] for s, (c, k, f) in picks.items()}, "runs": []}
    grid = list(product([350.0, 10000.0], [1.0, 1.8], [False, True], [False, True], [10.0, 20.0]))
    for wname, (lo, hi) in windows.items():
        sets = {name: book(lo, hi, *flags) for name, flags in books.items()}
        for name, sym in single.items():
            cfg, key, flt = picks[sym]
            sets[name] = orb_legs(blob["setups"][sym][cfg], key, None, lo, hi, "orb")
        for bname, legs in sets.items():
            print(f"\n== {bname} | {wname} | {sum(not l.is_add for l in legs)} trades ==")
            print(f"{'capital':>8} {'risk':>5} {'comp':>5} {'pyr':>4} {'dayDD':>5} | {'net $':>10} {'ret%':>8} {'maxDD%':>7} {'PF':>5} "
                  f"{'exp $':>8} {'exp R':>7} {'trades':>6} {'avg mo%':>8} {'worst mo%':>9} {'+mo':>5} {'unsizable':>9}")
            for cap, risk, comp, pyr, dd in grid:
                s = simulate_account(legs, contracts, AccountRules(cap, risk, compounding=comp, pyramiding=pyr,
                                                                   daily_dd_cap_pct=dd))
                results["runs"].append({"book": bname, "window": wname, "capital": cap, "risk_pct": risk,
                                        "compounding": comp, "pyramiding": pyr, "daily_dd_cap_pct": dd, **s})
                print(f"{cap:>8.0f} {risk:>5} {str(comp)[0]:>5} {str(pyr)[0]:>4} {dd:>5.0f} | {s['net_pnl']:>10,.0f} {s['return_pct']:>8.1f} "
                      f"{s['max_dd_pct']:>7.1f} {s['profit_factor'] or 0:>5.2f} {s['expectancy_usd'] or 0:>8.2f} {s['expectancy_r'] or 0:>+7.3f} "
                      f"{s['trades']:>6} {s['avg_month_pct'] or 0:>8.2f} {s['worst_month_pct'] or 0:>9.2f} "
                      f"{s['positive_months']:>2}/{s['months']:<2} {s['skipped_unsizable']:>9}")

    # ── what the monthly targets would take: risk scan with compounding, last 8 months + full ──
    print("\n== risk needed for a monthly target (ORB book, $10,000, compounding, 20% daily cap) ==")
    results["risk_scan"] = []
    for wname in ("last 8 months", "2024-01..2026-09"):
        lo, hi = windows[wname]
        legs = book(lo, hi, False, False)
        for risk in (1.0, 1.8, 3.0, 5.0, 8.0):
            for pyr in (False, True):
                rules = AccountRules(10000.0, risk, compounding=True, pyramiding=pyr, daily_dd_cap_pct=20.0)
                s = simulate_account(legs, contracts, rules)
                mc = shuffled_paths(legs, contracts, rules, n=200, seed=1)
                row = {"window": wname, "risk_pct": risk, "pyramiding": pyr, **{k: s[k] for k in
                       ("return_pct", "avg_month_pct", "median_month_pct", "worst_month_pct", "max_dd_pct")}, "shuffled": mc}
                results["risk_scan"].append(row)
                print(f"{wname:<18} risk {risk:>4}% pyr {str(pyr)[0]} | avg month {s['avg_month_pct']:>6.2f}%  worst {s['worst_month_pct']:>7.2f}%  "
                      f"maxDD {s['max_dd_pct']:>5.1f}% | reshuffled: ret p5 {mc['return_pct_p5']}% p50 {mc['return_pct_p50']}%  "
                      f"DD p95 {mc['max_dd_pct_p95']}%  P(loss) {mc['prob_loss']}")

    (DATA / "account_sims.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"\nsaved {DATA / 'account_sims.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
