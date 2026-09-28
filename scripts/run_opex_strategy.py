#!/usr/bin/env python
"""
scripts/run_opex_strategy.py

One strategy out of three separate findings: the expiry calendar, the volatility
regime, and intraday timing.

THE THREE PIECES
----------------
1. CALENDAR. Equity indices fall on monthly options expiry (the third Friday)
   relative to other Fridays. Nasdaq 100: -0.23% over 480 expiries and 40 years,
   t -3.14, negative in every decade. FX shows nothing (|t| < 0.35 on 193
   events each), which is what makes it options-related rather than a Friday
   effect. Measured in Implementation/EXPIRY-AND-OPTIONS-2026-09-27.md.

2. REGIME. Dealer gamma is not observable without a chain and an assumption
   about who is short. VIX level and VIX term structure are free, observable and
   move with the same regime: contango = calm, hedging demand cheap; backwardation
   = stress, hedging demand immediate. Used here only as a FILTER on the
   calendar trade, never as a signal of its own.

3. INTRADAY. The close-to-close version carries overnight gap risk for an effect
   that happens during the session. This tests whether the move is capturable
   INSIDE the expiry session -- open to close, and the afternoon window where the
   gamma literature puts the unpin -- which removes the gap and shortens the hold
   from ~18 hours to a few.

WHAT THIS CANNOT DO, STATED PLAINLY
-----------------------------------
It cannot see individual option trades. Whether a specific call being bought at
11:04 moves the index is a question that needs real-time OPRA (~$599/month and
up). This script tests the part of that hypothesis that is free: if the AGGREGATE
of a month of hedging is visible on the calendar and in the regime, the finer
data is worth buying; if the free aggregate shows nothing, the paid version is
not going to rescue it.

    py -3.12 scripts/run_opex_strategy.py
    py -3.12 scripts/run_opex_strategy.py --monthly
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_app_form_check import broker_utc_offset_hours, ny_sessions  # noqa: E402
from scripts.run_expiry_study import fred_series, is_triple_witching, opex_days  # noqa: E402
from scripts.run_published_strategies import FEEDS, load  # noqa: E402
from scripts.run_vix_regime_filter import fred, regimes  # noqa: E402

OUT = ROOT / "data" / "opex"
NY = pytz.timezone("America/New_York")
# Deriv's index history starts 2024-01-22; FundedNext's M5 retention is ~100k bars.
INTRADAY = [("deriv", "US Tech 100"), ("deriv", "US SP 500"), ("deriv", "Germany 40"),
            ("fundednext", "US30"), ("fundednext", "SPX500")]


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


# ── the daily (close-to-close) version, on the long FRED history ────────────
def daily_trades(series_id: str, reg: dict, cost_pct: float = 0.01) -> list[dict]:
    """Short from the prior close to the expiry-Friday close, once a month."""
    days, close = fred_series(series_id)
    idx = {d: i for i, d in enumerate(days)}
    out = []
    for d in sorted(opex_days(days[0], days[-1])):
        i = idx.get(d)
        if not i:
            continue
        r = -(close[i] - close[i - 1]) / close[i - 1] * 100.0 - cost_pct
        info = reg.get(d) or {}
        out.append({"date": d, "ret": r, "tw": is_triple_witching(d),
                    "level": info.get("level", "n/a"), "term": info.get("term", "n/a"),
                    "vix": info.get("vix")})
    return out


# ── the intraday versions, on the CFDs we trade ─────────────────────────────
def intraday_trades(feed: str, symbol: str, reg: dict, mode: str,
                    cost_pct: float = 0.01) -> list[dict]:
    """Short inside the expiry session. `mode` picks the window:

        open_close  in at the cash open, out at the cash close
        afternoon   in halfway through the session, out at the close
        last2h      in with two hours to go, out at the close
    """
    bars_dir, _ = FEEDS[feed]
    try:
        b = load(bars_dir, symbol)
    except FileNotFoundError:
        return []
    if len(b["time"]) < 20_000:
        return []
    off = broker_utc_offset_hours(b)
    sessions = ny_sessions(b, off)
    o, c, t = b["open"], b["close"], b["time"]

    out = []
    for first, last in sessions:
        d = datetime.fromtimestamp(int(t[first]) - off * 3600, timezone.utc).astimezone(NY).date()
        if d not in opex_days(d, d):
            continue
        span = last - first
        if span < 20:
            continue
        if mode == "open_close":
            entry_i = first
        elif mode == "afternoon":
            entry_i = first + span // 2
        else:                                  # last2h: 24 M5 bars an hour
            entry_i = max(first, last - 24)
        entry, exit_px = o[entry_i], c[last]
        r = -(exit_px - entry) / entry * 100.0 - cost_pct
        info = reg.get(d) or {}
        out.append({"date": d, "ret": r, "tw": is_triple_witching(d),
                    "level": info.get("level", "n/a"), "term": info.get("term", "n/a"),
                    "vix": info.get("vix")})
    return out


# ── scoring ─────────────────────────────────────────────────────────────────
def score(trades: list[dict]) -> dict | None:
    if len(trades) < 8:
        return None
    r = np.array([x["ret"] for x in trades])
    eq = np.cumprod(1 + r / 100.0)
    peak = np.maximum.accumulate(eq)
    return {
        "n": len(r), "mean": float(r.mean()), "win": float((r > 0).mean() * 100),
        "t": float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))) if r.std() else 0.0,
        "sharpe": float(r.mean() / r.std(ddof=1) * np.sqrt(12)) if r.std() else 0.0,
        "total": float((eq[-1] - 1) * 100), "dd": float(((peak - eq) / peak).max() * 100),
        "worst": float(r.min()), "best": float(r.max()),
    }


ROW = (f"  {'variant':40s} {'N':>4s} {'mean%':>8s} {'win%':>6s} {'t':>6s} {'Sh':>6s} "
       f"{'total%':>9s} {'DD%':>6s} {'worst%':>7s}")


def line(label, st):
    if st is None:
        print(f"  {label:40s}   -   too few")
        return
    print(f"  {label:40s} {st['n']:>4d} {st['mean']:>+8.3f} {st['win']:>5.1f}% {st['t']:>+6.2f} "
          f"{st['sharpe']:>+6.2f} {st['total']:>+8.1f}% {st['dd']:>5.1f}% {st['worst']:>+7.2f}")


def by_filter(trades: list[dict], key: str, want) -> list[dict]:
    want = {want} if isinstance(want, str) else set(want)
    return [x for x in trades if x.get(key) in want]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monthly", action="store_true", help="month-by-month table for 2026")
    ap.add_argument("--split", default="2016-01-01", help="in-sample ends here (daily test)")
    ap.add_argument("--cost", type=float, default=0.01, help="round-trip cost, percent")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    reg = regimes()
    results: dict = {}

    print("\nOPEX STRATEGY -- short the index into monthly options expiry")
    print(f"costed at {args.cost:g}% a round trip; 12 trades a year\n")

    # ── 1. the calendar alone, long history ────────────────────────────────
    print("1. CALENDAR ONLY -- close to close, FRED daily")
    print(ROW)
    print("  " + "-" * (len(ROW) - 2))
    daily = {}
    for name, sid in (("Nasdaq 100", "NASDAQ100"), ("S&P 500", "SP500"), ("Dow 30", "DJIA")):
        tr = daily_trades(sid, reg, args.cost)
        daily[name] = tr
        line(name, score(tr))
        results[f"calendar|{name}"] = score(tr)

    # ── 2. the regime filter on top ────────────────────────────────────────
    print("\n2. + REGIME FILTER (Nasdaq 100, the only one with a real sample)")
    print(ROW)
    print("  " + "-" * (len(ROW) - 2))
    ndx = daily["Nasdaq 100"]
    line("all expiries", score(ndx))
    for key, values in (("term", ["contango", "backwardation"]),
                        ("level", ["low", "mid", "high"])):
        for v in values:
            st = score(by_filter(ndx, key, v))
            line(f"{key} = {v}", st)
            results[f"regime|{key}={v}"] = st
    line("triple witching only", score([x for x in ndx if x["tw"]]))
    line("monthly, not triple witching", score([x for x in ndx if not x["tw"]]))

    # walk it forward: pick the regime on the early half, score the late half
    split = date.fromisoformat(args.split)
    early = [x for x in ndx if x["date"] < split]
    late = [x for x in ndx if x["date"] >= split]
    print(f"\n   walk-forward: choose on < {split}, score on >= {split}")
    for key, values in (("term", ["contango", "backwardation"]),
                        ("level", ["low", "mid", "high"])):
        scored = {v: score(by_filter(early, key, v)) for v in values}
        usable = {k: v for k, v in scored.items() if v}
        if not usable:
            continue
        pick = max(usable, key=lambda k: usable[k]["mean"])
        base = score(late)
        got = score(by_filter(late, key, pick))
        print(f"   {key:6s} pick={pick:14s} early {usable[pick]['mean']:+.3f}%  "
              f"late {got['mean'] if got else float('nan'):+.3f}%  "
              f"(late unfiltered {base['mean']:+.3f}%)  "
              f"{'BETTER' if got and base and got['mean'] > base['mean'] else 'no better'}")

    # ── 3. intraday ────────────────────────────────────────────────────────
    print("\n3. INTRADAY -- inside the expiry session, no overnight gap")
    print(ROW)
    print("  " + "-" * (len(ROW) - 2))
    intra: dict[str, list[dict]] = {}
    for feed, symbol in INTRADAY:
        for mode in ("open_close", "afternoon", "last2h"):
            tr = intraday_trades(feed, symbol, reg, mode, args.cost)
            if not tr:
                continue
            intra[f"{symbol}|{mode}"] = tr
            line(f"{symbol} [{feed}] {mode}", score(tr))
            results[f"intraday|{symbol}|{mode}"] = score(tr)
        print()

    pooled = defaultdict(list)
    for key, tr in intra.items():
        pooled[key.split("|")[1]] += tr
    print("  pooled across the index CFDs")
    for mode, tr in pooled.items():
        line(f"ALL {mode}", score(tr))
        results[f"intraday|pooled|{mode}"] = score(tr)

    # ── 4. month by month ──────────────────────────────────────────────────
    if args.monthly:
        print("\n4. MONTH BY MONTH -- Nasdaq 100 close-to-close, 2026")
        print(f"  {'month':9s} {'ret%':>8s}  {'VIX':>6s}  {'regime':>14s} {'TW':>3s}")
        for x in sorted(ndx, key=lambda y: y["date"]):
            if x["date"].year != 2026:
                continue
            vix = f"{x['vix']:.1f}" if x["vix"] else "  -"
            print(f"  {month_key(x['date']):9s} {x['ret']:>+8.3f}  {vix:>6s}  "
                  f"{x['term']:>14s} {'TW' if x['tw'] else '':>3s}")
        ytd = [x for x in ndx if x["date"].year == 2026]
        st = score(ytd)
        if st:
            print(f"  {'2026 YTD':9s} {sum(x['ret'] for x in ytd):>+8.3f}   "
                  f"({st['n']} expiries, {st['win']:.0f}% win)")

    # -- 5. the three findings stacked ------------------------------------
    # last2h beat open_close on every instrument, high VIX beat the other
    # terciles AND held up walk-forward, and the ordinary monthly expiries beat
    # the quarterly ones. Stacking three filters chosen on this data is exactly
    # how a 126-event sample gets talked into anything, so the count is printed
    # next to every row and the out-of-sample split is printed under it.
    print(chr(10) + "5. COMBINED -- last two hours, per filter (pooled index CFDs)")
    print(ROW)
    print("  " + "-" * (len(ROW) - 2))
    pool = pooled["last2h"]
    line("last2h, no filter", score(pool))
    line("  + skip triple witching", score([x for x in pool if not x["tw"]]))
    line("  + VIX high only", score(by_filter(pool, "level", "high")))
    line("  + VIX mid or high", score(by_filter(pool, "level", ["mid", "high"])))
    line("  + skip TW AND VIX mid/high",
         score([x for x in by_filter(pool, "level", ["mid", "high"]) if not x["tw"]]))
    results["combined|last2h+filters"] = score(
        [x for x in by_filter(pool, "level", ["mid", "high"]) if not x["tw"]])

    cut = date(2026, 1, 1)
    print(chr(10) + f"   split at {cut}: everything before is what the filters were read off")
    for label, sel in (("last2h, no filter", pool),
                       ("last2h + skip TW + VIX mid/high",
                        [x for x in by_filter(pool, "level", ["mid", "high"]) if not x["tw"]])):
        a = score([x for x in sel if x["date"] < cut])
        b = score([x for x in sel if x["date"] >= cut])
        print(f"   {label:34s} before {a['mean'] if a else float('nan'):+.3f}% (n {a['n'] if a else 0:>3d})"
              f"   2026 {b['mean'] if b else float('nan'):+.3f}% (n {b['n'] if b else 0:>3d})")

    print(chr(10) + "6. MONTH BY MONTH 2026 -- last two hours, pooled index CFDs")
    print(f"  {'month':9s} {'trades':>7s} {'sum %':>9s} {'avg %':>8s}  {'regime':>14s}")
    by_month: dict[str, list[dict]] = defaultdict(list)
    for x in pool:
        if x["date"].year == 2026:
            by_month[month_key(x["date"])].append(x)
    run = 0.0
    for m in sorted(by_month):
        rs = [y["ret"] for y in by_month[m]]
        run += sum(rs)
        print(f"  {m:9s} {len(rs):>7d} {sum(rs):>+9.3f} {np.mean(rs):>+8.3f}  "
              f"{by_month[m][0]['term']:>14s}")
    print(f"  {'2026 YTD':9s} {sum(len(v) for v in by_month.values()):>7d} {run:>+9.3f}")

    # -- 7. the honest sample ---------------------------------------------
    # Five US/EU equity index CFDs on the same expiry are not five independent
    # trades, they are one bet placed five times. Pooling them multiplies n by
    # five and divides the standard error by sqrt(5), which flatters the t-stat
    # by more than a factor of two. Collapsing each expiry DATE to one number --
    # what an equally-weighted basket would actually have returned -- gives the
    # sample size the statistics are entitled to.
    print(chr(10) + "7. ONE EVENT PER EXPIRY -- an equal-weight basket, not five separate bets")
    print(ROW)
    print("  " + "-" * (len(ROW) - 2))
    for mode in ("open_close", "afternoon", "last2h"):
        by_date: dict[date, list[float]] = defaultdict(list)
        meta: dict[date, dict] = {}
        for x in pooled[mode]:
            by_date[x["date"]].append(x["ret"])
            meta[x["date"]] = x
        basket = [{**meta[d], "ret": float(np.mean(v))} for d, v in sorted(by_date.items())]
        line(f"basket {mode}", score(basket))
        results[f"basket|{mode}"] = score(basket)
        if mode == "last2h":
            before = score([x for x in basket if x["date"] < date(2026, 1, 1)])
            after = score([x for x in basket if x["date"] >= date(2026, 1, 1)])
            print(f"     before 2026 {before['mean'] if before else float('nan'):+.3f}% "
                  f"(n {before['n'] if before else 0})    "
                  f"2026 {after['mean'] if after else float('nan'):+.3f}% "
                  f"(n {after['n'] if after else 0})")
            results["basket|last2h|before2026"] = before
            results["basket|last2h|2026"] = after

    (OUT / "opex_strategy.json").write_text(json.dumps(results, indent=1, default=float))
    print(f"\n-> {OUT / 'opex_strategy.json'}")


if __name__ == "__main__":
    main()
