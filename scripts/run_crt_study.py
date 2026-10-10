#!/usr/bin/env python
"""
scripts/run_crt_study.py

Four marketed 4-hour models, implemented to their stated rules and tested on
this account's own bars.

THE CLAIMS
----------
1. "4H MANIPULATION" (two videos, same model)
     "Wait for price to go lower first, sweeping below the previous four hour
      candle's low, and closing in the opposite direction. Stop loss at the
      low, enter at the new candle. A model which will change your life."
   The second video adds one condition: the candle must also **engulf the
   previous candle's body**. Neither video states a target.

2. "4H CRT, lower-timeframe entry"
     "Price takes the low, you buy; price takes the high, you sell. Go to the
      lower time frame and wait for a valid entry signal, stop loss just below
      that candle low, target the opposing side of the range."
   Claimed: **82% win rate.** The settings screenshot pins the CRT candle to a
   05:00 start on an America/New_York clock, so that is what is tested (and
   the other two documented anchors, 01:00 and 09:00, alongside it).

3. "4H CRT, three-step confluence"
     "Step one, a higher time frame key level. Step two, the four hour CRT.
      Step three, lower time frame confirmations — a 15m or 5m CRT, and the
      15m IFVG and order block. Stop at the swing high, take profit at the 4H
      CRT low."
   Claimed: **11.25 R** on the example trade.

WHAT IS TESTED, AND WHY IT IS TESTED THIS WAY
---------------------------------------------
* **Targets are a range, not a guess.** Only model 2 and 3 state one (the
  opposite side of the range). Model 1 states none, so it is tested at
  1:1, 1:1.5, 1:2, 1:3 and 1:5 as well as the range target. Taking a
  marketer's unstated target as read is how a test confirms whatever was
  wanted.
* **Every confluence is a toggle.** Model 3 stacks four filters on the same
  base signal, so each is applied cumulatively AND alone, and the marginal
  effect of each is reported. "Does this confluence do anything?" is the
  question the stack is making a claim about.
* **Entries are next-bar-open.** "Enter at the close of the candle" is not an
  achievable fill; it is the open of the next bar. This project has measured a
  level entry costing up to 60% of an edge.
* **Stops are checked before targets inside a bar.** When a 5-minute bar
  contains both, the loss is taken. The opposite convention invents an edge.
* **Costs are the bar's own spread plus slippage**, and a trade whose stop is
  tighter than 2x the spread is dropped — the live risk gate refuses those, so
  counting them produces a backtest the platform cannot reproduce.

    python scripts/run_crt_study.py --feed deriv
    python scripts/run_crt_study.py --feed fundednext
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_published_strategies import FEEDS, load  # noqa: E402

NY = ZoneInfo("America/New_York")

# The markets the request named, under each feed's own naming. Deriv calls the
# Nasdaq "US Tech 100"; FundedNext does not carry it at all. XRP is on neither
# feed, which the report says rather than quietly dropping it.
# Named as the SPEC file names them (spaces), not as the bar files do
# (underscores) - `load` converts, the contract specs do not, and a name in
# the wrong form is silently dropped rather than erroring. That quietly cost
# the Nasdaq, Deriv's own S&P and the DAX on the first run of this study.
DERIV = ["XAUUSD", "GBPJPY", "US Tech 100", "US SP 500", "SPX500", "EURUSD",
         "GBPUSD", "USDJPY", "BTCUSD", "XAGUSD", "Germany 40", "US30"]
FUNDEDNEXT = ["XAUUSD", "GBPJPY", "SPX500", "US30", "EURUSD", "GBPUSD",
              "USDJPY", "BTCUSD"]

SLIP_POINTS = 2.0          # on top of the bar's own spread
MIN_STOP_SPREADS = 2.0     # the live gate's floor
TARGETS = [1.0, 1.5, 2.0, 3.0, 5.0]


# -- bar plumbing -----------------------------------------------------------


def buckets(t: np.ndarray, minutes: int) -> np.ndarray:
    """Which `minutes`-wide block each 5-minute bar belongs to (UTC grid)."""
    return (t // (minutes * 60)).astype(np.int64)


def aggregate(b: dict, key: np.ndarray) -> dict:
    """OHLC per block, plus the first and last 5-minute index of each.

    `first_i` is what an entry needs: "the open of the next candle" is the
    open of that candle's FIRST five-minute bar, which is a fill that exists.
    """
    order = np.argsort(key, kind="stable")
    k = key[order]
    starts = np.r_[0, np.flatnonzero(np.diff(k)) + 1]
    ends = np.r_[starts[1:], len(k)]
    first_i = order[starts]
    last_i = order[ends - 1]
    o = b["open"][first_i]
    c = b["close"][last_i]
    hi = np.array([b["high"][order[s:e]].max() for s, e in zip(starts, ends)])
    lo = np.array([b["low"][order[s:e]].min() for s, e in zip(starts, ends)])
    return {"key": k[starts], "open": o, "high": hi, "low": lo, "close": c,
            "first_i": first_i, "last_i": last_i, "t": b["time"][first_i]}


def ny_parts(t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(New York calendar day ordinal, New York hour) for each bar.

    Computed per distinct UTC hour rather than per bar: a five-year 5-minute
    series is 500,000 bars and `datetime.fromtimestamp` is not cheap, but
    there are only ~44,000 distinct hours and the hour is all that is needed.
    """
    hour_key = (t // 3600).astype(np.int64)
    uniq, inv = np.unique(hour_key, return_inverse=True)
    days = np.empty(len(uniq), dtype=np.int64)
    hours = np.empty(len(uniq), dtype=np.int64)
    for n, hk in enumerate(uniq):
        d = datetime.fromtimestamp(int(hk) * 3600, timezone.utc).astimezone(NY)
        days[n] = d.toordinal()
        hours[n] = d.hour
    return days[inv], hours[inv]


# -- model 1: the 4-hour manipulation sweep ---------------------------------


def sweep_trades(b: dict, sym: str, *, engulf: bool, target_r: float | None,
                 max_hold_blocks: int = 6) -> list[dict]:
    """Sweep the previous 4H candle's extreme, close the other way, enter next.

    `engulf=True` is the second video's extra condition — the close must clear
    the previous candle's BODY, not merely reverse. `target_r=None` targets the
    opposite extreme of the signal candle, which is the nearest thing the
    videos imply when they show price running back up.
    """
    h4 = aggregate(b, buckets(b["time"], 240))
    o, hi, lo, c = h4["open"], h4["high"], h4["low"], h4["close"]
    spread_px = b["spread_pts"] * b["point"]
    slip = SLIP_POINTS * b["point"]
    out = []

    for i in range(1, len(c) - 1):
        body_hi = max(o[i - 1], c[i - 1])
        body_lo = min(o[i - 1], c[i - 1])
        bull = lo[i] < lo[i - 1] and c[i] > o[i] and (not engulf or c[i] > body_hi)
        bear = hi[i] > hi[i - 1] and c[i] < o[i] and (not engulf or c[i] < body_lo)
        if bull == bear:                       # neither, or (impossibly) both
            continue

        entry_i = int(h4["first_i"][i + 1])
        entry = b["open"][entry_i]
        stop = lo[i] if bull else hi[i]
        risk = abs(entry - stop)
        if risk <= 0 or risk < MIN_STOP_SPREADS * spread_px[entry_i]:
            continue
        if target_r is None:
            # the far side of the signal candle, which is the nearest thing
            # the videos imply when they show price running back through it
            tgt = hi[i] if bull else lo[i]
            if (tgt <= entry) if bull else (tgt >= entry):
                continue                       # the target is already behind us
        else:
            tgt = entry + target_r * risk if bull else entry - target_r * risk

        stop_i = int(h4["last_i"][min(i + max_hold_blocks, len(c) - 1)])
        gross, exit_i = walk(b, entry_i, stop_i, bull, entry, stop, tgt)
        cost = spread_px[entry_i] + slip
        out.append({"t": int(b["time"][entry_i]), "symbol": sym,
                    "r": (gross - cost) / risk, "stop_distance": risk,
                    "exit_t": int(b["time"][exit_i]),
                    "dir": "long" if bull else "short"})
    return out


def walk(b: dict, i0: int, i1: int, long_: bool, entry: float,
         stop: float, tgt: float) -> tuple[float, int]:
    """(gross points, exit bar index). Stop checked before target within a bar.

    The exit index is not decoration. Signals fire faster than trades close,
    so the raw list holds OVERLAPPING positions — several open at once on the
    same symbol. That inflates both the expectancy and the t-statistic, and
    it is not what a platform with one slot per symbol would do. Knowing when
    each trade ended is what makes the honest version computable.
    """
    h, lo, c = b["high"], b["low"], b["close"]
    for j in range(i0, min(i1, len(c) - 1) + 1):
        if long_:
            if lo[j] <= stop:
                return stop - entry, j
            if h[j] >= tgt:
                return tgt - entry, j
        else:
            if h[j] >= stop:
                return entry - stop, j
            if lo[j] <= tgt:
                return entry - tgt, j
    last = min(i1, len(c) - 1)
    return ((c[last] - entry) if long_ else (entry - c[last])), last


def drop_overlaps(trades: list[dict]) -> list[dict]:
    """One open position per symbol at a time, taken first-come.

    What a live slot actually does. Everything the raw list adds beyond this
    is a position the account could not have held.
    """
    out: list[dict] = []
    free_at: dict[str, int] = {}
    for t in sorted(trades, key=lambda x: x["t"]):
        if t["t"] < free_at.get(t["symbol"], -1):
            continue
        out.append(t)
        free_at[t["symbol"]] = t["exit_t"]
    return out


# -- the CRT range ----------------------------------------------------------


_WINDOW_CACHE: dict = {}


def crt_windows(b: dict, anchor_hour: int) -> list[tuple[int, int, int]]:
    """(crt_start, crt_end, day_end) 5-minute indices for each NY day.

    The CRT candle is the 4-hour block beginning at `anchor_hour` New York
    time — 01:00, 05:00 or 09:00, the three the indicator offers, with 05:00
    the one in the settings screenshot. Built on a real New York clock so it
    survives daylight saving, which a fixed UTC offset would not.
    """
    ck = (id(b), anchor_hour)
    if ck in _WINDOW_CACHE:
        return _WINDOW_CACHE[ck]
    days, hours = ny_parts(b["time"])
    out = []
    for d in np.unique(days):
        sel = np.flatnonzero(days == d)
        if len(sel) < 24:
            continue
        hh = hours[sel]
        inside = sel[(hh >= anchor_hour) & (hh < anchor_hour + 4)]
        after = sel[hh >= anchor_hour + 4]
        if len(inside) < 12 or len(after) < 12:
            continue
        out.append((int(inside[0]), int(inside[-1]), int(after[-1])))
    _WINDOW_CACHE[ck] = out
    return out


def crt_ltf_trades(b: dict, sym: str, *, anchor_hour: int, target_r: float | None,
                   confirm_tf_min: int = 5) -> list[dict]:
    """Model 2. Sweep one side of the CRT range, close back in, trade the other way.

    "A valid entry signal by this kind of closure" is the only description the
    video gives, and the chart shows a candle closing back inside the range
    after taking a level. That is implemented literally: the first confirming
    candle to close back inside after the sweep.
    """
    spread_px = b["spread_pts"] * b["point"]
    slip = SLIP_POINTS * b["point"]
    o, h, lo, c = b["open"], b["high"], b["low"], b["close"]
    step = max(1, confirm_tf_min // 5)
    out = []

    for c0, c1, day_end in crt_windows(b, anchor_hour):
        crt_hi = h[c0:c1 + 1].max()
        crt_lo = lo[c0:c1 + 1].min()
        if crt_hi <= crt_lo:
            continue

        swept = None
        j = c1 + 1
        while j <= day_end - step:
            if swept is None:
                if lo[j] < crt_lo:
                    swept = "low"
                elif h[j] > crt_hi:
                    swept = "high"
                j += 1
                continue
            # a confirming close back inside the range, on the chosen timeframe
            k = min(j + step - 1, day_end)
            close_k = c[k]
            bull = swept == "low"
            back_in = close_k > crt_lo if bull else close_k < crt_hi
            if not back_in:
                j += step
                continue

            entry_i = k + 1
            if entry_i > day_end:
                break
            entry = o[entry_i]
            stop = lo[j:k + 1].min() if bull else h[j:k + 1].max()
            risk = abs(entry - stop)
            if risk <= 0 or risk < MIN_STOP_SPREADS * spread_px[entry_i]:
                break
            if target_r is None:
                tgt = crt_hi if bull else crt_lo
                if (bull and tgt <= entry) or (not bull and tgt >= entry):
                    break
            else:
                tgt = entry + target_r * risk if bull else entry - target_r * risk
            gross, exit_i = walk(b, entry_i, day_end, bull, entry, stop, tgt)
            cost = spread_px[entry_i] + slip
            out.append({"t": int(b["time"][entry_i]), "symbol": sym,
                        "r": (gross - cost) / risk, "stop_distance": risk,
                        "exit_t": int(b["time"][exit_i]),
                        "dir": "long" if bull else "short"})
            break
    return out


# -- model 3: the same range, with the confluence stack --------------------


_LEVEL_CACHE: dict = {}


def week_levels(b: dict) -> tuple[np.ndarray, np.ndarray]:
    """Previous week's high and low, carried forward to every bar.

    ISO weeks on a UTC clock. The level is the PREVIOUS week's, so it is known
    before any bar of the current week — the point of a key level is that you
    drew it in advance.
    """
    if id(b) in _LEVEL_CACHE:
        return _LEVEL_CACHE[id(b)]
    t = b["time"]
    wk = ((t + 345600) // 604800).astype(np.int64)    # Monday-aligned
    hi_of, lo_of = {}, {}
    for w in np.unique(wk):
        m = wk == w
        hi_of[int(w)] = float(b["high"][m].max())
        lo_of[int(w)] = float(b["low"][m].min())
    prev_hi = np.array([hi_of.get(int(w) - 1, np.nan) for w in wk])
    prev_lo = np.array([lo_of.get(int(w) - 1, np.nan) for w in wk])
    _LEVEL_CACHE[id(b)] = (prev_hi, prev_lo)
    return prev_hi, prev_lo


def m15_crt_ok(b: dict, sweep_i: int, entry_i: int, bull: bool) -> bool:
    """A 15-minute purge-and-reclaim between the sweep and the entry."""
    h, lo, c = b["high"], b["low"], b["close"]
    a = max(0, sweep_i - 3)
    for s in range(a, entry_i - 3, 3):
        prev_hi = h[s - 3:s].max() if s >= 3 else np.inf
        prev_lo = lo[s - 3:s].min() if s >= 3 else -np.inf
        cl = c[min(s + 2, entry_i - 1)]
        if bull and lo[s:s + 3].min() < prev_lo and cl > prev_lo:
            return True
        if not bull and h[s:s + 3].max() > prev_hi and cl < prev_hi:
            return True
    return False


def m15_fvg_ok(b: dict, sweep_i: int, entry_i: int, bull: bool) -> bool:
    """A 15-minute fair value gap in the trade's direction, before entry.

    Three consecutive 15m candles where candle 1 and candle 3 do not overlap.
    The "inverted" part of IFVG is not separable from this at bar resolution
    without inventing a rule the video did not state, so this is the gap
    itself — a generous reading, which is the right way to test a filter you
    suspect does nothing.
    """
    h, lo = b["high"], b["low"]
    for s in range(max(0, sweep_i - 6), entry_i - 9, 3):
        a_hi, a_lo = h[s:s + 3].max(), lo[s:s + 3].min()
        c_hi, c_lo = h[s + 6:s + 9].max(), lo[s + 6:s + 9].min()
        if bull and c_lo > a_hi:
            return True
        if not bull and c_hi < a_lo:
            return True
    return False


def m15_ob_ok(b: dict, sweep_i: int, entry_i: int, bull: bool) -> bool:
    """The last opposing 15m candle before the move that broke structure."""
    o, c = b["open"], b["close"]
    for s in range(entry_i - 3, max(0, sweep_i - 6), -3):
        body_up = c[min(s + 2, len(c) - 1)] > o[s]
        if bull and not body_up:
            return True
        if not bull and body_up:
            return True
    return False


def crt_confluence_trades(b: dict, sym: str, *, anchor_hour: int,
                          target_r: float | None, filters: set[str],
                          key_atr_mult: float = 0.5) -> list[dict]:
    """Model 3: model 2's range, gated by whichever confluences are enabled."""
    spread_px = b["spread_pts"] * b["point"]
    slip = SLIP_POINTS * b["point"]
    o, h, lo, c = b["open"], b["high"], b["low"], b["close"]
    pw_hi, pw_lo = week_levels(b)
    # a day's typical range, for "price tapped into the key level"
    atr = rolling_range(b, 288)
    out = []

    for c0, c1, day_end in crt_windows(b, anchor_hour):
        crt_hi = h[c0:c1 + 1].max()
        crt_lo = lo[c0:c1 + 1].min()
        if crt_hi <= crt_lo:
            continue
        swept, sweep_i = None, None
        j = c1 + 1
        while j <= day_end - 1:
            if swept is None:
                if lo[j] < crt_lo:
                    swept, sweep_i = "low", j
                elif h[j] > crt_hi:
                    swept, sweep_i = "high", j
                j += 1
                continue
            bull = swept == "low"
            if (c[j] > crt_lo) if bull else (c[j] < crt_hi):
                entry_i = j + 1
                if entry_i > day_end:
                    break
                tol = key_atr_mult * atr[sweep_i]
                if "key_level" in filters:
                    lvl = pw_lo[sweep_i] if bull else pw_hi[sweep_i]
                    if not np.isfinite(lvl) or abs(lo[sweep_i] - lvl
                                                   if bull else h[sweep_i] - lvl) > tol:
                        break
                if "m15_crt" in filters and not m15_crt_ok(b, sweep_i, entry_i, bull):
                    break
                if "m15_fvg" in filters and not m15_fvg_ok(b, sweep_i, entry_i, bull):
                    break
                if "m15_ob" in filters and not m15_ob_ok(b, sweep_i, entry_i, bull):
                    break

                entry = o[entry_i]
                stop = lo[sweep_i:entry_i].min() if bull else h[sweep_i:entry_i].max()
                risk = abs(entry - stop)
                if risk <= 0 or risk < MIN_STOP_SPREADS * spread_px[entry_i]:
                    break
                if target_r is None:
                    tgt = crt_hi if bull else crt_lo
                    if (bull and tgt <= entry) or (not bull and tgt >= entry):
                        break
                else:
                    tgt = entry + target_r * risk if bull else entry - target_r * risk
                gross, exit_i = walk(b, entry_i, day_end, bull, entry, stop, tgt)
                cost = spread_px[entry_i] + slip
                out.append({"t": int(b["time"][entry_i]), "symbol": sym,
                            "r": (gross - cost) / risk, "stop_distance": risk,
                            "exit_t": int(b["time"][exit_i]),
                            "dir": "long" if bull else "short"})
                break
            j += 1
    return out


def rolling_range(b: dict, win: int) -> np.ndarray:
    """A crude daily range, used only as a tolerance for "tapped the level"."""
    h, lo = b["high"], b["low"]
    n = len(h)
    out = np.full(n, np.nan)
    if n <= win:
        return np.nan_to_num(out, nan=float(np.ptp(h)) if n else 1.0)
    hh = np.lib.stride_tricks.sliding_window_view(h, win).max(axis=1)
    ll = np.lib.stride_tricks.sliding_window_view(lo, win).min(axis=1)
    out[win - 1:] = hh - ll
    out[:win - 1] = out[win - 1]
    return out


# -- statistics -------------------------------------------------------------


def sharpe_sortino(rows: list[dict], start: float) -> tuple[float, float]:
    """Annualised, from MONTHLY returns.

    Per-trade Sharpe is the number that flatters a strategy taking hundreds of
    tiny trades, and it is not what anyone means by the word. Monthly, on the
    actual equity curve, is comparable to how a fund quotes it.
    """
    if len(rows) < 10:
        return 0.0, 0.0
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_month[datetime.fromtimestamp(r["t"], timezone.utc).strftime("%Y-%m")].append(r)
    rets = []
    for _, v in sorted(by_month.items()):
        opening = v[0]["balance"] - v[0]["pnl"]
        if opening > 0:
            rets.append(sum(x["pnl"] for x in v) / opening)
    a = np.array(rets)
    if len(a) < 3 or a.std(ddof=1) == 0:
        return 0.0, 0.0
    sharpe = float(a.mean() / a.std(ddof=1) * np.sqrt(12))
    down = a[a < 0]
    dstd = float(down.std(ddof=1)) if len(down) > 1 else 0.0
    sortino = float(a.mean() / dstd * np.sqrt(12)) if dstd > 0 else float("inf")
    return sharpe, sortino


def stats(trades: list[dict], label: str, risk_pct: float = 1.0) -> dict:
    if len(trades) < 20:
        return {"label": label, "n": len(trades), "thin": True}
    r = np.array([t["r"] for t in trades])
    acct = eng.run_account(trades, start_balance=10_000.0, risk_pct=risk_pct,
                           compounding=True)
    rep = eng.report(acct, label)
    sharpe, sortino = sharpe_sortino(acct["rows"], 10_000.0)
    wins, losses = r[r > 0], r[r < 0]
    span_days = (max(t["t"] for t in trades) - min(t["t"] for t in trades)) / 86400 or 1
    return {
        "label": label, "n": int(len(r)),
        "win_rate": float((r > 0).mean() * 100),
        "expectancy_r": float(r.mean()),
        "total_r": float(r.sum()),
        "t_stat": float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if r.std() else 0.0,
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "payoff": float(wins.mean() / abs(losses.mean())) if len(losses) and len(wins) else 0.0,
        "profit_factor": rep.get("profit_factor", 0.0),
        "final": rep.get("final", 10_000.0),
        "return_pct": rep.get("return_pct", 0.0),
        "max_dd_pct": rep.get("max_dd_pct", 0.0),
        "max_dd_usd": rep.get("max_dd_usd", 0.0),
        "sharpe": sharpe, "sortino": sortino,
        "trades_per_month": len(r) / (span_days / 30.4),
        "tradable": rep.get("trades", 0),
        "years": span_days / 365.25,
    }


# -- the run ----------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(Path(specs_path).read_text(encoding="utf-8"))
    markets = DERIV if args.feed == "deriv" else FUNDEDNEXT
    missing = [m for m in markets
               if not (bars_dir / f"{m.replace(' ', '_')}.npz").exists()
               or m not in eng.SPECS]
    if missing:
        print(f"[{args.feed}] NOT AVAILABLE on this feed: {', '.join(missing)}")
    markets = [m for m in markets if m not in missing]

    data = {m: load(bars_dir, m) for m in markets}
    print(f"[{args.feed}] {len(markets)} markets: {', '.join(markets)}")

    results: dict = {"feed": args.feed, "markets": markets, "runs": {}}

    def record(name: str, per_symbol: dict[str, list[dict]]) -> None:
        pooled = [t for v in per_symbol.values() for t in v]
        solo = drop_overlaps(pooled)
        results["runs"][name] = {
            "pooled": stats(solo, name),
            "pooled_overlapping": stats(pooled, name + " [overlapping]"),
            "per_symbol": {s: stats(drop_overlaps(v), f"{name} {s}")
                           for s, v in per_symbol.items()},
        }
        p = results["runs"][name]["pooled"]
        if p.get("thin"):
            print(f"  {name:<46s} only {p['n']} trades")
        else:
            print(f"  {name:<46s} n={p['n']:>5d} win={p['win_rate']:5.1f}% "
                  f"E={p['expectancy_r']:+.4f}R t={p['t_stat']:+5.2f} "
                  f"PF={p['profit_factor']:.2f} ret={p['return_pct']:+8.1f}% "
                  f"DD={p['max_dd_pct']:.1f}%")

    # ---- model 1, both videos, every target --------------------------------
    print("\n-- model 1: 4H manipulation sweep --")
    for engulf in (False, True):
        tag = "v2-engulf" if engulf else "v1-plain"
        for tr in [None, *TARGETS]:
            t_name = "range" if tr is None else f"1:{tr:g}"
            record(f"sweep {tag} target={t_name}",
                   {s: sweep_trades(b, s, engulf=engulf, target_r=tr)
                    for s, b in data.items()})

    # ---- model 2, three anchors, two confirm timeframes --------------------
    print("\n-- model 2: 4H CRT with a lower-timeframe entry --")
    for anchor in (1, 5, 9):
        for tf in (5, 15):
            for tr in [None, 2.0, 3.0]:
                t_name = "range" if tr is None else f"1:{tr:g}"
                record(f"crt-ltf anchor={anchor:02d}NY confirm={tf}m target={t_name}",
                       {s: crt_ltf_trades(b, s, anchor_hour=anchor, target_r=tr,
                                          confirm_tf_min=tf)
                        for s, b in data.items()})

    # ---- model 3, the confluence stack, one filter at a time ---------------
    print("\n-- model 3: the three-step confluence stack (anchor 05:00 NY) --")
    stack = ["key_level", "m15_crt", "m15_fvg", "m15_ob"]
    cumulative: list[str] = []
    for f in [None, *stack]:
        if f:
            cumulative.append(f)
        name = "base (no filters)" if not cumulative else "+".join(cumulative)
        record(f"crt-conf {name} target=range",
               {s: crt_confluence_trades(b, s, anchor_hour=5, target_r=None,
                                         filters=set(cumulative))
                for s, b in data.items()})
    for f in stack:                      # each filter alone, to see its sign
        record(f"crt-conf ONLY {f} target=range",
               {s: crt_confluence_trades(b, s, anchor_hour=5, target_r=None,
                                         filters={f})
                for s, b in data.items()})

    out = Path(args.out or ROOT / "debug" / f"crt_study_{args.feed}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
