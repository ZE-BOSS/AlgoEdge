#!/usr/bin/env python
"""
scripts/run_strategy_book_sims.py

Dollars for whatever the VWAP / APA strip-downs selected.

The research modules answer "which confluences and which exit", in R. This turns
a chosen configuration into money on a real account — the same simulator the ORB
book used (backend/analytics/money_sim.py): every capital / risk / compounding /
pyramiding / daily-cap combination, the broker's real lot grid, and trades refused
when the minimum lot would exceed the risk budget.

A configuration is named here EXPLICITLY rather than read from the research pick,
because a per-market pick with 40-140 trades is mostly selection noise: state the
configuration the pooled evidence supports, then measure it on every market.

    # everything the study saved, live-default gates, per market and pooled
    python scripts/run_strategy_book_sims.py --strategy apa --gates head_not_breached session_ok --exit 1:3

    # a specific book
    python scripts/run_strategy_book_sims.py --strategy vwap --setup reversion \
        --gates in_session_window --exit "sigma2 hold" --markets EURUSD GBPUSD
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics import apa_research as apa  # noqa: E402
from backend.analytics import vwap_research as vw  # noqa: E402
from backend.analytics.money_sim import AccountRules, Contract, Leg, shuffled_paths, simulate_account  # noqa: E402

DIRS = {"vwap": ROOT / "data" / "vwap_research", "apa": ROOT / "data" / "apa_research"}


def load(kind: str) -> tuple[dict[str, list], dict[str, Contract], dict]:
    per_market: dict[str, list] = {}
    contracts: dict[str, Contract] = {}
    windows: dict = {}
    for f in sorted(DIRS[kind].glob("*.pkl")):
        with open(f, "rb") as fh:
            blob = pickle.load(fh)
        windows = blob.get("windows", windows)
        for sym, spec in blob.get("specs", {}).items():
            contracts[sym] = Contract(spec["value_per_price_per_lot"], spec["min_lot"],
                                      spec["lot_step"], spec["max_lot"])
        for k, v in blob.get("candidates", {}).items():
            if k.endswith("__pick") or not isinstance(v, list):
                continue
            per_market.setdefault(k, []).extend(v)
    return per_market, contracts, windows


def legs_for(cands: list, key: str, mod, gates: tuple[str, ...], setup: str | None,
             lo: int, hi: int, first_only: bool, max_per: int) -> list[Leg]:
    sub = mod.in_window(cands, lo, hi)
    if mod is vw:
        picked = mod.apply_combination(sub, use=gates, first_only=first_only,
                                       max_per_session=max_per,
                                       setups=(setup,) if setup else None)
    else:
        picked = mod.apply_combination(sub, use=gates, max_per_day=max_per)
    out = []
    for c in picked:
        if not vw.took_trade(c, key):      # structural target already behind price
            continue
        r, t_exit = c.outcomes[key][0], c.outcomes[key][1]
        out.append(Leg(c.symbol, c.t_entry, t_exit, r, c.stop_dist,
                       group=f"{c.symbol}|{c.t_entry}"))
    return out


def table(name: str, legs: list[Leg], contracts: dict[str, Contract], out: list) -> None:
    print(f"\n== {name} | {len(legs)} trades ==")
    if not legs:
        return
    print(f"{'capital':>8} {'risk':>5} {'comp':>5} {'pyr':>4} {'dayDD':>6} | {'net $':>9} {'ret%':>7} "
          f"{'maxDD%':>7} {'PF':>5} {'exp $':>7} {'exp R':>7} {'trades':>6} {'avg mo%':>8} {'+mo':>6} {'skipped':>7}")
    for cap, risk, comp, pyr, dd in product([350.0, 10000.0], [1.0, 1.8], [False, True], [False], [10.0, 20.0]):
        s = simulate_account(legs, contracts, AccountRules(cap, risk, compounding=comp,
                                                          pyramiding=pyr, daily_dd_cap_pct=dd))
        out.append({"book": name, "capital": cap, "risk_pct": risk, "compounding": comp,
                    "pyramiding": pyr, "daily_dd_cap_pct": dd, **s})
        print(f"{cap:>8.0f} {risk:>5} {str(comp)[0]:>5} {str(pyr)[0]:>4} {dd:>6.0f} | {s['net_pnl']:>9,.0f} "
              f"{s['return_pct']:>7.1f} {s['max_dd_pct']:>7.1f} {s['profit_factor'] or 0:>5.2f} "
              f"{s['expectancy_usd'] or 0:>7.2f} {s['expectancy_r'] or 0:>+7.3f} {s['trades']:>6} "
              f"{s['avg_month_pct'] or 0:>8.2f} {s['positive_months']:>2}/{s['months']:<3} {s['skipped_unsizable']:>7}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=["vwap", "apa"], required=True)
    ap.add_argument("--gates", nargs="*", default=[])
    ap.add_argument("--exit", required=True, help='variant key, e.g. "1:3" or "sigma2 hold"')
    ap.add_argument("--setup", default=None, help="vwap only: pullback | reversion")
    ap.add_argument("--markets", nargs="*", default=None)
    ap.add_argument("--first-only", action="store_true", help="vwap only: first candidate per session")
    ap.add_argument("--max-per", type=int, default=0, help="cap per session (vwap) / per day (apa)")
    args = ap.parse_args()

    mod = vw if args.strategy == "vwap" else apa
    per_market, contracts, windows = load(args.strategy)
    if not per_market:
        raise SystemExit(f"no saved {args.strategy} research in {DIRS[args.strategy]} yet")
    if args.markets:
        per_market = {k: v for k, v in per_market.items() if k in set(args.markets)}

    gates = tuple(args.gates)
    unknown = [g for g in gates if g not in mod.CONFLUENCES]
    if unknown:
        raise SystemExit(f"unknown confluence(s) {unknown}; choose from {list(mod.CONFLUENCES)}")

    print(f"{args.strategy.upper()} | gates: {', '.join(gates) or 'none'} | exit {args.exit}"
          f"{' | ' + args.setup if args.setup else ''}")
    results: list = []
    for wname, (lo, hi) in ((k, v) for k, v in windows.items() if k != "pick"):
        book: list[Leg] = []
        for sym, cands in sorted(per_market.items()):
            legs = legs_for(cands, args.exit, mod, gates, args.setup, lo, hi,
                            args.first_only, args.max_per)
            book += legs
            if legs:
                table(f"{sym} | {wname}", legs, contracts, results)
        table(f"ALL MARKETS | {wname}", book, contracts, results)
        if book:
            rules = AccountRules(10000.0, 1.8, compounding=True, daily_dd_cap_pct=10.0)
            mc = shuffled_paths(book, contracts, rules, n=200, seed=3)
            print(f"  reshuffled ({wname}, $10k/1.8%/compounding): return p5 {mc['return_pct_p5']}% "
                  f"p50 {mc['return_pct_p50']}%  DD p95 {mc['max_dd_pct_p95']}%  P(loss) {mc['prob_loss']}")
            results.append({"book": f"ALL MARKETS | {wname}", "shuffled": mc})

    out = DIRS[args.strategy] / f"book_sims_{args.strategy}.json"
    out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
