"""RIPTIDE — a strategy built only from the confluences that measured positive.

Report 08 ranked every structural atom by forward edge. Four were positive and
everything else was neutral or negative:

    liquidity sweep (low)   +0.0267 R      premium/discount (buy)  +0.0226 R
    liquidity sweep (high)  +0.0177 R      premium/discount (sell) +0.0194 R

Riptide uses those and nothing else. No FVG, no break of structure, no retest,
no displacement — all four measured NEGATIVE, i.e. worse than a random entry.

Name: a riptide drags water out past the shoreline and then returns it. That is
the setup — price sweeps out past a level to take stops, then comes back.

ENTRY LOGIC
-----------
BUY  when: price traded below a prior swing low (the sweep), closed back above
           it (the reclaim), and sits in the lower part of its recent range
           (discount).
SELL when: the mirror image.

CONFLUENCE SCORE — one point per condition met, entry gated on a minimum:
    1. sweep_reclaim   the sweep happened and price closed back through
    2. zone            in discount (buy) / premium (sell)
    3. deep_zone       in the extreme third, not just the near half
    4. clean_sweep     the swept level was untouched for `lookback` bars
    5. momentum_ok     the reclaim bar closes in the top/bottom third of range

STOP: beyond the sweep extreme plus a buffer.
TARGET: fixed R multiple, swept over 2R / 3R / 5R.
"""
from __future__ import annotations
import numpy as np


def swings(high, low, m=5):
    """Fractal swing highs/lows: extreme of a 2m+1 window."""
    n = len(high)
    sh = np.zeros(n, dtype=bool)
    sl = np.zeros(n, dtype=bool)
    for i in range(m, n - m):
        if high[i] == high[i - m:i + m + 1].max():
            sh[i] = True
        if low[i] == low[i - m:i + m + 1].min():
            sl[i] = True
    return sh, sl


def atr(h, l, c, n=14):
    prev = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    out = np.full(len(tr), np.nan)
    a = tr[:n].mean()
    out[n - 1] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def signals(bars, lookback=60, swing_m=5, min_score=2, zone_frac=0.33,
            buffer_atr=0.25):
    """Return list of (bar_index, direction, entry, stop, score)."""
    o, h, l, c = (bars[k] for k in ("open", "high", "low", "close"))
    n = len(c)
    a = atr(h, l, c)
    sh, sl = swings(h, l, swing_m)
    sh_idx = np.where(sh)[0]
    sl_idx = np.where(sl)[0]
    out = []

    for i in range(lookback + swing_m, n - 1):
        if not np.isfinite(a[i]) or a[i] <= 0:
            continue
        win_hi = h[i - lookback:i].max()
        win_lo = l[i - lookback:i].min()
        rng = win_hi - win_lo
        if rng <= 0:
            continue
        pos = (c[i] - win_lo) / rng          # 0 = bottom of range, 1 = top
        bar_rng = h[i] - l[i]
        if bar_rng <= 0:
            continue

        # ---- BUY: swept a prior swing low, closed back above it -----------
        prior_lows = sl_idx[(sl_idx < i - 1) & (sl_idx >= i - lookback)]
        if len(prior_lows):
            lvl = l[prior_lows].min()
            swept = l[i] < lvl and c[i] > lvl
            if swept:
                score = 1
                if pos <= 0.5:
                    score += 1
                if pos <= zone_frac:
                    score += 1
                touched = (l[i - lookback:i] < lvl).sum()
                if touched <= 1:
                    score += 1
                if (c[i] - l[i]) / bar_rng >= 0.66:
                    score += 1
                if score >= min_score:
                    stop = l[i] - buffer_atr * a[i]
                    if c[i] - stop > 0:
                        out.append((i, "BUY", c[i], stop, score))

        # ---- SELL: mirror --------------------------------------------------
        prior_highs = sh_idx[(sh_idx < i - 1) & (sh_idx >= i - lookback)]
        if len(prior_highs):
            lvl = h[prior_highs].max()
            swept = h[i] > lvl and c[i] < lvl
            if swept:
                score = 1
                if pos >= 0.5:
                    score += 1
                if pos >= 1 - zone_frac:
                    score += 1
                touched = (h[i - lookback:i] > lvl).sum()
                if touched <= 1:
                    score += 1
                if (h[i] - c[i]) / bar_rng >= 0.66:
                    score += 1
                if score >= min_score:
                    stop = h[i] + buffer_atr * a[i]
                    if stop - c[i] > 0:
                        out.append((i, "SELL", c[i], stop, score))
    return out


def backtest(bars, sigs, target_r, cost_r=0.0, horizon=400,
             balance=10000.0, risk_pct=1.0, min_gap=7):
    """Walk each signal forward on the real path. Fixed fractional sizing."""
    h, l, c = bars["high"], bars["low"], bars["close"]
    n = len(c)
    eq = balance
    curve, rs, last_i = [], [], -10 ** 9
    for i, d, entry, stop, score in sigs:
        if i - last_i < min_gap:
            continue
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        last_i = i
        buy = d == "BUY"
        tp = entry + target_r * risk if buy else entry - target_r * risk
        r = None
        end = min(i + horizon, n - 1)
        for j in range(i + 1, end + 1):
            if buy:
                if l[j] <= stop:
                    r = -1.0; break
                if h[j] >= tp:
                    r = target_r; break
            else:
                if h[j] >= stop:
                    r = -1.0; break
                if l[j] <= tp:
                    r = target_r; break
        if r is None:
            r = ((c[end] - entry) / risk) if buy else ((entry - c[end]) / risk)
            r = float(np.clip(r, -1.0, target_r))
        r -= cost_r
        rs.append(r)
        eq += eq * (risk_pct / 100.0) * r
        curve.append(eq)
    if not rs:
        return None
    rs = np.asarray(rs)
    curve = np.asarray(curve)
    peak = np.maximum.accumulate(curve)
    dd = float(((peak - curve) / peak).max() * 100) if len(curve) else 0.0
    wins = rs[rs > 0]
    gl = -rs[rs <= 0].sum()
    return {
        "n": len(rs), "exp": float(rs.mean()), "wr": float((rs > 0).mean()),
        "pf": float(wins.sum() / gl) if gl > 0 else float("inf"),
        "pnl": float(eq - balance), "final": float(eq), "maxdd_pct": dd,
        "curve": curve,
    }
