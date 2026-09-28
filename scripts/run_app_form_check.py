#!/usr/bin/env python
"""
scripts/run_app_form_check.py

Measuring the three recommended strategies IN THE FORM THE APP CAN ACTUALLY
TRADE THEM, before shipping them.

WHY THIS SCRIPT EXISTS
----------------------
The research forms took liberties the live engine cannot:

  1. `donchian_trend` entered AT the 20-day channel level the moment price
     touched it — a resting stop order filled intrabar. The app fills at the
     NEXT BAR'S OPEN after a signal (engine._create_position), so the app form
     must be "the bar CLOSES beyond the channel, fill at the next open".
  2. `h5_overnight` had NO STOP. `R = 0.5 x daily ATR` was only a normaliser for
     reporting. The app always places a real stop, which on a close-to-open hold
     will sometimes be hit. That changes the distribution, so it has to be
     re-measured with the stop live.
  3. Both used a session grouped at a FIXED minute-of-day found from the volume
     profile, which cannot follow DST — so for half of each year the "session
     open" was an hour out. The app resolves the New York open through pytz on
     true-UTC bar times, which is right all year. This script calibrates the
     broker's UTC offset from the data and does the same.
  4. The research trail exited AT the trail level intrabar. The app does too
     (the level is a real MT5 stop), but the level is recomputed from a bar's
     close and — in the backtester — applied to that same bar. That is one bar
     earlier than live, and it can only TIGHTEN a stop, so it is a conservative
     bias. Reported here both ways so the size of it is known, not assumed.

Nothing here is a new hypothesis. It is the same three rules, costed the same
way, measured through the app's own entry and exit mechanics.

    py -3.12 scripts/run_app_form_check.py --feed deriv
    py -3.12 scripts/run_app_form_check.py --feed fundednext --since 2025-06-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_claimed_strategy import claimed, ema  # noqa: E402
from scripts.run_edge_screen2 import atr_daily, h5_overnight  # noqa: E402
from scripts.run_published_strategies import FEEDS, load, summarise  # noqa: E402
from scripts.run_trend_system import donchian_trend  # noqa: E402

OUT = ROOT / "data" / "zone_study"

TREND_MARKETS = ["US Tech 100", "US SP 500", "Germany 40", "US30", "SPX500",
                 "XAUUSD", "XAGUSD", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY"]
# the marketed opening rule is only positive on indices, gold and BTC (run_claimed_strategy)
DRIVE_MARKETS = ["US Tech 100", "US SP 500", "Germany 40", "US30", "SPX500", "XAUUSD", "BTCUSD"]
NIGHT_MARKETS = ["US Tech 100", "US SP 500", "Germany 40", "US30", "SPX500"]

NY = pytz.timezone("America/New_York")
CASH_MINUTES = 390


# ─────────────────────────────────────────────────────────────────────────────
# the broker clock, and the New York session in it
# ─────────────────────────────────────────────────────────────────────────────
def broker_utc_offset_hours(b: dict) -> int:
    """The broker's UTC offset, chosen as the one that puts the DST-aware New
    York cash open where the volume actually is.

    backend/mt5/data_fetcher.detect_server_utc_offset_hours does this live by
    asking MT5 for a tick; the cached bars have no tick, so it is calibrated
    from the volume profile instead. Both answer the same question: how many
    hours must be subtracted from a stored timestamp to get true UTC.
    """
    t, vol = b["time"], b["vol"]
    best, best_score = 0, -1.0
    for off in range(-1, 5):
        true_utc = t - off * 3600
        # The NY open is 13:30 UTC in summer and 14:30 in winter; resolved per
        # day through pytz so the comparison is right on both sides of DST.
        rel = _minutes_since_ny_open(true_utc)
        inside = (rel >= 0) & (rel < 30)
        score = float(vol[inside].mean()) if inside.any() else 0.0
        if score > best_score:
            best, best_score = off, score
    return best


_NY_OPEN_CACHE: dict[int, int] = {}


def _ny_open_utc(day: int) -> int:
    """Epoch seconds of 09:30 New York on the true-UTC day number `day`."""
    got = _NY_OPEN_CACHE.get(day)
    if got is None:
        d = datetime.fromtimestamp(day * 86400, timezone.utc).date()
        got = int(NY.localize(datetime(d.year, d.month, d.day, 9, 30)).timestamp())
        _NY_OPEN_CACHE[day] = got
    return got


def _minutes_since_ny_open(true_utc: np.ndarray) -> np.ndarray:
    days = (true_utc // 86400).astype(np.int64)
    opens = np.array([_ny_open_utc(int(d)) for d in np.unique(days)])
    lut = dict(zip(np.unique(days).tolist(), opens.tolist()))
    base = np.array([lut[int(d)] for d in days], dtype=np.int64)
    return (true_utc - base) // 60


def ny_sessions(b: dict, offset_h: int, length_min: int = CASH_MINUTES):
    """(first_idx, last_idx) of each DST-aware New York cash session, in stored-bar
    index space. The app resolves the same boundaries with pytz on true-UTC bars."""
    rel = _minutes_since_ny_open(b["time"] - offset_h * 3600)
    day = np.cumsum(np.r_[0, (np.diff(rel) < 0).astype(int)])   # a new session each time rel resets
    out = []
    for d in np.unique(day):
        idx = np.flatnonzero((day == d) & (rel >= 0) & (rel < length_min))
        if len(idx) >= 10:
            out.append((int(idx[0]), int(idx[-1])))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# bar resampling — the trend rule is an end-of-day/hourly rule, not an M5 one
# ─────────────────────────────────────────────────────────────────────────────
def resample(b: dict, seconds: int) -> dict:
    key = b["time"] // seconds
    starts = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    ends = np.r_[starts[1:] - 1, len(key) - 1]
    return {
        "time": b["time"][starts], "open": b["open"][starts],
        "high": np.maximum.reduceat(b["high"], starts),
        "low": np.minimum.reduceat(b["low"], starts),
        "close": b["close"][ends],
        "spread_pts": b["spread_pts"][starts],
        "vol": np.add.reduceat(b["vol"], starts),
        "point": b["point"],
    }


def atr_bars(b: dict, n: int = 14) -> np.ndarray:
    """ATR of the bars as given (used on resampled D1/H1 bars, where `atr_daily`'s
    regrouping by calendar day would be a no-op or wrong)."""
    h, lo, c = b["high"], b["low"], b["close"]
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) > n:
        cs = np.cumsum(tr)
        out[n:] = (cs[n:] - cs[:-n]) / n
    return out


# ─────────────────────────────────────────────────────────────────────────────
# the three app forms
# ─────────────────────────────────────────────────────────────────────────────
def app_trend(b: dict, sym: str, slip: float, entry_bars: int, atr_stop: float,
              trail_atr: float, target_rr: float, bars_per_day: int,
              defer_trail: bool = False, trail_lookback: int = 0) -> list[dict]:
    """TrendBreakout_v1 as the app runs it.

    signal   the bar CLOSES beyond the previous `entry_bars` channel
    fill     next bar's open
    stop     `atr_stop` x ATR(14) of the strategy timeframe's daily-scale bars
    trail    chandelier `trail_atr` x ATR from the best close since entry, as a
             real stop that never loosens; `defer_trail` holds each new level
             back one bar (live's timing) instead of applying it to the bar that
             produced it (the backtester's timing)
    target   `target_rr` — the app always needs one; set far enough away that it
             never fills, because the measured rule has none

    `trail_lookback` > 0 anchors the chandelier on the best close of the last N
    bars (the canonical Chandelier Exit) rather than the best close since entry.
    Only a BOUNDED window can be recomputed identically in the backtester and
    live, because live hands the strategy a fixed-length fetch rather than the
    whole trade. With the stop ratcheting (the app never loosens one) the two
    should agree; this measures whether they do.
    """
    o, h, lo, c, t = b["open"], b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    a = atr_bars(b) if bars_per_day == 1 else atr_daily(b)
    n = len(c)
    win = entry_bars
    if n < win + 20:
        return []

    hi_n = np.full(n, np.nan)
    lo_n = np.full(n, np.nan)
    sw_h = np.lib.stride_tricks.sliding_window_view(h, win)
    sw_l = np.lib.stride_tricks.sliding_window_view(lo, win)
    hi_n[win:] = sw_h.max(axis=1)[:-1]
    lo_n[win:] = sw_l.min(axis=1)[:-1]

    out: list[dict] = []
    i = win + 1
    while i < n - 3:
        if not np.isfinite(a[i]) or a[i] <= 0 or not np.isfinite(hi_n[i]):
            i += 1
            continue
        long_ = c[i] > hi_n[i]
        short_ = c[i] < lo_n[i]
        if long_ == short_:                      # neither, or (impossible) both
            i += 1
            continue

        entry_i = i + 1
        entry = o[entry_i]
        R = atr_stop * a[i]
        level = entry - R if long_ else entry + R
        tp = entry + target_rr * R if long_ else entry - target_rr * R
        pending = level
        exit_px = None
        j = entry_i
        while j < n:
            lb = max(entry_i, j - trail_lookback + 1) if trail_lookback else entry_i
            if long_:
                if lo[j] <= level:
                    exit_px = level
                    break
                if h[j] >= tp:
                    exit_px = tp
                    break
                best = max(entry, float(c[lb:j + 1].max()))
                nxt = max(pending, best - trail_atr * a[j])
                level, pending = (pending, nxt) if defer_trail else (nxt, nxt)
            else:
                if h[j] >= level:
                    exit_px = level
                    break
                if lo[j] <= tp:
                    exit_px = tp
                    break
                best = min(entry, float(c[lb:j + 1].min()))
                nxt = min(pending, best + trail_atr * a[j])
                level, pending = (pending, nxt) if defer_trail else (nxt, nxt)
            j += 1
        if exit_px is None:
            exit_px, j = c[n - 1], n - 1

        gross = (exit_px - entry) if long_ else (entry - exit_px)
        out.append({"t": int(t[entry_i]), "symbol": sym, "bars": j - entry_i,
                    "r": (gross - spread[entry_i] - sl) / R, "stop_distance": R})
        i = j + 1
    return out


def app_overnight(b: dict, sym: str, slip: float, offset_h: int,
                  stop_atr: float) -> list[dict]:
    """OvernightSession_v1 as the app runs it.

    Long the cash close -> next cash open on an equity index, with a REAL stop
    at `stop_atr` x daily ATR. Signalled on the session's last bar, filled at the
    next bar's open, closed on the last bar before the next session opens (whose
    close is the best available stand-in for that session's open price).
    """
    o, h, lo, c, t = b["open"], b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    a = atr_daily(b)
    ses = ny_sessions(b, offset_h)
    out = []
    for k in range(len(ses) - 1):
        _, close_i = ses[k]
        next_open_i, _ = ses[k + 1]
        entry_i = close_i + 1
        if entry_i >= next_open_i or not np.isfinite(a[close_i]) or a[close_i] <= 0:
            continue
        entry = o[entry_i]
        R = stop_atr * a[close_i]
        stop = entry - R
        exit_i = next_open_i - 1                 # hold through the gap, out before the bell
        exit_px = None
        for j in range(entry_i, exit_i + 1):
            if lo[j] <= stop:
                exit_px = stop
                break
        if exit_px is None:
            exit_px = c[exit_i]
        out.append({"t": int(t[entry_i]), "symbol": sym, "bars": exit_i - entry_i,
                    "r": (exit_px - entry - spread[entry_i] - sl) / R, "stop_distance": R})
    return out


def app_drive(b: dict, sym: str, slip: float, offset_h: int, ema_span: int = 12,
              stop_atr: float = 1.0, trail_atr: float = 1.0) -> list[dict]:
    """OpeningDrive_v1 as the app runs it — the marketed rule, with the session
    resolved DST-aware instead of at a fixed minute-of-day."""
    o, h, lo, c, t = b["open"], b["high"], b["low"], b["close"], b["time"]
    spread = b["spread_pts"] * b["point"]
    sl = slip * b["point"]
    e = ema(c, ema_span)
    a = atr_daily(b)
    out = []
    for first, last in ny_sessions(b, offset_h):
        if last - first < 12 or not np.isfinite(a[first]) or a[first] <= 0:
            continue
        long_ = c[first] > e[first]
        entry_i = first + 1
        entry = o[entry_i]
        R = stop_atr * a[first]
        level = entry - R if long_ else entry + R
        best = entry
        exit_px = None
        j = entry_i
        while j <= last:
            if long_:
                if lo[j] <= level:
                    exit_px = level
                    break
                best = max(best, c[j])
                level = max(level, best - trail_atr * a[first])
            else:
                if h[j] >= level:
                    exit_px = level
                    break
                best = min(best, c[j])
                level = min(level, best + trail_atr * a[first])
            j += 1
        if exit_px is None:
            exit_px, j = c[last], last
        gross = (exit_px - entry) if long_ else (entry - exit_px)
        out.append({"t": int(t[entry_i]), "symbol": sym, "bars": j - entry_i,
                    "r": (gross - spread[entry_i] - sl) / R, "stop_distance": R})
    return out


# ─────────────────────────────────────────────────────────────────────────────
def sharpe_daily(trades: list[dict], risk: float = 0.005) -> tuple[float, float]:
    by_day: dict[int, float] = {}
    for x in trades:
        d = int(x["t"] // 86400)
        by_day[d] = by_day.get(d, 0.0) + x["r"] * risk
    v = np.array(list(by_day.values()))
    if len(v) < 5 or v.std() == 0:
        return 0.0, 0.0
    return float(v.mean() / v.std() * np.sqrt(252)), float(v.std() * np.sqrt(252))


def row(name: str, trades: list[dict], risk: float) -> dict | None:
    if len(trades) < 20:
        return None
    s = summarise(trades, name)
    sh, vol = sharpe_daily(trades, risk / 100)
    rep = eng.report(eng.run_account(trades, 10_000.0, risk), name)
    print(f"  {name:34s} {s['n']:>5d} {s['expectancy_r']:>+7.3f} {s['win_rate'] * 100:>5.1f}% "
          f"{s['t_stat']:>+6.2f} {sh:>6.2f} {rep['final']:>10,.0f} {rep['return_pct']:>+8.1f}% "
          f"{rep['max_dd_pct']:>6.1f}%")
    return {"name": name, "summary": s, "sharpe": sh, "ann_vol": vol, "money": rep,
            "r_sequence": [x["r"] for x in trades]}


HDR = (f"  {'form':34s} {'N':>5s} {'expR':>7s} {'win%':>6s} {'t':>6s} {'Sh':>6s} "
       f"{'$ end':>10s} {'return':>9s} {'maxDD':>7s}")


TIMEFRAMES = {"M15": (900, 96), "M30": (1800, 48), "H1": (3600, 24),
              "H4": (14400, 6), "D1": (86400, 1)}


def trend_sweep(get, markets, slip, risk, target_rr) -> None:
    """Which bar size to signal the channel break on, and whether a bounded
    trail window costs anything.

    The research entered AT the channel on a resting order. The app cannot, so
    the break has to be a CLOSE beyond the channel and the lag is one bar of
    whatever timeframe the strategy runs on. Smaller bars mean a closer fill and
    a shorter lag; they also mean more bars per position, which is what the
    exit hook has to be able to recompute. This is that trade-off, measured.
    """
    print(chr(10) + "1b. TREND BREAKOUT sweep: signal timeframe x trail window")
    print(f"  {'tf':>4s} {'trail window':>13s} {'N':>5s} {'expR':>7s} {'win%':>6s} {'t':>6s} "
          f"{'Sh':>6s} {'return':>9s} {'maxDD':>7s} {'med bars':>9s} {'max bars':>9s}")
    for tf, (secs, per_day) in TIMEFRAMES.items():
        entry_bars = 20 * per_day
        for lookback in (0, 100, 300):
            tr = []
            for sym in markets:
                b = get(sym)
                if b is None:
                    continue
                tr += app_trend(resample(b, secs), sym, slip, entry_bars, 2.0, 1.5,
                                target_rr, per_day, trail_lookback=lookback)
            if len(tr) < 20:
                continue
            s = summarise(tr, tf)
            sh, _ = sharpe_daily(tr, risk / 100)
            rep = eng.report(eng.run_account(tr, 10_000.0, risk), tf)
            held = np.array([x["bars"] for x in tr])
            label = "since entry" if not lookback else f"last {lookback}"
            print(f"  {tf:>4s} {label:>13s} {s['n']:>5d} {s['expectancy_r']:>+7.3f} "
                  f"{s['win_rate'] * 100:>5.1f}% {s['t_stat']:>+6.2f} {sh:>6.2f} "
                  f"{rep['return_pct']:>+8.1f}% {rep['max_dd_pct']:>6.1f}% "
                  f"{np.median(held):>9.0f} {held.max():>9.0f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--since", default="2021-10-01")
    ap.add_argument("--slip-points", type=float, default=1.0)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--target-rr", type=float, default=20.0)
    ap.add_argument("--sweep", action="store_true",
                    help="only the trend signal-timeframe / trail-window sweep")
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(specs_path.read_text())
    t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())

    cache: dict[str, dict] = {}

    def get(sym):
        if sym in cache:
            return cache[sym]
        try:
            b = load(bars_dir, sym)
        except FileNotFoundError:
            cache[sym] = None
            return None
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        cache[sym] = b if len(b["time"]) >= 5000 else None
        return cache[sym]

    print(f"\nAPP-FORM CHECK — {args.feed} feed, {args.since} -> today, "
          f"${10_000:,} at {args.risk}% risk, costs = bar spread + {args.slip_points} pts")

    offsets = {}
    for sym in set(TREND_MARKETS + DRIVE_MARKETS + NIGHT_MARKETS):
        b = get(sym)
        if b is not None:
            offsets[sym] = broker_utc_offset_hours(b)
    if offsets:
        uniq = sorted(set(offsets.values()))
        print(f"\nbroker UTC offset calibrated from the volume profile: "
              f"{', '.join(f'{o:+d}h' for o in uniq)} "
              f"({len(offsets)} symbols; the app detects the same thing from a live tick)")

    results: dict[str, dict] = {}

    if args.sweep:
        trend_sweep(get, TREND_MARKETS, args.slip_points, args.risk, args.target_rr)
        return

    # ── 1. trend ────────────────────────────────────────────────────────────
    print("\n1. TREND BREAKOUT — 20-day channel, 2xATR stop, 1.5xATR chandelier trail")
    print(HDR)
    research = []
    for sym in TREND_MARKETS:
        b = get(sym)
        if b is not None:
            research += donchian_trend(b, sym, 20, 20, 2.0, 1.5, args.slip_points)
    results["trend_research"] = row("research (level entry, M5)", research, args.risk)

    for tf_name, tf_secs, bars_per_day, entry_bars in (("D1", 86400, 1, 20), ("H1", 3600, 24, 480)):
        for defer in (False, True):
            tr = []
            for sym in TREND_MARKETS:
                b = get(sym)
                if b is None:
                    continue
                tr += app_trend(resample(b, tf_secs), sym, args.slip_points, entry_bars,
                                2.0, 1.5, args.target_rr, bars_per_day, defer_trail=defer)
            tag = f"app {tf_name} close entry{' (trail +1 bar)' if defer else ''}"
            results[f"trend_app_{tf_name}{'_defer' if defer else ''}"] = row(tag, tr, args.risk)

    # ── 2. overnight ────────────────────────────────────────────────────────
    print("\n2. OVERNIGHT SESSION — long the cash close -> next cash open, equity indices")
    print(HDR)
    research = []
    for sym in NIGHT_MARKETS:
        b = get(sym)
        if b is not None:
            research += h5_overnight(b, sym, args.slip_points, True)
    results["night_research"] = row("research (no stop, fixed session)", research, args.risk)

    for stop_atr in (0.5, 1.0, 1.5, 2.0, 3.0):
        tr = []
        for sym in NIGHT_MARKETS:
            b = get(sym)
            if b is None:
                continue
            tr += app_overnight(b, sym, args.slip_points, offsets.get(sym, 0), stop_atr)
        results[f"night_app_{stop_atr}"] = row(f"app, real stop {stop_atr:g}xATR", tr, args.risk)

    # ── 3. opening drive ────────────────────────────────────────────────────
    print("\n3. OPENING DRIVE — first 5-minute candle vs the 12 EMA at the NY open")
    print(HDR)
    research = []
    for sym in DRIVE_MARKETS:
        b = get(sym)
        if b is not None:
            research += claimed(b, sym, args.slip_points, trail_atr=1.0, stop_atr=1.0)
    results["drive_research"] = row("research (fixed session minute)", research, args.risk)

    tr = []
    per_market = {}
    for sym in DRIVE_MARKETS:
        b = get(sym)
        if b is None:
            continue
        got = app_drive(b, sym, args.slip_points, offsets.get(sym, 0))
        per_market[sym] = got
        tr += got
    results["drive_app"] = row("app (DST-aware NY session)", tr, args.risk)

    print("\n   per market, app form:")
    for sym, got in per_market.items():
        if len(got) >= 20:
            s = summarise(got, sym)
            print(f"     {sym:16s} n {s['n']:>4d}  expR {s['expectancy_r']:>+.3f}  "
                  f"win {s['win_rate'] * 100:>4.1f}%  t {s['t_stat']:>+5.2f}")

    (OUT / f"app_form_{args.feed}.json").write_text(json.dumps(
        {k: v for k, v in results.items() if v}, indent=1, default=float))
    print(f"\n-> {OUT / f'app_form_{args.feed}.json'}")


if __name__ == "__main__":
    main()
