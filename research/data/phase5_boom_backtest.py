"""Backtest BoomDriftJump (and variants) on ticks, 1 Jan 2026 -> now.

Two things this does that the app's backtester cannot:

* **Fills come from ticks, not bars.** A stop is a market order and fills at the
  FIRST tick through the level, which on Boom means wherever the up-spike lands -
  often far beyond the stop. research/24 §4.1 measured that term at -0.98 R on a
  0.5 x ATR stop, and both existing harnesses book a flat -1.0 R instead. Every
  result below is reported BOTH ways so the size of that artifact is visible
  rather than assumed.
* **Significance accounts for trade overlap.** research/24 §4 showed a strategy
  with no entry logic reads t = +3.99 on overlapping trades and +0.56 on
  independent ones. The independent subset is reported alongside the raw number.

Indicators are imported from the production DriftJumpAlpha engine, so ATR and ADX
are computed by exactly the code that runs live rather than a lookalike.

The prediction on record (research/24): Boom is a fair martingale with memoryless
jumps, so gross expectancy should be ~0 and net expectancy ~ -cost, for every
geometry tested. This script exists to check that rather than assert it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.strategies.strategy_two.engine import (            # noqa: E402
    calculate_adx, calculate_atr)

TICKS = Path(__file__).resolve().parent / "ticks"
START = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()

# historical median spread as a fraction of price (synth_spread_audit.py)
SPREAD = {"BOOM1000": 0.00100 / 100, "BOOM500": 0.00139 / 100,
          "CRASH1000": 0.00099 / 100, "CRASH500": 0.00138 / 100}
MAX_LOOK = 2_000_000        # ticks; ~23 days, far beyond any trade here
MAX_TRADES_PER_DAY = 6


_CACHE: dict = {}


def load_m5(code: str):
    """Cached. The geometry sweep calls run() ~40 times and each call would
    otherwise re-slice 31 M ticks and rebuild 71,000 M5 bars from scratch."""
    if code in _CACHE:
        return _CACHE[code]
    r = _load_m5_uncached(code)
    _CACHE[code] = r
    return r


def _load_m5_uncached(code: str):
    z = np.load(max(TICKS.glob(f"{code}_*d.npz"), key=lambda x: x.stat().st_size),
                allow_pickle=True)
    t, p = z["t"].astype(np.int64), z["p"].astype(float)
    m = t >= START
    t, p = t[m], p[m]
    bucket = (t // 300) * 300
    edges = np.flatnonzero(np.diff(bucket)) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(bucket)]))
    o = p[starts]
    c = p[ends - 1]
    hi = np.maximum.reduceat(p, starts)
    lo = np.minimum.reduceat(p, starts)
    df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c},
                      index=pd.to_datetime(bucket[starts], unit="s", utc=True))
    return t, p, df, starts


_SIGCACHE: dict = {}


def signals(df: pd.DataFrame, mirror: bool, tp_rr: float, stop_atr: float,
            use_adx: bool = True):
    """Mirror=True -> Boom (SELL the grind). Mirror=False -> Crash (BUY the grind)."""
    key = (id(df), mirror, use_adx)
    if key in _SIGCACHE:
        d, ok, a, sep = _SIGCACHE[key]
        return _emit(d, ok, a, sep, tp_rr, stop_atr)

    d = df.copy()
    d["ema_fast"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema_slow"] = d["close"].ewm(span=50, adjust=False).mean()
    d["atr"] = calculate_atr(d, 14)
    d["adx"] = calculate_adx(d, 14)
    a = d["atr"].to_numpy()
    ef, es = d["ema_fast"].to_numpy(), d["ema_slow"].to_numpy()
    cl, op, hi, lo = (d["close"].to_numpy(), d["open"].to_numpy(),
                      d["high"].to_numpy(), d["low"].to_numpy())
    adx = d["adx"].to_numpy()
    sep = np.abs(ef - es)

    # gap percentile of this bar's move in the jump direction
    move = (hi - op) if mirror else (op - lo)
    with np.errstate(invalid="ignore", divide="ignore"):
        move_atr = np.where(a > 0, move / a, 0.0)
    gap_pct = np.full(len(d), 0.0)
    for i in range(30, len(d)):
        w = move_atr[max(0, i - 500):i]
        gap_pct[i] = (w < move_atr[i]).mean() * 100 if len(w) else 0.0

    regime = (ef < es) if mirror else (ef > es)
    ok = (regime & (sep > 0.2 * a) & np.isfinite(a) & (a > 0)
          & (gap_pct < 95.0))
    ok &= (cl <= ef) if mirror else (cl >= ef)
    if use_adx:
        ok &= np.nan_to_num(adx, nan=0.0) >= 20.0
    ok[:50] = False

    _SIGCACHE[key] = (d, ok, a, sep)
    return _emit(d, ok, a, sep, tp_rr, stop_atr)


def _emit(d, ok, a, sep, tp_rr, stop_atr):
    cl = d["close"].to_numpy()
    out = []
    day_count, cur_day = 0, None
    for i in np.flatnonzero(ok):
        day = d.index[i].date()
        if day != cur_day:
            cur_day, day_count = day, 0
        if day_count >= MAX_TRADES_PER_DAY:
            continue
        day_count += 1
        risk = max(stop_atr * a[i], sep[i] + a[i]) if stop_atr >= 2.0 else stop_atr * a[i]
        if not np.isfinite(risk) or risk <= 0:
            continue
        out.append((i, float(cl[i]), float(risk), tp_rr))
    return d, out


def run(code: str, mirror: bool, tp_rr=5.0, stop_atr=2.5, use_adx=True,
        label: str = ""):
    t, p, df, starts = load_m5(code)
    d, sigs = signals(df, mirror, tp_rr, stop_atr, use_adx)
    half = SPREAD[code] / 2
    naive, real, opens, closes = [], [], [], []

    for i, close_px, risk, rr in sigs:
        k = starts[i + 1] if i + 1 < len(starts) else None
        if k is None or k >= len(p) - 2:
            continue
        mid = p[k]
        entry = mid * (1 - half) if mirror else mid * (1 + half)   # sell at bid
        sl = entry + risk if mirror else entry - risk
        tp = entry - risk * rr if mirror else entry + risk * rr
        seg = p[k + 1:min(k + 1 + MAX_LOOK, len(p))]
        if seg.size == 0:
            continue
        hit_sl = np.flatnonzero(seg >= sl) if mirror else np.flatnonzero(seg <= sl)
        hit_tp = np.flatnonzero(seg <= tp) if mirror else np.flatnonzero(seg >= tp)
        js = int(hit_sl[0]) if hit_sl.size else 10 ** 18
        jt = int(hit_tp[0]) if hit_tp.size else 10 ** 18
        if js == 10 ** 18 and jt == 10 ** 18:
            continue
        if js < jt:                                   # stop: market, fills where it lands
            raw = float(seg[js])
            fill = raw * (1 + half) if mirror else raw * (1 - half)
            r_real = (entry - fill) / risk if mirror else (fill - entry) / risk
            r_naive = -1.0
            end = k + 1 + js
        else:                                         # target: limit, no improvement
            fill = tp * (1 + half) if mirror else tp * (1 - half)
            r_real = (entry - fill) / risk if mirror else (fill - entry) / risk
            r_naive = rr
            end = k + 1 + jt
        naive.append(r_naive)
        real.append(r_real)
        opens.append(k)
        closes.append(end)

    if not real:
        print(f"  {label or code}: no trades")
        return None
    nv, rl = np.asarray(naive), np.asarray(real)
    keep, last = [], -1
    for idx, (o_, c_) in enumerate(zip(opens, closes)):
        if o_ > last:
            keep.append(idx)
            last = c_
    ind = rl[keep]
    se = rl.std(ddof=1) / np.sqrt(len(rl))
    se_i = ind.std(ddof=1) / np.sqrt(len(ind)) if len(ind) > 2 else np.nan
    print(f"  {label or code:26s} n={len(rl):4d}  win {(rl>0).mean():5.1%}  "
          f"naive {nv.mean():+7.4f}R  real {rl.mean():+7.4f}R  "
          f"slip {rl.mean()-nv.mean():+7.4f}  t {rl.mean()/se:+5.2f}  "
          f"| indep n={len(ind):3d} {ind.mean():+7.4f}R t {ind.mean()/se_i:+5.2f}")
    return {"n": len(rl), "naive": nv.mean(), "real": rl.mean(),
            "t": rl.mean() / se, "n_ind": len(ind), "ind": ind.mean(),
            "t_ind": ind.mean() / se_i if se_i == se_i else np.nan}


def main() -> None:
    print("Window: 2026-01-01 -> now. Fills from ticks. Costs = historical spread.\n")
    print("=" * 118)
    print("1. BoomDriftJump — the DriftJumpAlpha mirror, as shipped in "
          "backend/strategies/strategy_boom/")
    print("=" * 118)
    run("BOOM1000", True, label="Boom 1000  drift SELL")
    run("BOOM500", True, label="Boom 500   drift SELL")
    print("\n  control — the original on its own instrument:")
    run("CRASH1000", False, label="Crash 1000 drift BUY")
    run("CRASH500", False, label="Crash 500  drift BUY")

    print("\n" + "=" * 118)
    print("2. GEOMETRY SWEEP — is any stop/target combination better?")
    print("=" * 118)
    for code, mir in (("BOOM1000", True), ("CRASH1000", False)):
        print(f"\n  {code}:")
        for stop_atr in (0.5, 1.0, 2.5, 5.0):
            for rr in (1.5, 3.0, 5.0, 8.0):
                run(code, mir, tp_rr=rr, stop_atr=stop_atr,
                    label=f"stop {stop_atr:>3}xATR  tp {rr:>3}R")

    print("\n" + "=" * 118)
    print("3. FILTER TEST — does the ADX gate add anything?")
    print("=" * 118)
    for code, mir in (("BOOM1000", True), ("CRASH1000", False)):
        run(code, mir, use_adx=True, label=f"{code} with ADX gate")
        run(code, mir, use_adx=False, label=f"{code} no ADX gate")


if __name__ == "__main__":
    main()
