"""Every confluence, broken into its smallest unit, measured against the market.

This is deliberately independent of the saved backtest. It asks, for each
atomic market condition, one question:

    when this condition is true, does price subsequently travel further in the
    condition's direction than it does when the condition is false?

Measurement is stop-aware forward excursion in R (see excursion_engine), so a
peak reached after the stop was hit never counts.

Atom definitions follow the project's own detectors where those exist, so the
numbers here are comparable to what the strategies actually key on:
  FVG           3-candle gap, size >= 0.2 x ATR      (FVGDetector default)
  displacement  candle body >= 1.0 x ATR, body >= 60% of range
  sweep         prior 20-bar extreme taken, closed back inside  (LiquidityMapper)
  BOS           close beyond prior 5-bar swing        (MarketStructureDetector)
  wick reject   wick >= 2x body, on the correct side
  premium/disc  position in the trailing 50-bar range (PremiumDiscountCalculator)
  HTF bias      higher-timeframe close vs its 50-period mean
  volume spike  tick volume >= 2x trailing median
"""
from __future__ import annotations
from pathlib import Path
import numpy as np

CACHE = Path(__file__).parent / "cache"


def load(sym: str, tf: str):
    f = CACHE / f"{sym.replace(' ', '_')}__{tf}.npz"
    if not f.exists():
        return None
    z = np.load(f, allow_pickle=False)
    return {k: z[k] for k in z.files}


def atr(h, l, c, n=14):
    prev = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    out = np.full(len(tr), np.nan)
    if len(tr) < n:
        return out
    a = tr[:n].mean(); out[n - 1] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def rolling_max(x, w):
    out = np.full(len(x), np.nan)
    for i in range(w, len(x)):
        out[i] = x[i - w:i].max()
    return out


def rolling_min(x, w):
    out = np.full(len(x), np.nan)
    for i in range(w, len(x)):
        out[i] = x[i - w:i].min()
    return out


def rolling_median(x, w):
    out = np.full(len(x), np.nan)
    for i in range(w, len(x)):
        out[i] = np.median(x[i - w:i])
    return out


def build_atoms(b: dict) -> dict[str, np.ndarray]:
    """Boolean atom -> array. Each is DIRECTIONAL: suffix _up favours BUY."""
    o, h, l, c, v = b["open"], b["high"], b["low"], b["close"], b["volume"]
    a = atr(h, l, c)
    n = len(c)
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-12)
    A = {}

    # -- displacement ------------------------------------------------------
    disp = (body >= 1.0 * a) & (body / rng >= 0.60)
    A["displacement_up"] = disp & (c > o)
    A["displacement_dn"] = disp & (c < o)

    # -- FVG (3-candle gap, >= 0.2 ATR), evaluated at the bar that closes it
    fvg_up = np.zeros(n, bool); fvg_dn = np.zeros(n, bool)
    gap_up = l[2:] - h[:-2]            # bullish gap between bar i-2 and i
    gap_dn = l[:-2] - h[2:]
    thr = 0.2 * a[2:]
    fvg_up[2:] = gap_up > np.maximum(thr, 0)
    fvg_dn[2:] = gap_dn > np.maximum(thr, 0)
    A["fvg_up"] = fvg_up
    A["fvg_dn"] = fvg_dn

    # -- liquidity sweep: prior 20-bar extreme taken then closed back inside
    hi20, lo20 = rolling_max(h, 20), rolling_min(l, 20)
    A["sweep_low_up"] = (l < lo20) & (c > lo20)     # sell-side swept -> bullish
    A["sweep_high_dn"] = (h > hi20) & (c < hi20)    # buy-side swept -> bearish

    # -- break of structure: close beyond prior 5-bar swing
    hi5, lo5 = rolling_max(h, 5), rolling_min(l, 5)
    A["bos_up"] = c > hi5
    A["bos_dn"] = c < lo5

    # -- wick rejection
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    A["wick_reject_up"] = lower >= 2 * body
    A["wick_reject_dn"] = upper >= 2 * body

    # -- premium / discount within trailing 50-bar range
    hi50, lo50 = rolling_max(h, 50), rolling_min(l, 50)
    span = np.maximum(hi50 - lo50, 1e-12)
    pos = (c - lo50) / span
    A["discount_up"] = pos <= 0.382       # cheap -> favours BUY
    A["premium_dn"] = pos >= 0.618        # rich  -> favours SELL

    # -- volume spike (direction-neutral, tagged both ways)
    vmed = rolling_median(v, 50)
    vs = v >= 2 * vmed
    A["volspike_up"] = vs & (c > o)
    A["volspike_dn"] = vs & (c < o)

    # -- retest: price returns to a level broken within the last 10 bars
    ret_up = np.zeros(n, bool); ret_dn = np.zeros(n, bool)
    for i in range(20, n):
        w = slice(i - 10, i)
        if np.any(A["bos_up"][w]):
            lvl = hi5[i - 10:i][np.nonzero(A["bos_up"][w])[0][-1]]
            ret_up[i] = np.isfinite(lvl) and l[i] <= lvl <= h[i]
        if np.any(A["bos_dn"][w]):
            lvl = lo5[i - 10:i][np.nonzero(A["bos_dn"][w])[0][-1]]
            ret_dn[i] = np.isfinite(lvl) and l[i] <= lvl <= h[i]
    A["retest_up"] = ret_up
    A["retest_dn"] = ret_dn

    return A, a


def htf_bias(b_ltf: dict, b_htf: dict) -> np.ndarray:
    """+1 bullish / -1 bearish HTF bias aligned onto the lower timeframe.

    Bias = HTF close above/below its trailing 50-bar mean, using only HTF bars
    that had already CLOSED at the lower-timeframe bar's timestamp.
    """
    ht, hc = b_htf["time"], b_htf["close"]
    m = np.full(len(hc), np.nan)
    for i in range(50, len(hc)):
        m[i] = hc[i - 50:i].mean()
    sign = np.where(hc > m, 1, np.where(hc < m, -1, 0)).astype(float)
    sign[~np.isfinite(m)] = 0
    # a HTF bar at time T is only known after it closes; align conservatively
    idx = np.searchsorted(ht, b_ltf["time"], side="right") - 1
    idx = np.clip(idx - 1, 0, len(sign) - 1)
    out = sign[idx]
    out[idx < 50] = 0
    return out
