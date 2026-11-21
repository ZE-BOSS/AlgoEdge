"""Quant lab round 2: more published, fixed-rule strategies (no tuning).

    python3 quant_lab2.py <qdata dir> [--json out.json]

Indices (US cash-session daily bars from M30, Jan 2024+):
  Connors Double 7s; Connors multiple-days-down; Connors cumulative RSI;
  Crabel NR7 breakout; gap-down fade; IBS with an exit at the next open.
Gold (NY-day bars from M30 2018+, H4 from H1 2014+):
  time-series momentum, vol-targeted (Moskowitz-Ooi-Pedersen 2012);
  Double 7s / cumulative RSI with the 200-day filter; Friday-long / Tuesday-short
  weekday effect; H4 EMA20/50 trend with ADX(14) > 25.
Same costs and reporting as quant_lab.py.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quant_lab as L  # noqa: E402

warnings.filterwarnings("ignore")


def _trade(D, sym, i, j, side=1):
    a, b = D.C.iat[i], D.C.iat[j]
    r = side * (b / a - 1) - L.cost(sym, a) - L.FIN_PER_NIGHT * L.nights(D.t_close.iat[i], D.t_close.iat[j])
    return {"t": D.t_close.iat[j], "ret": r, "side": side, "t0": D.t_close.iat[i]}


def s_double7(D, sym):
    """Connors: close > SMA200 and close = 7-day low -> buy; exit at a close that is the 7-day high."""
    sma = D.C.rolling(200).mean()
    lo7, hi7 = D.C.rolling(7).min(), D.C.rolling(7).max()
    tr, i = [], 0
    while i < len(D) - 1:
        if D.C.iat[i] > sma.iat[i] and D.C.iat[i] <= lo7.iat[i]:
            j = i + 1
            while j < len(D) - 1 and D.C.iat[j] < hi7.iat[j]:
                j += 1
            tr.append(_trade(D, sym, i, j))
            i = j + 1
        else:
            i += 1
    return tr


def s_days_down(D, sym, n=3):
    """Connors multiple days down: close > SMA200 and n lower closes in a row -> buy; exit close > SMA5."""
    sma200, sma5 = D.C.rolling(200).mean(), D.C.rolling(5).mean()
    down = (D.C.diff() < 0).astype(int).rolling(n).sum() == n
    tr, i = [], 0
    while i < len(D) - 1:
        if D.C.iat[i] > sma200.iat[i] and down.iat[i]:
            j = i + 1
            while j < len(D) - 1 and not (D.C.iat[j] > sma5.iat[j]) and j - i < 10:
                j += 1
            tr.append(_trade(D, sym, i, j))
            i = j + 1
        else:
            i += 1
    return tr


def s_cum_rsi(D, sym):
    """Connors cumulative RSI: 2-day sum of RSI(2) < 35 and close > SMA200 -> buy; exit when RSI(2) > 65."""
    r2 = L.rsi(D.C, 2)
    cum = r2.rolling(2).sum()
    sma = D.C.rolling(200).mean()
    tr, i = [], 0
    while i < len(D) - 1:
        if cum.iat[i] < 35 and D.C.iat[i] > sma.iat[i]:
            j = i + 1
            while j < len(D) - 1 and not (r2.iat[j] > 65) and j - i < 10:
                j += 1
            tr.append(_trade(D, sym, i, j))
            i = j + 1
        else:
            i += 1
    return tr


def s_nr7(D, m30, sym):
    """Crabel NR7: after the narrowest range of 7 sessions, take the FIRST break of its high (long) or low
    (short) in the next cash session, found bar by bar on M30 (no look-ahead), exit at that session's close.
    A bar that breaks both sides is skipped (order unknown)."""
    rng = D.H - D.L
    nr7 = rng <= rng.rolling(7).min()
    m = m30.copy()
    hm = m.ny.dt.hour * 60 + m.ny.dt.minute
    m = m[(hm >= 570) & (hm < 960)]
    by_day = {k: g for k, g in m.groupby(m.ny.dt.date)}
    tr = []
    for i in range(7, len(D) - 1):
        if not nr7.iat[i]:
            continue
        hi, lo = D.H.iat[i], D.L.iat[i]
        g = by_day.get(D.d.iat[i + 1])
        if g is None:
            continue
        side = entry = t0 = None
        for _, b in g.iterrows():
            up, dn = b.high > hi, b.low < lo
            if up and dn:
                break
            if up or dn:
                # a session that opens beyond the level fills at the open, not at the level
                side, entry, t0 = (1, max(hi, b.open), b.ny) if up else (-1, min(lo, b.open), b.ny)
                break
        if side is None:
            continue
        C = D.C.iat[i + 1]
        r = side * (C / entry - 1) - L.cost(sym, entry)
        tr.append({"t": D.t_close.iat[i + 1], "ret": r, "side": side, "t0": t0})
    return tr


def s_gap_fade(D, sym):
    """Gap down below the prior session's low: buy the open, exit at the close (gap-fill tendency)."""
    tr = []
    for i in range(1, len(D)):
        if D.O.iat[i] < D.L.iat[i - 1]:
            r = D.C.iat[i] / D.O.iat[i] - 1 - L.cost(sym, D.O.iat[i])
            tr.append({"t": D.t_close.iat[i], "ret": r, "side": 1, "t0": D.t_open.iat[i]})
    return tr


def s_ibs_open_exit(D, sym, trend=True):
    """IBS < 0.2 (+ SMA200): buy the close, sell the NEXT OPEN (keeps the overnight part only)."""
    ibs = (D.C - D.L) / (D.H - D.L)
    sma = D.C.rolling(200).mean()
    tr = []
    for i in range(len(D) - 1):
        if ibs.iat[i] < 0.2 and (not trend or D.C.iat[i] > sma.iat[i]):
            r = D.O.iat[i + 1] / D.C.iat[i] - 1 - L.cost(sym, D.C.iat[i]) - L.FIN_PER_NIGHT * L.nights(D.t_close.iat[i], D.t_open.iat[i + 1])
            tr.append({"t": D.t_open.iat[i + 1], "ret": r, "side": 1, "t0": D.t_close.iat[i]})
    return tr


# ── gold ──────────────────────────────────────────────────────────────────────
def s_tsmom_vol(G, sym, look=252, vol_n=60, target=0.10, cap=2.0):
    """Moskowitz-Ooi-Pedersen time-series momentum: sign of the 12-month return, scaled to 10% annual
    volatility (capped at 2x), held day to day. One 'trade' per day of exposure."""
    r = G.C.pct_change()
    sig = np.sign(G.C / G.C.shift(look) - 1)
    vol = r.rolling(vol_n).std() * np.sqrt(252)
    w = (target / vol).clip(upper=cap) * sig
    tr = []
    prev_w = 0.0
    for i in range(look + 1, len(G)):
        wi = w.iat[i - 1]
        if not np.isfinite(wi) or wi == 0:
            prev_w = 0.0
            continue
        turn = abs(wi - prev_w)
        ret = wi * r.iat[i] - turn * L.cost(sym, G.C.iat[i - 1]) - abs(wi) * L.FIN_PER_NIGHT * L.nights(G.t_close.iat[i - 1], G.t_close.iat[i])
        tr.append({"t": G.t_close.iat[i], "ret": ret, "side": int(np.sign(wi)), "t0": G.t_close.iat[i - 1]})
        prev_w = wi
    return tr


def s_weekday(G, sym, day, side):
    """Gold weekday effect: hold the NY-day ending on `day` (Mon=0) close-to-close."""
    tr = []
    for i in range(1, len(G)):
        if pd.Timestamp(G.d.iat[i]).weekday() == day:
            tr.append(_trade(G, sym, i - 1, i, side))
    return tr


def h4_from_h1(h1):
    x = h1.set_index("ts")[["open", "high", "low", "close"]].resample("4h", offset="1h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    x = x.reset_index()
    x["ny"] = x.ts.dt.tz_convert(L.NY)
    return x


def adx(h, lo, c, n=14):
    up = h.diff()
    dn = -lo.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h - lo, np.maximum((h - c.shift()).abs(), (lo - c.shift()).abs()))
    atr = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm).ewm(alpha=1 / n, adjust=False).mean() / atr
    ndi = 100 * pd.Series(ndm).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().values


def s_h4_trend(h4, sym):
    """H4 EMA20/50 cross in the cross's direction when ADX(14) > 25; exit on the opposite cross."""
    c = h4.close.reset_index(drop=True)
    e20, e50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    ax = adx(h4.high.reset_index(drop=True), h4.low.reset_index(drop=True), c)
    tr, side, ent, t0 = [], 0, None, None
    for i in range(51, len(c)):
        cross_up = e20[i] > e50[i] and e20[i - 1] <= e50[i - 1]
        cross_dn = e20[i] < e50[i] and e20[i - 1] >= e50[i - 1]
        if side and ((side == 1 and cross_dn) or (side == -1 and cross_up)):
            r = side * (c[i] / ent - 1) - L.cost(sym, ent) - L.FIN_PER_NIGHT * L.nights(t0, h4.ny.iat[i])
            tr.append({"t": h4.ny.iat[i], "ret": r, "side": side, "t0": t0})
            side = 0
        if not side and ax[i] > 25 and (cross_up or cross_dn):
            side, ent, t0 = (1 if cross_up else -1), c[i], h4.ny.iat[i]
    return tr


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/algoedge/research_out/qdata")
    out = {}

    def run(name, tr, s, e):
        m = L.metrics(tr, s, e)
        out[name] = {**m, "trades": [{"t": str(x["t"]), "ret": x["ret"], "side": x["side"]} for x in tr]}
        if m["n"] == 0:
            print(f"{name:34s} no trades")
            return
        yrs = " ".join(f"{k}:{v * 100:+.1f}" for k, v in m["years"].items())
        print(f"{name:34s} n{m['n']:4d} win{m['win'] * 100:3.0f}% avg{m['avg_bps']:+6.1f}bp PF{min(m['pf'], 99):5.2f} "
              f"ann{m['ann'] * 100:+6.1f}% DD{m['dd'] * 100:5.1f}% mon+{m['mon_pos'] * 100:3.0f}% "
              f"worstM{m['worst_m'] * 100:+5.1f}% SR{m['sharpe']:5.2f} L12{m['last12'] * 100:+6.1f}% | {yrs}", flush=True)

    for sym in ("US_Tech_100", "US_SP_500", "Wall_Street_30"):
        m30 = L.load(d, sym, "M30")
        D = L.cash_days(m30)
        s, e = D.t_open.iat[0], D.t_close.iat[-1]
        print(f"== {sym}")
        run(f"{sym}|Double 7s", s_double7(D, sym), s, e)
        run(f"{sym}|3 days down >SMA200", s_days_down(D, sym), s, e)
        run(f"{sym}|Cumulative RSI2<35", s_cum_rsi(D, sym), s, e)
        run(f"{sym}|NR7 breakout", s_nr7(D, m30, sym), s, e)
        run(f"{sym}|Gap-down fade", s_gap_fade(D, sym), s, e)
        run(f"{sym}|IBS+trend exit next open", s_ibs_open_exit(D, sym), s, e)

    g30 = L.load(d, "XAUUSD", "M30")
    G = L.ny_days(g30)
    s, e = g30.ny.iat[0], g30.ny.iat[-1]
    print("== XAUUSD")
    run("XAUUSD|TSMOM 12m vol-target 10%", s_tsmom_vol(G, "XAUUSD"), s, e)
    run("XAUUSD|Double 7s", s_double7(G, "XAUUSD"), s, e)
    run("XAUUSD|Cumulative RSI2<35", s_cum_rsi(G, "XAUUSD"), s, e)
    run("XAUUSD|Friday long", s_weekday(G, "XAUUSD", 4, 1), s, e)
    run("XAUUSD|Tuesday short", s_weekday(G, "XAUUSD", 1, -1), s, e)
    h1 = L.load(d, "XAUUSD", "H1")
    h4 = h4_from_h1(h1)
    run("XAUUSD|H4 EMA20/50 + ADX>25 (2014+)", s_h4_trend(h4, "XAUUSD"), h4.ny.iat[0], h4.ny.iat[-1])
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, default=str))
    print("LAB2_DONE")


if __name__ == "__main__":
    main()
