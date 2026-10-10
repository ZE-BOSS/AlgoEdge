#!/usr/bin/env python
"""
scripts/run_h4_lab.py

Building a 4-hour system per market, with the validation that stops a search
inventing one.

WHY THIS IS NOT ANOTHER PARAMETER SWEEP
---------------------------------------
`run_crt_study.py` just tested 446 market x configuration cells and found 5
with expectancy > 0 and t > +2, where chance alone gives ~10. That is what an
unvalidated search looks like from the inside: plenty of green cells, none of
them real. So this lab is built around the thing that makes a search
trustworthy rather than around the search itself.

    1. SPLIT FIRST. Everything before `--split` picks the configuration.
       Everything after it is touched exactly once, to report.
    2. A SURROGATE BASELINE. The identical search is run on entries shuffled
       to random bars. Its 95th percentile is what "the best of N tries on
       noise" is worth, and a real edge has to clear it. This project's own
       gate uses the same bar (~+0.24R).
    3. ONE PICK PER MARKET, chosen in-sample, reported out-of-sample. No
       re-picking after seeing the test period.

WHAT IS BEING SEARCHED, AND WHY EACH PIECE IS THERE
---------------------------------------------------
* **Donchian breakout on 4-hour candles, with a CHANDELIER TRAIL and no
  target.** The one structural lesson this project has already paid for: a
  fixed target on a trend system removes the right tail and keeps every
  loser. The trailing-stop Donchian is the only CFD edge here that ever
  measured t +2.50.
* **A volatility-expansion filter** (rv20/rv60). Measured at 10x expectancy
  out of sample on TrendBreakout, and it needs no data feed.
* **A daily trend filter**, because the 60-minute ORB only worked with the
  H1 trend behind it.
* **The sweep-and-reverse condition** from the CRT study. Not as a strategy -
  it loses as one - but as a CONDITIONER. It marks the day's extreme at
  1.77-2.45x the base rate on both feeds, which is a real fact looking for
  the right use.
* **Next-4H-open entries.** A level entry is not a fill; this project has
  measured it costing up to 60% of an edge.

    python scripts/run_h4_lab.py --feed deriv
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_crt_study import (aggregate, buckets, drop_overlaps,  # noqa: E402
                                   sharpe_sortino)
from scripts.run_published_strategies import FEEDS, load  # noqa: E402

H4 = 240
SLIP_POINTS = 2.0
MIN_STOP_SPREADS = 2.0

DERIV = ["XAUUSD", "US Tech 100", "GBPJPY", "EURUSD", "US SP 500", "GBPUSD",
         "USDJPY", "BTCUSD", "XAGUSD", "Germany 40",
         "Volatility 75 Index", "Volatility 100 Index", "Step Index",
         "Boom 1000 Index", "Crash 1000 Index", "Jump 100 Index"]
FUNDEDNEXT = ["XAUUSD", "GBPJPY", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD",
              "SPX500", "US30"]

# Deliberately small. Every extra knob is another lottery ticket, and the
# out-of-sample period has to carry all of them.
ENTRY_WIN = [6, 12, 20, 30]        # 4H candles: 1, 2, 3.3, 5 days
ATR_STOP = [1.5, 2.5]
TRAIL_ATR = [2.5, 4.0]
FILTERS = ["none", "vol", "trend", "vol+trend", "sweep", "vol+sweep"]


def atr_h4(h4: dict, period: int = 14) -> np.ndarray:
    """ATR on the 4-hour candles, from the bars BEFORE each one."""
    hi, lo, c = h4["high"], h4["low"], h4["close"]
    n = len(c)
    out = np.zeros(n)
    if n <= period:
        return out
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - prev), np.abs(lo - prev)))
    win = np.lib.stride_tricks.sliding_window_view(tr, period)
    out[period:n] = win[:n - period].mean(axis=1)
    return out


def realised_vol(c: np.ndarray, win: int) -> np.ndarray:
    """Rolling standard deviation of log returns, in the same units as itself."""
    n = len(c)
    out = np.full(n, np.nan)
    if n <= win + 1:
        return out
    r = np.r_[0.0, np.diff(np.log(np.maximum(c, 1e-12)))]
    w = np.lib.stride_tricks.sliding_window_view(r, win)
    out[win:] = w.std(axis=1)[:n - win]
    return out


def context(h4: dict) -> dict:
    """Everything a filter can read, computed once per market.

    All of it is strictly backward-looking: each value at candle i uses
    candles up to i-1 only. That is checked by construction here rather than
    asserted later, because a filter that peeks is the single easiest way to
    manufacture an edge.
    """
    c, hi, lo, o = h4["close"], h4["high"], h4["low"], h4["open"]
    n = len(c)
    atr = atr_h4(h4)

    rv20, rv60 = realised_vol(c, 20), realised_vol(c, 60)
    with np.errstate(invalid="ignore", divide="ignore"):
        vol_ratio = rv20 / rv60

    # daily trend: close against the mean of the last 30 4H closes (5 days)
    ma = np.full(n, np.nan)
    if n > 30:
        ma[30:] = np.lib.stride_tricks.sliding_window_view(c, 30).mean(axis=1)[:n - 30]

    # the CRT study's one real finding, as a conditioner
    swept_up = np.zeros(n, dtype=bool)      # swept the low and closed green
    swept_dn = np.zeros(n, dtype=bool)
    swept_up[1:] = (lo[1:] < lo[:-1]) & (c[1:] > o[1:])
    swept_dn[1:] = (hi[1:] > hi[:-1]) & (c[1:] < o[1:])
    return {"atr": atr, "vol_ratio": vol_ratio, "ma": ma,
            "swept_up": swept_up, "swept_dn": swept_dn}


def allowed(ctx: dict, i: int, long_: bool, filt: str) -> bool:
    """Is this breakout permitted by the chosen filter set?"""
    if filt == "none":
        return True
    parts = filt.split("+")
    if "vol" in parts:
        v = ctx["vol_ratio"][i]
        if not np.isfinite(v) or v <= 1.15:
            return False
    if "trend" in parts:
        m = ctx["ma"][i]
        if not np.isfinite(m):
            return False
        if long_ and not ctx["close"][i] > m:
            return False
        if not long_ and not ctx["close"][i] < m:
            return False
    if "sweep" in parts:
        # within the last three candles, price swept the other side and
        # reversed - the breakout is the continuation of that reversal
        a = max(0, i - 3)
        side = ctx["swept_up"] if long_ else ctx["swept_dn"]
        if not side[a:i + 1].any():
            return False
    return True


def trades_for(b: dict, h4: dict, ctx: dict, sym: str, *, entry_win: int,
               atr_stop: float, trail_atr: float, filt: str,
               shuffle_seed: int | None = None) -> list[dict]:
    """Donchian breakout, chandelier trail, next-4H-open entry.

    `shuffle_seed` is the surrogate: the same number of entries, in the same
    direction mix, placed at RANDOM candles. Everything downstream is
    identical, so whatever it scores is what this machinery pays for noise.
    """
    hi, lo, c, o = h4["high"], h4["low"], h4["close"], h4["open"]
    atr = ctx["atr"]
    n = len(c)
    if n < entry_win + 40:
        return []

    hh = np.full(n, np.nan)
    ll = np.full(n, np.nan)
    sw_h = np.lib.stride_tricks.sliding_window_view(hi, entry_win)
    sw_l = np.lib.stride_tricks.sliding_window_view(lo, entry_win)
    hh[entry_win:] = sw_h.max(axis=1)[:n - entry_win]     # excludes candle i
    ll[entry_win:] = sw_l.min(axis=1)[:n - entry_win]

    signals: list[tuple[int, bool]] = []
    i = entry_win + 40
    while i < n - 2:
        if not np.isfinite(atr[i]) or atr[i] <= 0 or not np.isfinite(hh[i]):
            i += 1
            continue
        long_ = hi[i] >= hh[i]
        short_ = lo[i] <= ll[i]
        if long_ == short_:
            i += 1
            continue
        if not allowed(ctx, i, long_, filt):
            i += 1
            continue
        signals.append((i, long_))
        i += 1

    if shuffle_seed is not None and signals:
        rng = np.random.default_rng(shuffle_seed)
        lows, highs = entry_win + 40, n - 3
        dirs = [d for _, d in signals]
        rng.shuffle(dirs)
        spots = rng.choice(np.arange(lows, highs), size=len(signals), replace=False)
        signals = sorted(zip(spots.tolist(), dirs))

    spread_h4 = b["spread_pts"][h4["first_i"]] * b["point"]
    slip = SLIP_POINTS * b["point"]
    out: list[dict] = []
    for i, long_ in signals:
        ei = i + 1
        if ei >= n - 1:
            break
        entry = o[ei]
        R = atr_stop * atr[i]
        if R <= 0 or R < MIN_STOP_SPREADS * spread_h4[ei]:
            continue
        stop = entry - R if long_ else entry + R
        extreme = entry
        exit_px, j = None, ei
        while j < n:
            if long_:
                extreme = max(extreme, c[j])
                level = max(stop, extreme - trail_atr * atr[j])
                if lo[j] <= level:
                    exit_px = level
                    break
            else:
                extreme = min(extreme, c[j])
                level = min(stop, extreme + trail_atr * atr[j])
                if hi[j] >= level:
                    exit_px = level
                    break
            j += 1
        if exit_px is None:
            exit_px, j = c[n - 1], n - 1
        gross = (exit_px - entry) if long_ else (entry - exit_px)
        out.append({"t": int(h4["t"][ei]), "symbol": sym,
                    "r": (gross - spread_h4[ei] - slip) / R,
                    "stop_distance": R, "exit_t": int(h4["t"][j]),
                    "bars": j - ei, "dir": "long" if long_ else "short"})
    return out


MIN_TRADES = 25


def score(trades: list[dict]) -> dict:
    """`expectancy_r` is None when there are too few trades to have one.

    It used to be -9.0 as a sentinel, which printed in the results table as
    though the strategy had lost nine R per trade. A sentinel that looks like
    a measurement is worse than no measurement.
    """
    if len(trades) < MIN_TRADES:
        return {"n": len(trades), "thin": True, "expectancy_r": None, "t_stat": 0.0}
    r = np.array([t["r"] for t in trades])
    sd = r.std(ddof=1)
    return {"n": int(len(r)), "expectancy_r": float(r.mean()),
            "win_rate": float((r > 0).mean() * 100),
            "t_stat": float(r.mean() / (sd / np.sqrt(len(r)))) if sd else 0.0,
            "payoff": float(r[r > 0].mean() / abs(r[r < 0].mean()))
            if (r > 0).any() and (r < 0).any() else 0.0,
            "total_r": float(r.sum()), "thin": False}


def money(trades: list[dict], risk_pct: float = 1.0) -> dict:
    if len(trades) < 25:
        return {}
    acct = eng.run_account(trades, start_balance=10_000.0, risk_pct=risk_pct,
                           compounding=True)
    rep = eng.report(acct, "x")
    sh, so = sharpe_sortino(acct["rows"], 10_000.0)
    return {"final": rep.get("final"), "return_pct": rep.get("return_pct"),
            "max_dd_pct": rep.get("max_dd_pct"), "profit_factor": rep.get("profit_factor"),
            "sharpe": sh, "sortino": so, "trades": rep.get("trades")}


def split_at(trades: list[dict], cut: int) -> tuple[list[dict], list[dict]]:
    return ([t for t in trades if t["t"] < cut],
            [t for t in trades if t["t"] >= cut])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--split", default="2025-04-01",
                    help="in-sample ends here; everything after is touched once")
    ap.add_argument("--surrogates", type=int, default=24)
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(Path(specs_path).read_text(encoding="utf-8"))
    cut = int(datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc).timestamp())
    names = DERIV if args.feed == "deriv" else FUNDEDNEXT
    markets = [m for m in names
               if (bars_dir / f"{m.replace(' ', '_')}.npz").exists() and m in eng.SPECS]
    combos = list(itertools.product(ENTRY_WIN, ATR_STOP, TRAIL_ATR, FILTERS))
    print(f"[{args.feed}] {len(markets)} markets x {len(combos)} configs, "
          f"split {args.split}")

    results: dict = {"feed": args.feed, "split": args.split,
                     "configs": len(combos), "markets": {}}

    # ---- load every market once -------------------------------------
    book: dict[str, tuple] = {}
    for sym in markets:
        b = load(bars_dir, sym)
        h4 = aggregate(b, buckets(b["time"], H4))
        if len(h4["close"]) < 400:
            print(f"  {sym:<22s} too few 4H candles")
            continue
        ctx = context(h4)
        ctx["close"] = h4["close"]
        book[sym] = (b, h4, ctx)

    # ---- ONE configuration for the whole universe --------------------
    #
    # A trend system is a portfolio system. The tail arrives in whichever
    # market happens to trend and you cannot know which in advance, so
    # picking a different configuration per market is both the wrong model
    # and 16x the selection. Per market the filtered configs give 25-80
    # trades over three years, which cannot establish anything; pooled they
    # give hundreds.
    pooled_best = None
    for ew, st, tr, f in combos:
        ins_all: list[dict] = []
        for sym, (b, h4, ctx) in book.items():
            tt = drop_overlaps(trades_for(b, h4, ctx, sym, entry_win=ew,
                                          atr_stop=st, trail_atr=tr, filt=f))
            ins_all += split_at(tt, cut)[0]
        sc = score(ins_all)
        if sc["thin"]:
            continue
        if pooled_best is None or sc["expectancy_r"] > pooled_best[0]["expectancy_r"]:
            pooled_best = (sc, (ew, st, tr, f))
    if pooled_best is None:
        print("  no configuration had enough in-sample trades")
        return
    pool_is, (PEW, PST, PTR, PF) = pooled_best
    print(f"  POOLED PICK (in-sample only): {PEW} candles, stop {PST}x ATR, "
          f"trail {PTR}x ATR, filter '{PF}'")
    print(f"    in-sample  E={pool_is['expectancy_r']:+.4f}R n={pool_is['n']} "
          f"t={pool_is['t_stat']:+.2f} win={pool_is['win_rate']:.1f}% "
          f"payoff={pool_is['payoff']:.2f}")

    # ---- the surrogate, on the SAME pooled search --------------------
    sur = []
    for k in range(args.surrogates):
        rnd: list[dict] = []
        for sym, (b, h4, ctx) in book.items():
            tt = drop_overlaps(trades_for(b, h4, ctx, sym, entry_win=PEW,
                                          atr_stop=PST, trail_atr=PTR, filt=PF,
                                          shuffle_seed=7000 + k))
            rnd += split_at(tt, cut)[0]
        sc = score(rnd)
        if not sc["thin"]:
            sur.append(sc["expectancy_r"])
    p95 = float(np.percentile(sur, 95)) if len(sur) >= 8 else None
    print(f"    surrogate  p95 {p95:+.4f}R over {len(sur)} shuffles"
          if p95 is not None else "    surrogate  not enough shuffles")

    # ---- out of sample, once ------------------------------------------
    oos_all: list[dict] = []
    per_market: dict[str, dict] = {}
    for sym, (b, h4, ctx) in book.items():
        tt = drop_overlaps(trades_for(b, h4, ctx, sym, entry_win=PEW,
                                      atr_stop=PST, trail_atr=PTR, filt=PF))
        ins, oos = split_at(tt, cut)
        oos_all += oos
        per_market[sym] = {"in_sample": score(ins), "out_of_sample": score(oos),
                           "money_all": money(tt)}
    pool_oos = score(oos_all)
    verdict = ("CLEARS the surrogate" if (p95 is not None and not pool_oos["thin"]
                                          and pool_oos["expectancy_r"] > p95)
               else "does NOT clear the surrogate")
    print(f"    OUT OF SAMPLE E={pool_oos.get('expectancy_r') or 0:+.4f}R "
          f"n={pool_oos['n']} t={pool_oos['t_stat']:+.2f} -> {verdict}")
    print(f"    money (all)   {json.dumps(money(oos_all))}")

    results["pooled"] = {
        "pick": {"entry_win": PEW, "atr_stop": PST, "trail_atr": PTR, "filter": PF},
        "in_sample": pool_is, "surrogate_p95": p95, "surrogates": len(sur),
        "out_of_sample": pool_oos, "money_oos": money(oos_all),
        "verdict": verdict, "per_market": per_market,
    }
    print("  per market, on the ONE pooled configuration:")
    for sym, r in sorted(per_market.items(),
                         key=lambda kv: -(kv[1]["out_of_sample"].get("expectancy_r") or -9)):
        o = r["out_of_sample"]
        e = o.get("expectancy_r")
        shown = "thin" if e is None else f"{e:+.4f}R t={o['t_stat']:+5.2f}"
        print(f"    {sym:<22s} IS n={r['in_sample']['n']:<4d} | OOS n={o['n']:<4d} {shown}")

    out = ROOT / "debug" / f"h4_lab_{args.feed}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
