#!/usr/bin/env python
"""
scripts/reprice_backtest.py — [P1.2 / P1.9 / P4.6]

Answers three questions about a saved backtest JSON without re-running it:

  1. Is this run's fill model realistic?     (what fraction of stops filled at
                                              exactly the stop price)
  2. What is it worth if it is not?          (recharge the measured overshoot
                                              and re-derive total R and P&L)
  3. Can this account size even trade this?  (how many signals died on the
                                              broker's minimum lot)

Usage
-----
    python scripts/reprice_backtest.py <run.json> [<run.json> ...]
    python scripts/reprice_backtest.py Implementation/resources/*.json

Method note
-----------
This reprices from the RECORDED trade rows, so it is an estimate applied at the
measured per-symbol mean, not a tick replay. It is the same arithmetic as
research/27 §1.3's headline, and it is deliberately cheap so it can be run on
every saved run. A tick replay (fill_model EMPIRICAL + the raw tick archive) is
the authoritative version and is what CI should gate on.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.backtester.fill_model import get_overshoot_profile  # noqa: E402

STOP_EXITS = {"SL", "TRAIL_SL", "BE_SL"}


def _r_of(t: dict) -> tuple[float, float]:
    """(realised R, risk in price units) for one grouped trade."""
    risk = abs(t["entry_price"] - t.get("initial_stop_loss", t["stop_loss"]))
    if risk <= 0:
        return 0.0, 0.0
    move = (t["exit_price"] - t["entry_price"]) if t["direction"] == "BUY" \
        else (t["entry_price"] - t["exit_price"])
    return move / risk, risk


def report(path: Path) -> None:
    d = json.loads(path.read_text(encoding="utf-8"))
    g = d.get("grouped_trades") or d.get("trades") or []
    if not g:
        print(f"{path.name}: no trades")
        return

    bal0 = d.get("initial_balance", 0.0)
    bal1 = d.get("final_balance", 0.0)

    print("=" * 78)
    print(f"{path.name}")
    print(f"  balance {bal0:,.2f} -> {bal1:,.2f}   ({(bal1 - bal0) / bal0 * 100:+.2f}%)   "
          f"{len(g)} trades")

    # ── 1. is the fill model realistic? ──────────────────────────────────────
    exact = 0
    stops = 0
    by_symbol_stops: Counter = Counter()
    for t in g:
        if t.get("exit_reason") not in STOP_EXITS:
            continue
        stops += 1
        by_symbol_stops[t["symbol"]] += 1
        if abs(t["exit_price"] - t["stop_loss"]) < 1e-9:
            exact += 1
    gapped = sum(1 for t in g if t.get("gap_fill"))

    print(f"\n  fill realism")
    print(f"    stop-type exits ................. {stops}")
    print(f"    filled at EXACTLY the stop ...... {exact} "
          f"({100 * exact / max(stops, 1):.1f}%)")
    print(f"    flagged gap_fill ................ {gapped} "
          f"({100 * gapped / max(len(g), 1):.1f}% of trades)")
    fm = d.get("fill_model")
    if fm:
        print(f"    fill_model ...................... {fm.get('mode')} "
              f"(mean charged {fm.get('mean_overshoot_r')} R)")
    else:
        print(f"    fill_model ...................... ABSENT — run predates the "
              f"realistic-fill model")
        if stops and exact / stops > 0.5:
            print(f"    >> On a jump instrument this is not conservative, it is "
                  f"impossible. Do not size against this run.")

    # ── 2. what is it worth? ─────────────────────────────────────────────────
    rs = []
    dollars_per_r = []
    for t in g:
        r, risk = _r_of(t)
        rs.append(r)
        pnl = t.get("pnl", 0.0)
        if r != 0:
            dollars_per_r.append(abs(pnl / r))
    total_r = sum(rs)
    r_dollar = statistics.median(dollars_per_r) if dollars_per_r else 0.0

    print(f"\n  as booked")
    print(f"    total R ......................... {total_r:+.2f}")
    print(f"    expectancy ...................... {total_r / len(g):+.4f} R/trade")
    print(f"    ~$ per R (median) ............... {r_dollar:,.2f}")

    hard = sum(1 for t in g if t.get("exit_reason") == "SL")

    # A run that already priced its own stops must NOT be charged again — the
    # correction below exists to estimate what a legacy run is missing, and
    # applying it on top of an honest run double-counts the very thing it is
    # measuring. Exactly the kind of quietly-wrong number this script exists
    # to catch, so it refuses rather than printing it.
    if fm and str(fm.get("mode", "OFF")).upper() != "OFF":
        print(f"\n  corrected for unbooked stop overshoot")
        print(f"    SKIPPED — this run already charged {fm.get('total_overshoot_r', 0):+.2f} R "
              f"at the fill model's own {fm.get('mode')} setting, so the figures above")
        print(f"    are ALREADY corrected. Re-running with stop_fill_model=OFF shows what")
        print(f"    the legacy harness would have reported: "
              f"{total_r + float(fm.get('total_overshoot_r', 0) or 0):+.2f} R "
              f"({(total_r + float(fm.get('total_overshoot_r', 0) or 0)) / len(g):+.4f} R/trade).")
        _signal_mortality(d, g, bal0)
        _by_symbol(g)
        return

    print(f"\n  corrected for unbooked stop overshoot")
    print(f"    {'scope':<28} {'charge':>8} {'total R':>10} {'R/trade':>10} {'~$':>12}")
    for label, n in (("hard SL exits only", hard), ("all stop-type exits", stops)):
        for scope, ov in (("measured mean", None), ("-0.33 R", 0.33), ("-0.40 R", 0.40)):
            if ov is None:
                # per-symbol mean, weighted by that symbol's share of stops
                charged = sum(
                    by_symbol_stops[s] * get_overshoot_profile(s).mean
                    for s in by_symbol_stops
                )
                if label == "hard SL exits only":
                    charged *= hard / max(stops, 1)
                shown = "per-symbol"
            else:
                charged = n * ov
                shown = scope
            corrected = total_r - charged
            print(f"    {label:<28} {shown:>8} {corrected:>+10.2f} "
                  f"{corrected / len(g):>+10.4f} {corrected * r_dollar:>+12,.2f}")

    _signal_mortality(d, g, bal0)
    _by_symbol(g)


def _signal_mortality(d: dict, g: list, bal0: float) -> None:
    # ── 3. can this account size trade this? ─────────────────────────────────
    funnel = d.get("rejection_funnel", {})
    blocked = d.get("blocked_signals", [])
    minlot = sum(
        v for k, v in (funnel.get("risk_rejections") or {}).items()
        if "minimum tradeable volume" in k
    )
    pre = funnel.get("pre_risk_rejections") or {}
    concurrency = sum(pre.values()) or sum(
        1 for b in blocked if b.get("gate") in
        ("same_direction_already_open", "min_bars_between_entries")
    )
    raw = funnel.get("raw_signals") or (funnel.get("total_evaluated", 0) + concurrency)

    print(f"\n  signal mortality  (raw setups the strategy found: {raw})")
    if raw:
        for label, n in (("blocked by concurrency", concurrency),
                         ("below broker minimum lot", minlot),
                         ("filled", len(g))):
            print(f"    {label:<28} {n:>5}  ({100 * n / raw:>5.1f}%)")
    if minlot and raw and minlot / raw > 0.25:
        print(f"    >> {100 * minlot / raw:.0f}% of this strategy's signals cannot be "
              f"expressed at ${bal0:,.0f}. The position it wants is smaller than the")
        print(f"       broker will accept, so this account trades a biased subsample "
              f"— the cheap-stop trades — not the strategy.")

def _by_symbol(g: list) -> None:
    # ── per symbol / direction ───────────────────────────────────────────────
    bysd = defaultdict(list)
    for t in g:
        bysd[(t["symbol"], t["direction"])].append(_r_of(t)[0])
    print(f"\n  by symbol x direction")
    for k in sorted(bysd):
        v = bysd[k]
        wins = sum(1 for x in v if x > 0)
        print(f"    {k[0]:<20} {k[1]:<5} n={len(v):>4}  sumR={sum(v):>+8.2f}  "
              f"win%={100 * wins / len(v):>5.1f}  mean={statistics.mean(v):+.4f}R")


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    for a in args:
        p = Path(a)
        if p.is_dir():
            for f in sorted(p.glob("*.json")):
                report(f)
        elif p.exists():
            report(p)
        else:
            print(f"not found: {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
