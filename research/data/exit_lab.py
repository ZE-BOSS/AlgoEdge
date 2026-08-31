"""Exit Lab — tests R-based ratchet trailing against fixed TP and partials.

Built for the proposal: forget ATR trailing, use a STIPULATED LADDER of
(trigger_R -> stop_R) steps. Price reaches 1R, stop jumps to entry+buffer; reaches
2R, stop jumps to 1R; and so on to the target. Rigid, systematic, no ATR
sensitivity to volatility regime.

Everything is simulated on the real bar path from MT5 (not on MFE summaries), so
the ratchet is evaluated the way it would actually behave intrabar.

Conservative intrabar rule: if a bar could have hit both the trailing stop and
the target, the STOP is taken. This never flatters the ratchet.

Also measures the Hurst exponent per symbol, because the literature is clear that
trailing only beats a fixed target in trending regimes (H > ~0.55) and is
actively negative in mean-reverting ones. That predicts WHICH symbols should
trail, rather than assuming all of them should.
"""
from __future__ import annotations
import os
import numpy as np
from pathlib import Path

# Cache directory is overridable so the same analysis code runs against either
# broker: ALGOEDGE_CACHE=cache_fn for FundedNext, default "cache" for Deriv.
CACHE = Path(__file__).parent / os.environ.get("ALGOEDGE_CACHE", "cache")

# ---- ladders -------------------------------------------------------------
# (trigger in R, stop moves to this R). Must be ascending.
LADDERS = {
    "fixed_TP_only":      [],
    "BE_at_1R":           [(1.0, 0.05)],
    "user_ladder":        [(1.0, 0.05), (1.5, 0.30), (2.0, 1.00),
                           (3.0, 2.00), (4.0, 3.00)],
    "loose_ladder":       [(1.5, 0.05), (2.5, 1.00), (3.5, 2.00)],
    "tight_ladder":       [(1.0, 0.05), (1.5, 0.75), (2.0, 1.50),
                           (2.5, 2.00), (3.0, 2.50), (4.0, 3.50)],
    "step_1R_behind":     [(2.0, 1.00), (3.0, 2.00), (4.0, 3.00), (5.0, 4.00)],
}


def load(sym, tf="M15"):
    f = CACHE / f"{sym.replace(' ', '_')}__{tf}.npz"
    if not f.exists():
        return None
    z = np.load(f)
    return {k: z[k].astype(float) for k in ("open", "high", "low", "close")} | {
        "time": z["time"].astype(np.int64),
        "point": float(z["point"]), "spread": float(z["spread"]),
    }


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


def hurst(series, q=(2, 4, 8, 16, 32, 64)):
    """Trend persistence via the variance ratio, expressed on the Hurst scale.

    VR(q) = Var(q-period return) / (q * Var(1-period return)). A random walk
    gives VR = 1; VR > 1 means moves persist (trending), VR < 1 means they
    reverse (mean-reverting). Regressing log VR on log q gives 2H - 1, so
    H = (slope + 1) / 2 and H = 0.5 is a random walk.

    Replaces an earlier rescaled-range implementation that returned ~0.000 for
    every symbol - the aggregation was wrong, not the data.
    """
    lr = np.diff(np.log(series[np.isfinite(series) & (series > 0)]))
    lr = lr[np.isfinite(lr)]
    if len(lr) < 5000:
        return np.nan
    v1 = np.var(lr, ddof=1)
    if v1 <= 0:
        return np.nan
    xs, ys = [], []
    for k in q:
        m = (len(lr) // k) * k
        agg = lr[:m].reshape(-1, k).sum(axis=1)
        vr = np.var(agg, ddof=1) / (k * v1)
        if vr > 0:
            xs.append(np.log(k)); ys.append(np.log(vr))
    if len(xs) < 4:
        return np.nan
    slope = float(np.polyfit(xs, ys, 1)[0])
    return (slope + 1.0) / 2.0


def simulate(bars, direction, stop_mult, target_r, ladder, horizon=250,
             stride=3, cost_r=0.0):
    """Vectorised path simulation of a ratchet ladder.

    Returns realised R per entry (entries taken every `stride` bars).
    """
    h, l, c = bars["high"], bars["low"], bars["close"]
    a = atr(h, l, c)
    n = len(c)
    idx = np.arange(200, n - horizon, stride)
    if len(idx) == 0:
        return np.array([])

    entry = c[idx]
    risk = stop_mult * a[idx]
    ok = np.isfinite(risk) & (risk > 0)
    idx, entry, risk = idx[ok], entry[ok], risk[ok]
    if len(idx) == 0:
        return np.array([])

    buy = direction == "BUY"
    stop_r = np.full(len(idx), -1.0)      # current stop, in R
    max_r = np.zeros(len(idx))
    done = np.zeros(len(idx), dtype=bool)
    out = np.zeros(len(idx))

    trig = np.array([t for t, _ in ladder]) if ladder else np.array([])
    dest = np.array([d for _, d in ladder]) if ladder else np.array([])

    for step in range(1, horizon + 1):
        j = idx + step
        fh, fl = h[j], l[j]
        if buy:
            fav = (fh - entry) / risk
            adv = (fl - entry) / risk
        else:
            fav = (entry - fl) / risk
            adv = (entry - fh) / risk

        live = ~done
        # stop first (conservative): a bar that touched both is a stop
        hit_stop = live & (adv <= stop_r)
        out = np.where(hit_stop, stop_r - cost_r, out)
        done |= hit_stop

        live = ~done
        hit_tp = live & (fav >= target_r)
        out = np.where(hit_tp, target_r - cost_r, out)
        done |= hit_tp

        live = ~done
        max_r = np.where(live, np.maximum(max_r, fav), max_r)
        if len(trig):
            # highest ladder step whose trigger has been reached
            reached = (max_r[:, None] >= trig[None, :])
            best = np.where(reached.any(axis=1),
                            dest[np.argmax(np.where(reached, trig, -np.inf), axis=1)],
                            -np.inf)
            stop_r = np.where(live, np.maximum(stop_r, best), stop_r)
        if done.all():
            break

    # Never resolved inside the horizon: mark at the LAST CLOSE, not at the
    # best excursion. Marking to max_r credits a peak the trade never banked -
    # the same high-water-mark error that plan rule 0.5-1 exists to prevent, and
    # it flatters whichever method resolves least often (the fixed target).
    last = c[np.clip(idx + horizon, 0, n - 1)]
    if buy:
        mtm = (last - entry) / risk
    else:
        mtm = (entry - last) / risk
    mtm = np.clip(mtm, stop_r, target_r)
    out = np.where(done, out, mtm - cost_r)
    return out


def summarise(rs):
    if len(rs) == 0:
        return None
    wins = rs[rs > 0]
    gl = -rs[rs <= 0].sum()
    eq = np.cumsum(rs)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    return {
        "n": len(rs), "exp": float(rs.mean()), "total": float(rs.sum()),
        "wr": float((rs > 0).mean()),
        "pf": float(wins.sum() / gl) if gl > 0 else float("inf"),
        "dd": dd,
        "ret_dd": float(rs.sum() / dd) if dd > 0 else float("inf"),
    }
