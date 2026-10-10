#!/usr/bin/env python
"""
scripts/run_h4_deep.py

The deeper 4-hour search: two new models, and an optimisation pass over the
one that already survived.

WHAT IS BEING TESTED
--------------------
1. **BREAK AND RETEST** - price breaks a level, comes back to it, and the
   retest is confirmed by a WIDE 4-hour candle in the direction of the break.
   Entered on the candle after that one. "Wide" is defined against the
   instrument's own recent range, not a fixed number of points, so it means
   the same thing on gold and on EURUSD.

2. **SESSION ALTERNATION** - "if New York sold, Asia probably buys; if London
   bought, New York probably sells." A clean, falsifiable claim: the sign of
   one session's return predicts the NEXT session's, negatively. Measured as
   a conditional probability first - if the base rate is 50% there is nothing
   to trade - and only then as a strategy.

3. **OPTIMISATION of the surviving Donchian trend system** - a wider grid
   around the pick, plus a per-slot give-back brake, plus pyramiding, all
   judged on the SAME out-of-sample period and against the SAME surrogate.

EVERY RESULT IS REPORTED IN DOLLARS on $10,000, because a percentage hides
whether the account could have taken the trade at all: the lot-size floor
means a $10,000 account cannot trade some instruments at some stops, and
`run_account` skips those rather than pretending.

    python scripts/run_h4_deep.py --feed deriv
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_crt_study import aggregate, buckets, drop_overlaps  # noqa: E402
from scripts.run_h4_lab import (H4, MIN_STOP_SPREADS, SLIP_POINTS,  # noqa: E402
                                atr_h4, context, money, score, split_at,
                                trades_for)
from scripts.run_published_strategies import FEEDS, load  # noqa: E402

REAL_DERIV = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY",
              "BTCUSD", "US Tech 100", "US SP 500", "Germany 40"]
REAL_FN = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "BTCUSD",
           "SPX500", "US30"]


# -- model: break and retest, confirmed by a wide candle -------------------


def break_retest(b: dict, h4: dict, ctx: dict, sym: str, *, look: int,
                 wide_mult: float, trail_atr: float, atr_stop: float,
                 retest_within: int = 6) -> list[dict]:
    """Break a `look`-candle extreme, come back to it, confirm with a wide candle.

    The confirming candle must close beyond its own midpoint in the break's
    direction AND have a range at least `wide_mult` x ATR. That is what "a
    big trending four-hour candle" means in a form a computer can check, and
    scaling it by ATR is what makes it mean the same thing on gold and on
    EURUSD.
    """
    o, hi, lo, c = h4["open"], h4["high"], h4["low"], h4["close"]
    atr = ctx["atr"]
    n = len(c)
    if n < look + 50:
        return []

    hh = np.full(n, np.nan)
    ll = np.full(n, np.nan)
    sw_h = np.lib.stride_tricks.sliding_window_view(hi, look)
    sw_l = np.lib.stride_tricks.sliding_window_view(lo, look)
    hh[look:] = sw_h.max(axis=1)[:n - look]
    ll[look:] = sw_l.min(axis=1)[:n - look]

    spread_h4 = b["spread_pts"][h4["first_i"]] * b["point"]
    slip = SLIP_POINTS * b["point"]
    out: list[dict] = []

    i = look + 50
    while i < n - 2:
        if not np.isfinite(atr[i]) or atr[i] <= 0 or not np.isfinite(hh[i]):
            i += 1
            continue
        broke_up = hi[i] >= hh[i]
        broke_dn = lo[i] <= ll[i]
        if broke_up == broke_dn:
            i += 1
            continue
        level = hh[i] if broke_up else ll[i]

        # the retest: price comes back to the broken level within N candles,
        # and a WIDE candle closes away from it in the break's direction
        hit = None
        for j in range(i + 1, min(i + 1 + retest_within, n - 1)):
            touched = (lo[j] <= level) if broke_up else (hi[j] >= level)
            if not touched:
                continue
            rng = hi[j] - lo[j]
            mid = (hi[j] + lo[j]) / 2.0
            wide = rng >= wide_mult * atr[j]
            closed_right = (c[j] > mid) if broke_up else (c[j] < mid)
            trend_body = (c[j] > o[j]) if broke_up else (c[j] < o[j])
            if wide and closed_right and trend_body:
                hit = j
                break
            # price went straight through the level: the break is dead
            if (c[j] < level) if broke_up else (c[j] > level):
                break
        if hit is None:
            i += 1
            continue

        ei = hit + 1
        if ei >= n - 1:
            break
        entry = o[ei]
        R = atr_stop * atr[hit]
        if R <= 0 or R < MIN_STOP_SPREADS * spread_h4[ei]:
            i = hit + 1
            continue
        stop = (lo[hit] - 0.0) if broke_up else (hi[hit] + 0.0)
        R = abs(entry - stop)
        if R <= 0 or R < MIN_STOP_SPREADS * spread_h4[ei]:
            i = hit + 1
            continue

        extreme = entry
        exit_px, j = None, ei
        while j < n:
            if broke_up:
                extreme = max(extreme, c[j])
                lvl = max(stop, extreme - trail_atr * atr[j])
                if lo[j] <= lvl:
                    exit_px = lvl
                    break
            else:
                extreme = min(extreme, c[j])
                lvl = min(stop, extreme + trail_atr * atr[j])
                if hi[j] >= lvl:
                    exit_px = lvl
                    break
            j += 1
        if exit_px is None:
            exit_px, j = c[n - 1], n - 1
        gross = (exit_px - entry) if broke_up else (entry - exit_px)
        out.append({"t": int(h4["t"][ei]), "symbol": sym,
                    "r": (gross - spread_h4[ei] - slip) / R,
                    "stop_distance": R, "exit_t": int(h4["t"][j]),
                    "dir": "long" if broke_up else "short"})
        i = j + 1
    return out


# -- the session-alternation claim, measured before it is traded ----------

SESSIONS = {"asia": (0, 8), "london": (8, 13), "newyork": (13, 21)}


def session_returns(b: dict) -> list[tuple[int, str, float]]:
    """(day, session, return) for every complete session, UTC clock."""
    t, o, c = b["time"], b["open"], b["close"]
    hour = (t % 86400) // 3600
    day = (t // 86400).astype(np.int64)
    out = []
    for d in np.unique(day):
        dm = day == d
        for name, (a, z) in SESSIONS.items():
            m = dm & (hour >= a) & (hour < z)
            idx = np.flatnonzero(m)
            if len(idx) < 6:
                continue
            out.append((int(d), name, float(c[idx[-1]] / o[idx[0]] - 1.0)))
    return out


def alternation_table(b: dict) -> dict:
    """P(next session is the opposite sign | this session's sign).

    50% is the null. Anything that is going to be tradable has to beat it by
    enough to pay the spread, and the claim is specific about WHICH pairs, so
    each ordered pair is reported separately rather than pooled.
    """
    rows = session_returns(b)
    order = ["asia", "london", "newyork"]
    by_day: dict[int, dict[str, float]] = defaultdict(dict)
    for d, s, r in rows:
        by_day[d][s] = r
    days = sorted(by_day)
    pairs: dict[str, list[int]] = defaultdict(list)
    for k, d in enumerate(days):
        for n, s in enumerate(order):
            if s not in by_day[d]:
                continue
            if n + 1 < len(order):
                nxt, nd = order[n + 1], d
            else:
                if k + 1 >= len(days) or days[k + 1] != d + 1:
                    continue
                nxt, nd = order[0], d + 1
            if nxt not in by_day[nd]:
                continue
            a, bb = by_day[d][s], by_day[nd][nxt]
            if a == 0 or bb == 0:
                continue
            pairs[f"{s}->{nxt}"].append(1 if (a > 0) != (bb > 0) else 0)
    out = {}
    for k, v in pairs.items():
        n = len(v)
        if n < 100:
            continue
        p = float(np.mean(v))
        se = float(np.sqrt(p * (1 - p) / n))
        out[k] = {"n": n, "p_opposite": p * 100,
                  "z_vs_50": (p - 0.5) / se if se > 0 else 0.0}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--split", default="2025-04-01")
    ap.add_argument("--surrogates", type=int, default=24)
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(Path(specs_path).read_text(encoding="utf-8"))
    cut = int(datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc).timestamp())
    names = REAL_DERIV if args.feed == "deriv" else REAL_FN
    markets = [m for m in names
               if (bars_dir / f"{m.replace(' ', '_')}.npz").exists() and m in eng.SPECS]

    book = {}
    for sym in markets:
        b = load(bars_dir, sym)
        h4 = aggregate(b, buckets(b["time"], H4))
        if len(h4["close"]) < 400:
            continue
        ctx = context(h4)
        ctx["close"] = h4["close"]
        book[sym] = (b, h4, ctx)
    print(f"[{args.feed}] REAL markets only: {', '.join(book)}")
    res: dict = {"feed": args.feed, "split": args.split, "markets": list(book)}

    # ---- 1. the session-alternation claim ---------------------------
    print("\n-- 'London bought, so New York sells' - is it even true? --")
    print("   (50% is the null; a tradable claim needs to clear it by a lot)")
    agg: dict[str, list] = defaultdict(list)
    for sym, (b, _, _) in book.items():
        for k, v in alternation_table(b).items():
            agg[k].append((v["n"], v["p_opposite"], v["z_vs_50"], sym))
    res["alternation"] = {}
    for k, rows in sorted(agg.items()):
        tot = sum(r[0] for r in rows)
        wavg = sum(r[0] * r[1] for r in rows) / tot
        best = max(rows, key=lambda r: abs(r[2]))
        res["alternation"][k] = {"n": tot, "p_opposite": wavg,
                                 "best_market": best[3], "best_z": best[2]}
        print(f"   {k:<20s} n={tot:>6,}  opposite {wavg:5.2f}%  "
              f"(strongest single market {best[3]} z={best[2]:+.2f})")

    # ---- 2. break and retest ----------------------------------------
    print("\n-- break and retest, confirmed by a wide 4H candle --")
    grid = list(itertools.product([6, 12, 20], [1.2, 1.6], [2.5, 4.0], [1.0]))
    best = None
    for look, wide, trail, st in grid:
        ins: list[dict] = []
        for sym, (b, h4, ctx) in book.items():
            tt = drop_overlaps(break_retest(b, h4, ctx, sym, look=look,
                                            wide_mult=wide, trail_atr=trail,
                                            atr_stop=st))
            ins += split_at(tt, cut)[0]
        sc = score(ins)
        if sc["thin"]:
            continue
        print(f"   look={look:<3d} wide={wide} trail={trail}  IS n={sc['n']:<5d} "
              f"E={sc['expectancy_r']:+.4f}R t={sc['t_stat']:+5.2f}")
        if best is None or sc["expectancy_r"] > best[0]["expectancy_r"]:
            best = (sc, (look, wide, trail, st))
    if best is not None:
        bs, (look, wide, trail, st) = best
        allt, oos_all = [], []
        for sym, (b, h4, ctx) in book.items():
            tt = drop_overlaps(break_retest(b, h4, ctx, sym, look=look,
                                            wide_mult=wide, trail_atr=trail,
                                            atr_stop=st))
            allt += tt
            oos_all += split_at(tt, cut)[1]
        os_ = score(oos_all)
        res["break_retest"] = {"pick": {"look": look, "wide": wide, "trail": trail},
                               "in_sample": bs, "out_of_sample": os_,
                               "money_oos": money(oos_all, 0.5)}
        m = money(oos_all, 0.5)
        print(f"   PICK look={look} wide={wide} trail={trail} -> OOS n={os_['n']} "
              f"E={os_.get('expectancy_r') or 0:+.4f}R t={os_['t_stat']:+.2f}"
              + (f" | $10k -> ${m['final']:,.0f} ({m['return_pct']:+.1f}%) "
                 f"DD {m['max_dd_pct']:.1f}%" if m else ""))

    # ---- 3. optimise the Donchian trend system ----------------------
    print("\n-- optimising the surviving trend system (real markets only) --")
    grid2 = list(itertools.product([4, 6, 9, 12], [2.0, 2.5, 3.0], [3.0, 4.0, 5.0],
                                   ["none", "vol"]))
    best2 = None
    for ew, stp, tr, f in grid2:
        ins = []
        for sym, (b, h4, ctx) in book.items():
            tt = drop_overlaps(trades_for(b, h4, ctx, sym, entry_win=ew,
                                          atr_stop=stp, trail_atr=tr, filt=f))
            ins += split_at(tt, cut)[0]
        sc = score(ins)
        if sc["thin"]:
            continue
        if best2 is None or sc["expectancy_r"] > best2[0]["expectancy_r"]:
            best2 = (sc, (ew, stp, tr, f))
    bs2, (EW, STP, TR, F) = best2
    sur = []
    for k in range(args.surrogates):
        rnd = []
        for sym, (b, h4, ctx) in book.items():
            tt = drop_overlaps(trades_for(b, h4, ctx, sym, entry_win=EW,
                                          atr_stop=STP, trail_atr=TR, filt=F,
                                          shuffle_seed=9000 + k))
            rnd += split_at(tt, cut)[0]
        sc = score(rnd)
        if not sc["thin"]:
            sur.append(sc["expectancy_r"])
    p95 = float(np.percentile(sur, 95)) if len(sur) >= 8 else None

    oos_all, per = [], {}
    for sym, (b, h4, ctx) in book.items():
        tt = drop_overlaps(trades_for(b, h4, ctx, sym, entry_win=EW, atr_stop=STP,
                                      trail_atr=TR, filt=F))
        _, oos = split_at(tt, cut)
        oos_all += oos
        per[sym] = {"out_of_sample": score(oos), "money_all": money(tt, 0.5)}
    os2 = score(oos_all)
    print(f"   PICK {EW} candles, stop {STP}, trail {TR}, filter '{F}'")
    print(f"   IS  E={bs2['expectancy_r']:+.4f}R n={bs2['n']} t={bs2['t_stat']:+.2f}"
          f"  surrogate p95 {p95:+.4f}R" if p95 is not None else "")
    for rp in (0.25, 0.5, 1.0, 2.0):
        m = money(oos_all, rp)
        if not m:
            continue
        print(f"   OOS risk {rp:>4.2f}%  n={os2['n']:<4d} E={os2['expectancy_r']:+.4f}R "
              f"t={os2['t_stat']:+.2f} | $10,000 -> ${m['final']:>9,.0f} "
              f"(${m['final']-10000:>+9,.0f}, {m['return_pct']:+6.1f}%)  "
              f"DD ${m['max_dd_usd']:>7,.0f} ({m['max_dd_pct']:.1f}%)  "
              f"Sharpe {m['sharpe']:+.2f}")
    res["trend_opt"] = {"pick": {"entry_win": EW, "atr_stop": STP, "trail_atr": TR,
                                 "filter": F},
                        "in_sample": bs2, "surrogate_p95": p95,
                        "out_of_sample": os2,
                        "money": {str(rp): money(oos_all, rp)
                                  for rp in (0.25, 0.5, 1.0, 2.0)},
                        "per_market": per}
    print("\n   per market, out of sample:")
    for sym, r in sorted(per.items(),
                         key=lambda kv: -(kv[1]["out_of_sample"].get("expectancy_r") or -9)):
        o = r["out_of_sample"]
        e = o.get("expectancy_r")
        print(f"     {sym:<16s} n={o['n']:<4d} "
              + ("thin" if e is None else f"{e:+.4f}R t={o['t_stat']:+5.2f}"))

    out = ROOT / "debug" / f"h4_deep_{args.feed}.json"
    out.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
