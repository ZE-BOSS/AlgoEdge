#!/usr/bin/env python
"""
scripts/run_spike_resumption.py

Does the drift resume after a Boom/Crash spike is spent?

THE PATTERN, AS DESCRIBED (2026-09-27, from two Boom 900 M15 screenshots)
-------------------------------------------------------------------------
On BOOM (which spikes UP and drifts DOWN):

  1. an established DOWNWARD drift;
  2. a single M15 bar breaks it with a sharp BUY -- the spike;
  3. the next bar closes bearish;
  4. SELL at that bar's close;
  5. it "always sells below the last spike", typically for 3 M15 bars,
     sometimes 4 or 5.

CRASH is the mirror: upward drift, one sharp SELL spike, one completed bullish
bar, BUY, target above the spike.

WHY THIS IS NOT BoomDriftJump_v1 WITH DIFFERENT NUMBERS
--------------------------------------------------------
The shipped strategy's Setup A enters on a pullback to the fast EMA during an
active drift and carries this line:

    # Do not sell into a fresh up-spike; the mirror of DJA's block.

It REFUSES the setup described above. This one uses the spike as the trigger;
that one treats a fresh spike as a reason to stand aside and takes EMA pullbacks
anywhere in the drift instead -- which is why its entries look arbitrary
relative to where the last spike was. Different hypothesis, so: different
strategy, measured from scratch.

THE FILL MODEL IS THE WHOLE BALLGAME
------------------------------------
A bar backtest fills a stop AT the stop. On Boom and Crash every spike happens
INSIDE a bar by construction, so a stop on the spike side is taken out by a jump
and fills far past it. Measured from ticks, that is worth ~0.3R per stopped
trade, and ignoring it had random Boom shorts booking +0.76R each against -0.03R
on real ticks. This strategy sits RIGHT NEXT TO a spike, which is where the
error is largest, so every number below is priced with the measured lambda from
backend/backtester/fill_model.SPIKE_FILLS. Nothing here is reported without it.

    py -3.12 scripts/run_spike_resumption.py --sweep
    py -3.12 scripts/run_spike_resumption.py --best --walk-forward
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.backtester.fill_model import get_spike_fill  # noqa: E402
from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_app_form_check import resample  # noqa: E402
from scripts.run_published_strategies import FEEDS, load, summarise  # noqa: E402

OUT = ROOT / "data" / "spike_resumption"
M15 = 900
BARS_PER_DAY = 96

# (symbol, spike direction) -- Boom spikes UP and drifts DOWN, Crash the mirror.
MARKETS = [
    ("Boom 300 Index", +1), ("Boom 500 Index", +1),
    ("Boom 900 Index", +1), ("Boom 1000 Index", +1),
    ("Crash 300 Index", -1), ("Crash 500 Index", -1),
    ("Crash 900 Index", -1), ("Crash 1000 Index", -1),
]


def atr(b: dict, n: int = 14) -> np.ndarray:
    h, lo, c = b["high"], b["low"], b["close"]
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) > n:
        cs = np.cumsum(tr)
        out[n:] = (cs[n:] - cs[:-n]) / n
    return out


def ema(x: np.ndarray, span: int) -> np.ndarray:
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def spike_bars(b: dict, side: int, k_atr: float, a: np.ndarray,
               metric: str = "body") -> np.ndarray:
    """Bars whose move IN THE SPIKE DIRECTION is at least `k_atr` x ATR.

    Measured close-to-close rather than on the bar's range: a Boom spike is a
    vertical move that ends the bar far above where it started, while a wide
    range with a small body is ordinary noise and not what the eye picks out on
    the chart.
    """
    o, c, h, lo = b["open"], b["close"], b["high"], b["low"]
    if metric == "range":
        # What the eye picks out on a chart is a tall bar, whether or not it
        # closed at its extreme. Worth testing separately: on a spike that is
        # immediately faded within its own bar, body and range disagree.
        move = (h - o) * side if side > 0 else (o - lo) * side * -1
        move = (h - o) if side > 0 else (o - lo)
    elif metric == "either":
        body = (c - o) * side
        rng = (h - o) if side > 0 else (o - lo)
        move = np.maximum(body, rng)
    else:
        move = (c - o) * side
    return (move >= k_atr * a) & np.isfinite(a) & (a > 0)


def drifting(b: dict, side: int, mode: str, look: int) -> np.ndarray:
    """Is the drift running AGAINST the spike direction (the trade's direction)?

    Three readings of "a clear downward trend", because the description does not
    pin one and the answer should not depend on which I happened to pick:
      ema     fast EMA below slow (for Boom), i.e. the usual regime test
      ret     the last `look` bars' return is negative
      below   the close has spent the last `look` bars under the slow EMA
    """
    c = b["close"]
    n = len(c)
    if mode == "ret":
        out = np.zeros(n, dtype=bool)
        out[look:] = ((c[look:] - c[:-look]) * side) < 0
        return out
    slow = ema(c, look)
    if mode == "below":
        under = ((c - slow) * side) < 0
        out = np.zeros(n, dtype=bool)
        win = min(look, 20)
        for i in range(win, n):
            out[i] = under[i - win:i + 1].all()
        return out
    fast = ema(c, max(3, look // 4))
    return ((fast - slow) * side) < 0


def run_variant(b: dict, sym: str, side: int, *, k_atr: float, trend_mode: str,
                trend_look: int, confirm_bars: int, stop_atr: float, target: str,
                target_rr: float, max_hold: int, slip_points: float,
                placebo_shift: int = 0, spike_metric: str = "body",
                require_below_spike: bool = False, min_bars_since_spike: int = 0,
                trail_atr: float = 0.0) -> list[dict]:
    """One parameter set, on one market, priced with the measured spike lambda.

    `placebo_shift` moves every entry N bars later while leaving the trend and
    spike tests where they were -- the control. If the edge is in the pattern it
    dies when the entry is decoupled from it; if it survives, the "edge" was a
    property of the instrument's drift and not of the setup.
    """
    o, h, lo, c, t = b["open"], b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl_pts = slip_points * b["point"]
    a = atr(b)
    n = len(c)

    spikes = spike_bars(b, side, k_atr, a, spike_metric)
    last_spike = -10_000
    trend = drifting(b, side, trend_mode, trend_look)
    fill = get_spike_fill(sym)
    lam = fill[1] if fill else 0.0
    spike_side = fill[0] if fill else 0

    out: list[dict] = []
    i = trend_look + 20
    while i < n - max_hold - 3:
        if spikes[i]:
            prev_spike, last_spike = last_spike, i
        else:
            prev_spike = last_spike
        if not spikes[i] or not trend[i]:
            i += 1
            continue
        # Spikes that arrive back to back are one event, not two setups.
        if min_bars_since_spike and (i - prev_spike) < min_bars_since_spike:
            i += 1
            continue
        # the spike must be ONE bar: the next bar is not itself a spike
        if spikes[i + 1]:
            i += 1
            continue
        # `confirm_bars` completed bars closing AGAINST the spike
        j = i + 1
        ok = True
        for k in range(confirm_bars):
            if (c[j + k] - o[j + k]) * side >= 0:
                ok = False
                break
        if not ok:
            i += 1
            continue
        # "it must come below the last buy spike" -- the confirming bar has to
        # close back beyond where the spike started, not merely close red.
        if require_below_spike:
            past = (c[j + confirm_bars - 1] < o[i]) if side > 0 else (c[j + confirm_bars - 1] > o[i])
            if not past:
                i += 1
                continue
        entry_i = j + confirm_bars + placebo_shift      # fill at the next bar's open
        if entry_i >= n - 2:
            break

        entry = o[entry_i]
        R = stop_atr * a[i]
        if not np.isfinite(R) or R <= 0:
            i += 1
            continue
        # SELL on Boom (side +1), BUY on Crash (side -1)
        long_ = side < 0
        stop = entry + R if not long_ else entry - R
        if target == "spike":
            tp = lo[i] if not long_ else h[i]           # back past the spike bar
            if (tp - entry) * (1 if long_ else -1) <= 0:
                tp = entry + target_rr * R * (1 if long_ else -1)
        else:
            tp = entry + target_rr * R * (1 if long_ else -1)

        exit_px, exit_reason = None, "MAX_HOLD"
        end = min(entry_i + max_hold, n - 1)
        best = entry
        for k in range(entry_i, end + 1):
            if trail_atr > 0 and k > entry_i:
                # chandelier from the best close so far, ratcheting only
                best = min(best, c[k - 1]) if not long_ else max(best, c[k - 1])
                level = (best + trail_atr * a[i]) if not long_ else (best - trail_atr * a[i])
                stop = min(stop, level) if not long_ else max(stop, level)
            hit_stop = (h[k] >= stop) if not long_ else (lo[k] <= stop)
            hit_tp = (lo[k] <= tp) if not long_ else (h[k] >= tp)
            if hit_stop:
                # A stop on the SPIKE side is taken out by a jump and fills
                # part-way to the bar's extreme -- this is the whole reason the
                # lambda table exists.
                on_spike_side = spike_side == 2 or (spike_side == 1 and not long_) \
                    or (spike_side == -1 and long_)
                if on_spike_side and lam > 0:
                    exit_px = (stop + lam * (h[k] - stop)) if not long_ \
                        else (stop - lam * (stop - lo[k]))
                else:
                    exit_px = stop
                exit_reason = "SL"
                break
            if hit_tp:
                exit_px, exit_reason = tp, "TP"
                break
        if exit_px is None:
            exit_px = c[end]

        gross = (exit_px - entry) if long_ else (entry - exit_px)
        out.append({"t": int(t[entry_i]), "symbol": sym, "bars": end - entry_i,
                    "r": (gross - spread[entry_i] - sl_pts) / R, "stop_distance": R,
                    "exit": exit_reason})
        i = end + 1                                     # one position at a time
    return out


def stats(trades: list[dict], risk: float) -> dict | None:
    if len(trades) < 20:
        return None
    s = summarise(trades, "x")
    r = np.array([x["r"] for x in trades])
    w, l = r[r > 0], r[r < 0]
    rep = eng.report(eng.run_account(sorted(trades, key=lambda x: x["t"]), 10_000.0, risk), "x")
    by_day: dict[int, float] = {}
    for x in trades:
        d = int(x["t"] // 86400)
        by_day[d] = by_day.get(d, 0.0) + x["r"] * risk / 100
    v = np.array(list(by_day.values()))
    sharpe = float(v.mean() / v.std() * np.sqrt(252)) if len(v) > 5 and v.std() else 0.0
    return {
        "n": s["n"], "expectancy_r": s["expectancy_r"], "win_rate": s["win_rate"],
        "t_stat": s["t_stat"], "payoff": float(w.mean() / abs(l.mean())) if len(w) and len(l) else 0.0,
        "sharpe": sharpe, "return_pct": rep["return_pct"], "max_dd_pct": rep["max_dd_pct"],
        "max_dd_usd": rep["max_dd_usd"], "profit_factor": rep["profit_factor"],
        "final": rep["final"], "expectancy_usd": rep["expectancy_usd"],
        "avg_win": rep["avg_win"], "avg_loss": rep["avg_loss"],
        "month_pos_pct": rep["month_pos_pct"], "months": len(rep["months"]),
        "week_pos_pct": rep["week_pos_pct"], "weeks": len(rep["weeks"]),
        "win_streak": rep["longest_win_streak"], "loss_streak": rep["longest_loss_streak"],
        "month_best_streak": rep["month_best_streak"], "month_worst_streak": rep["month_worst_streak"],
        "r_per_month": s["r_per_month"],
    }


HDR = (f"  {'variant':44s} {'N':>5s} {'expR':>7s} {'win%':>6s} {'payoff':>7s} {'t':>6s} "
       f"{'Sh':>6s} {'ret%':>8s} {'DD%':>6s}")


def show(label: str, st: dict | None) -> None:
    if st is None:
        print(f"  {label:44s}     -  (too few trades)")
        return
    print(f"  {label:44s} {st['n']:>5d} {st['expectancy_r']:>+7.3f} {st['win_rate'] * 100:>5.1f}% "
          f"{st['payoff']:>7.2f} {st['t_stat']:>+6.2f} {st['sharpe']:>6.2f} "
          f"{st['return_pct']:>+7.1f}% {st['max_dd_pct']:>5.1f}%")


def load_m15(bars_dir: Path, sym: str, since: int) -> dict | None:
    try:
        b = load(bars_dir, sym)
    except FileNotFoundError:
        return None
    m = b["time"] >= since
    b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
    if len(b["time"]) < 20_000:
        return None
    return resample(b, M15)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-09-01")
    ap.add_argument("--until", default=None, help="walk-forward: stop here")
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--placebo", action="store_true", help="also run the shifted-entry control")
    ap.add_argument("--markets", nargs="*", default=None,
                    help="restrict to these symbols (the 1000s carry five years of "
                         "history; the 900s only start 2024-08)")
    ap.add_argument("--combo", action="store_true",
                    help="the settings the sweep picked, rather than the baseline")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    bars_dir, specs = FEEDS["deriv"]
    eng.SPECS = json.loads(specs.read_text())
    t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc).timestamp()) \
        if args.until else None

    wanted = {m.lower() for m in (args.markets or [])}
    data = {}
    for sym, side in MARKETS:
        if wanted and sym.lower() not in wanted:
            continue
        b = load_m15(bars_dir, sym, t0)
        if b is None:
            print(f"  (no data for {sym})")
            continue
        if t1:
            m = b["time"] < t1
            b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        data[sym] = (b, side)
    if not data:
        print("no data")
        return

    span = f"{args.since} -> {args.until or 'today'}"
    print(f"\nSPIKE RESUMPTION -- Boom/Crash M15, {span}, $10,000 at {args.risk}% risk")
    print("stops priced with the measured spike lambda (fill_model.SPIKE_FILLS)\n")

    base = dict(k_atr=3.0, trend_mode="ema", trend_look=50, confirm_bars=1,
                stop_atr=2.0, target="spike", target_rr=3.0, max_hold=5)
    if args.combo:
        # Chosen by reading the sweep, so it is IN-SAMPLE by construction and
        # only means something on a window the sweep never saw.
        base = {**base, "trend_mode": "below", "stop_atr": 1.0}

    grids = {
        "k_atr": [2.0, 3.0, 5.0],
        "trend_mode": ["ema", "ret", "below"],
        "trend_look": [20, 50, 100],
        "confirm_bars": [1, 2],
        "stop_atr": [1.0, 2.0, 3.0],
        "target": ["spike", "rr"],
        "max_hold": [3, 4, 5, 10],
    }

    def book(params: dict, placebo: int = 0) -> list[dict]:
        out: list[dict] = []
        for sym, (b, side) in data.items():
            out += run_variant(b, sym, side, slip_points=args.slip_points,
                               placebo_shift=placebo, **params)
        return out

    results = {}
    print(HDR)
    print("  " + "-" * (len(HDR) - 2))
    if args.sweep:
        for key, values in grids.items():
            for value in values:
                params = {**base, key: value}
                st = stats(book(params), args.risk)
                label = f"{key}={value}"
                show(label, st)
                results[label] = st
            print()
        # target_rr only binds when the target IS an R multiple; swept against
        # target="spike" it was inert, and three identical rows in the first
        # version of this sweep said "this parameter does not matter" when what
        # they meant was "this parameter was never read".
        for value in (1.0, 2.0, 3.0, 5.0, 8.0):
            params = {**base, "target": "rr", "target_rr": value}
            label = f"target=rr, target_rr={value}"
            st = stats(book(params), args.risk)
            show(label, st)
            results[label] = st
        print()
        # The two best single choices, together -- and then the control for that
        # combination, because a combination picked from a sweep is exactly the
        # thing a control exists to check.
        combo = {**base, "trend_mode": "below", "stop_atr": 1.0}
        st = stats(book(combo), args.risk)
        show("COMBO trend=below, stop=1xATR", st)
        results["combo"] = st
        for shift in (3, 5, 10):
            show(f"  control: entry +{shift} bars", stats(book(combo, placebo=shift), args.risk))
    else:
        st = stats(book(base), args.risk)
        show("baseline " + ", ".join(f"{k}={v}" for k, v in base.items())[:32], st)
        results["baseline"] = st
        print("\n  per market")
        for sym, (b, side) in data.items():
            show("  " + sym, stats(run_variant(b, sym, side, slip_points=args.slip_points,
                                               **base), args.risk))

    if args.placebo:
        print("\n  CONTROL -- the same spikes and the same trend, entry shifted later")
        for shift in (3, 5, 10):
            show(f"entry +{shift} bars", stats(book(base, placebo=shift), args.risk))

    (OUT / f"sweep_{args.since}.json").write_text(json.dumps(
        {"span": span, "base": base, "results": results}, indent=1, default=float))
    print(f"\n-> {OUT / f'sweep_{args.since}.json'}")


if __name__ == "__main__":
    main()
