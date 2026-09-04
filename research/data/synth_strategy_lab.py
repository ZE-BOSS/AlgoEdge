"""Strategy lab: full-metric tick backtests across the synthetic universe.

Purpose: find the best available configuration per symbol in the window
1 Jan 2026 -> now, and report it with the complete metric set (P&L, win rate,
profit factor, max drawdown, Sharpe, consecutive losses) rather than expectancy
in R alone.

EXECUTION MODEL — this is the part that makes the numbers reproducible
----------------------------------------------------------------------
* Signals are computed on **M5 bars built from ticks**, so they match what the
  app's M5 strategies see.
* Entry is at the **first tick of the next bar**, crossing the spread
  (buy at ask, sell at bid). No same-bar entry, no look-ahead.
* A **stop is a market order**: it fills at the first tick THROUGH the level.
  On Boom/Crash that is wherever the spike lands, which is often far past the
  stop. This is the single biggest difference from a bar-based backtest.
* A **target is a limit order**: it fills at the limit price, never better.
* `naive_r` is reported alongside every result — that is what a bar backtest
  booking a flat -1R stop would have printed, so the two can be compared.

SIZING
------
`STATIC`  : risk = risk_pct of the STARTING balance on every trade (no compounding)
`BALANCE` : risk = risk_pct of the CURRENT balance (compounds)
Both are reported. research/20 notes a 69-trade losing streak costs a flat 6.9%
under STATIC and ~50% under BALANCE, so the two are not interchangeable.

CONCURRENCY
-----------
`max_concurrent` caps simultaneously open positions. At 1 the strategy is
strictly sequential (no pyramiding); above 1 it may hold several. Reported
explicitly on every run because it changes both P&L and drawdown materially.
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
MAX_LOOK = 3_000_000

# historical median spread as a fraction of price (synth_spread_audit.py)
SPREAD = {
    "BOOM1000": 0.00100 / 100, "BOOM500": 0.00139 / 100,
    "CRASH1000": 0.00099 / 100, "CRASH500": 0.00138 / 100,
    "R_75": 0.03467 / 100, "R_25": 0.00954 / 100, "R_100": 0.04260 / 100,
    "stpRNG": 0.00256 / 100, "RB100": 0.00185 / 100, "RB200": 0.00150 / 100,
    "JD25": 0.00588 / 100, "JD100": 0.02182 / 100,
}
MT5_NAME = {
    "BOOM1000": "Boom 1000 Index", "BOOM500": "Boom 500 Index",
    "CRASH1000": "Crash 1000 Index", "CRASH500": "Crash 500 Index",
    "R_75": "Volatility 75 Index", "R_25": "Volatility 25 Index",
    "R_100": "Volatility 100 Index", "stpRNG": "Step Index",
    "RB100": "Range Break 100 Index", "RB200": "Range Break 200 Index",
    "JD25": "Jump 25 Index", "JD100": "Jump 100 Index",
}

_BARS: dict = {}
_IND: dict = {}


def bars(code: str):
    if code in _BARS:
        return _BARS[code]
    z = np.load(max(TICKS.glob(f"{code}_*d.npz"), key=lambda x: x.stat().st_size),
                allow_pickle=True)
    t, p = z["t"].astype(np.int64), z["p"].astype(float)
    m = t >= START
    t, p = t[m], p[m]
    bucket = (t // 300) * 300
    edges = np.flatnonzero(np.diff(bucket)) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(bucket)]))
    df = pd.DataFrame(
        {"open": p[starts], "high": np.maximum.reduceat(p, starts),
         "low": np.minimum.reduceat(p, starts), "close": p[ends - 1]},
        index=pd.to_datetime(bucket[starts], unit="s", utc=True))
    _BARS[code] = (t, p, df, starts)
    return _BARS[code]


def indicators(code: str):
    if code in _IND:
        return _IND[code]
    _, _, df, _ = bars(code)
    d = df.copy()
    d["ema_f"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema_s"] = d["close"].ewm(span=50, adjust=False).mean()
    d["atr"] = calculate_atr(d, 14)
    d["adx"] = calculate_adx(d, 14)
    d["hh"] = d["high"].rolling(20).max()
    d["ll"] = d["low"].rolling(20).min()
    _IND[code] = d
    return d


# ---------------------------------------------------------------- templates
def entries(code: str, template: str, **kw) -> tuple[np.ndarray, np.ndarray]:
    """Return (bar_indices, direction) where direction is +1 long / -1 short."""
    d = indicators(code)
    a = d["atr"].to_numpy()
    ef, es = d["ema_f"].to_numpy(), d["ema_s"].to_numpy()
    cl, op = d["close"].to_numpy(), d["open"].to_numpy()
    hi, lo = d["high"].to_numpy(), d["low"].to_numpy()
    adx = np.nan_to_num(d["adx"].to_numpy(), nan=0.0)
    hh, ll = d["hh"].to_numpy(), d["ll"].to_numpy()
    n = len(d)
    ok = np.zeros(n, dtype=bool)
    dirn = np.zeros(n, dtype=int)
    valid = np.isfinite(a) & (a > 0)

    if template == "drift":
        # trend continuation along the EMA regime, entered on a pullback
        side = kw.get("side", 0)          # 0 = follow regime, +1 long only, -1 short only
        up = (ef > es) & (np.abs(ef - es) > 0.2 * a) & (cl >= ef)
        dn = (ef < es) & (np.abs(ef - es) > 0.2 * a) & (cl <= ef)
        if kw.get("adx", True):
            up &= adx >= 20
            dn &= adx >= 20
        if side >= 0:
            ok |= up
            dirn = np.where(up, 1, dirn)
        if side <= 0:
            ok |= dn
            dirn = np.where(dn & (dirn == 0), -1, dirn)

    elif template in ("jump_fade", "jump_follow"):
        # a bar whose range in one direction is a large multiple of ATR
        k = kw.get("k", 3.0)
        upmove = (hi - op) / np.where(a > 0, a, np.nan)
        dnmove = (op - lo) / np.where(a > 0, a, np.nan)
        is_up = np.nan_to_num(upmove, nan=0) >= k
        is_dn = np.nan_to_num(dnmove, nan=0) >= k
        if template == "jump_fade":
            ok = is_up | is_dn
            dirn = np.where(is_up, -1, np.where(is_dn, 1, 0))
        else:
            ok = is_up | is_dn
            dirn = np.where(is_up, 1, np.where(is_dn, -1, 0))

    elif template == "revert":
        # price stretched from the slow EMA, enter back toward it
        k = kw.get("k", 2.0)
        far_up = (cl - es) > k * a
        far_dn = (es - cl) > k * a
        ok = far_up | far_dn
        dirn = np.where(far_up, -1, np.where(far_dn, 1, 0))

    elif template == "breakout":
        prev_hh = np.concatenate(([np.nan], hh[:-1]))
        prev_ll = np.concatenate(([np.nan], ll[:-1]))
        bo_up = cl > np.nan_to_num(prev_hh, nan=np.inf)
        bo_dn = cl < np.nan_to_num(prev_ll, nan=-np.inf)
        ok = bo_up | bo_dn
        dirn = np.where(bo_up, 1, np.where(bo_dn, -1, 0))

    ok &= valid & (dirn != 0)
    ok[:60] = False
    return np.flatnonzero(ok), dirn


def first_touch(p: np.ndarray, k: int, sl: float, tp: float, long: bool):
    """Index and kind of the first level touched after tick k, or (None, None).

    Chunked rather than scanning the whole lookahead. Most trades resolve inside a
    few thousand ticks, but the window has to allow for the rare one that runs for
    weeks; scanning 3 M ticks for every trade made the full search take hours.
    Doubling the chunk keeps the worst case cheap without penalising the common one.
    """
    lo = k + 1
    hi = min(k + 1 + MAX_LOOK, len(p))
    step = 20_000
    while lo < hi:
        end = min(lo + step, hi)
        seg = p[lo:end]
        hs = np.flatnonzero(seg <= sl) if long else np.flatnonzero(seg >= sl)
        ht = np.flatnonzero(seg >= tp) if long else np.flatnonzero(seg <= tp)
        if hs.size or ht.size:
            js = int(hs[0]) if hs.size else 1 << 60
            jt = int(ht[0]) if ht.size else 1 << 60
            if js < jt:
                return lo + js, "sl"
            return lo + jt, "tp"
        lo = end
        step = min(step * 2, 500_000)
    return None, None


# ---------------------------------------------------------------- backtest
def backtest(code: str, template: str, stop_atr: float, tp_rr: float,
             max_concurrent: int = 1, risk_pct: float = 1.0,
             sizing: str = "STATIC", start_balance: float = 10_000.0,
             max_per_day: int = 6, **kw):
    t, p, df, starts = bars(code)
    d = indicators(code)
    a = d["atr"].to_numpy()
    idx, dirn = entries(code, template, **kw)
    half = SPREAD[code] / 2

    open_until: list[int] = []
    trades = []
    day_count, cur_day = 0, None

    for i in idx:
        day = d.index[i].date()
        if day != cur_day:
            cur_day, day_count = day, 0
        if day_count >= max_per_day:
            continue
        if i + 1 >= len(starts):
            continue
        k = starts[i + 1]
        if k >= len(p) - 2:
            continue
        open_until[:] = [x for x in open_until if x > k]
        if len(open_until) >= max_concurrent:
            continue

        long = dirn[i] > 0
        mid = p[k]
        entry = mid * (1 + half) if long else mid * (1 - half)
        risk = stop_atr * a[i]
        if not np.isfinite(risk) or risk <= 0:
            continue
        sl = entry - risk if long else entry + risk
        tp = entry + tp_rr * risk if long else entry - tp_rr * risk

        j, kind = first_touch(p, k, sl, tp, long)
        if j is None:
            continue
        if kind == "sl":                # market order: fills where the gap lands
            raw = float(p[j])
            fill = raw * (1 - half) if long else raw * (1 + half)
            naive = -1.0
        else:                           # limit order: fills at the level, never better
            fill = tp * (1 - half) if long else tp * (1 + half)
            naive = tp_rr
        r = (fill - entry) / risk if long else (entry - fill) / risk
        end = j
        open_until.append(end)
        day_count += 1
        trades.append({"r": r, "naive": naive, "open": k, "close": end,
                       "t_open": int(t[k]), "t_close": int(t[min(end, len(t) - 1)])})

    return metrics(trades, risk_pct, sizing, start_balance)


def metrics(trades: list[dict], risk_pct: float, sizing: str, bal0: float) -> dict:
    if len(trades) < 5:
        return {"n": len(trades)}
    trades.sort(key=lambda x: x["t_close"])
    r = np.array([x["r"] for x in trades])
    naive = np.array([x["naive"] for x in trades])

    # Equity curve, floored at zero. A real account stops when it is empty, so a
    # curve that keeps compounding a negative balance produces meaningless
    # figures - the first version of this printed -266% returns and a 269% max
    # drawdown. `blown` records whether the account died and on which trade.
    bal = bal0
    curve = [bal0]
    blown_at = None
    for j, x in enumerate(r):
        risk_usd = bal * risk_pct / 100 if sizing == "BALANCE" else bal0 * risk_pct / 100
        bal += risk_usd * x
        if bal <= 0:
            bal = 0.0
            curve.append(bal)
            blown_at = j + 1
            break
        curve.append(bal)
    curve = np.asarray(curve)
    r_used = r[:blown_at] if blown_at is not None else r
    peak = np.maximum.accumulate(curve)
    dd = (peak - curve) / np.maximum(peak, 1e-9)

    wins, losses = r[r > 0], r[r <= 0]
    gp = float(wins.sum()) if wins.size else 0.0
    gl = float(-losses.sum()) if losses.size else 0.0
    streak = mx = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        mx = max(mx, streak)

    days = max(1.0, (trades[-1]["t_close"] - trades[0]["t_open"]) / 86400)
    # daily returns for Sharpe
    day_key = np.array([x["t_close"] // 86400 for x in trades])
    daily = {}
    for k_, x in zip(day_key, r):
        daily[k_] = daily.get(k_, 0.0) + x
    dv = np.array(list(daily.values()))
    sharpe = (dv.mean() / dv.std(ddof=1) * np.sqrt(252)) if len(dv) > 2 and dv.std(ddof=1) > 0 else np.nan

    # independent (non-overlapping) subset
    keep, last = [], -1
    for j, x in enumerate(sorted(trades, key=lambda z: z["open"])):
        if x["open"] > last:
            keep.append(x["r"])
            last = x["close"]
    ind = np.asarray(keep)
    se_i = ind.std(ddof=1) / np.sqrt(len(ind)) if len(ind) > 2 else np.nan

    return {
        "n": len(r), "win_rate": float((r > 0).mean()),
        "expectancy_r": float(r.mean()), "total_r": float(r.sum()),
        "naive_r": float(naive.mean()),
        "pnl": float(curve[-1] - bal0), "final_balance": float(curve[-1]),
        "return_pct": float((curve[-1] / bal0 - 1) * 100),
        "profit_factor": (gp / gl if gl > 0 else float("inf")),
        "max_dd_pct": float(min(dd.max(), 1.0) * 100),
        "blown": blown_at is not None,
        "blown_at": blown_at,
        "max_dd_usd": float((peak - curve).max()),
        "sharpe": float(sharpe) if sharpe == sharpe else float("nan"),
        "max_consec_losses": int(mx),
        "avg_win_r": float(wins.mean()) if wins.size else 0.0,
        "avg_loss_r": float(losses.mean()) if losses.size else 0.0,
        "trades_per_day": len(r) / days, "days": days,
        "t": float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))),
        "n_ind": len(ind), "ind_r": float(ind.mean()) if len(ind) > 2 else float("nan"),
        "t_ind": float(ind.mean() / se_i) if se_i == se_i and se_i > 0 else float("nan"),
    }
