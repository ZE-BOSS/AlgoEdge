"""Quant lab: published, fixed-rule strategies for gold and US indices on Deriv data.

    python3 quant_lab.py <qdata dir> [--json out.json]

No parameter is tuned here: every rule and threshold is the one its source uses,
so the whole history is out-of-sample for the rule. Costs: Deriv median spread
x 1.5 per round trip, plus 0.02% financing per night held (CFD swap, conservative).

Returns are in % of notional at 1x exposure (no leverage, no compounding);
ORB-5 is reported at 1% risk per trade. One trade per row; daily P&L is booked
on the exit day.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
NY = "America/New_York"
FIN_PER_NIGHT = 0.0002
SPREAD = {"XAUUSD": 0.15, "US_Tech_100": 0.70, "US_SP_500": 0.31, "Wall_Street_30": 1.30}  # price units, Deriv M5 median
SLIP = 1.5


def load(d: Path, sym: str, tf: str) -> pd.DataFrame:
    df = pd.read_csv(d / f"{sym}_{tf}.csv.gz")
    df["ts"] = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    df["ny"] = df["ts"].dt.tz_convert(NY)
    return df.reset_index(drop=True)


def cost(sym: str, price: float) -> float:
    return SPREAD[sym] * SLIP / price


def nights(t0: pd.Timestamp, t1: pd.Timestamp) -> int:
    return max(0, (t1.normalize() - t0.normalize()).days)


# ── daily bars ────────────────────────────────────────────────────────────────
def cash_days(m30: pd.DataFrame) -> pd.DataFrame:
    """US cash session 9:30-16:00 NY from M30 bars (open-time stamped)."""
    m = m30.copy()
    hm = m.ny.dt.hour * 60 + m.ny.dt.minute
    m = m[(hm >= 570) & (hm < 960)]
    m["d"] = m.ny.dt.date
    g = m.groupby("d")
    out = pd.DataFrame({
        "O": g.open.first(), "H": g.high.max(), "L": g.low.min(), "C": g.close.last(),
        "n": g.size(), "t_close": g.ny.last() + pd.Timedelta(minutes=30), "t_open": g.ny.first(),
    })
    p1530 = m[hm.loc[m.index] == 930].groupby("d").open.first()
    out["P1530"] = p1530
    out = out[out.n >= 12]                          # full sessions only (13 bars; allow 1 missing)
    return out.reset_index()


def ny_days(m30: pd.DataFrame) -> pd.DataFrame:
    """FX-style day for gold: 17:00 NY to 17:00 NY."""
    m = m30.copy()
    m["d"] = (m.ny - pd.Timedelta(hours=17)).dt.date
    g = m.groupby("d")
    out = pd.DataFrame({"O": g.open.first(), "H": g.high.max(), "L": g.low.min(), "C": g.close.last(),
                        "n": g.size(), "t_close": g.ny.last() + pd.Timedelta(minutes=30)})
    out = out[out.n >= 30]
    return out.reset_index()


def rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ── strategies (each returns a list of trades: dict(t_exit, ret, side)) ─────────
def s_ibs(D, sym, trend=False, thr=0.2):
    ibs = (D.C - D.L) / (D.H - D.L)
    sma = D.C.rolling(200).mean()
    tr = []
    for i in range(len(D) - 1):
        if not (ibs.iat[i] < thr):
            continue
        if trend and not (D.C.iat[i] > sma.iat[i]):
            continue
        r = D.C.iat[i + 1] / D.C.iat[i] - 1 - cost(sym, D.C.iat[i]) - FIN_PER_NIGHT * nights(D.t_close.iat[i], D.t_close.iat[i + 1])
        tr.append({"t": D.t_close.iat[i + 1], "ret": r, "side": 1, "t0": D.t_close.iat[i]})
    return tr


def s_trend_hold(D, sym, n=200):
    """Time-series trend: hold long while the close is above its n-day average (one trade per run)."""
    sma = D.C.rolling(n).mean()
    tr, i = [], n
    while i < len(D) - 1:
        if D.C.iat[i] > sma.iat[i]:
            j = i + 1
            while j < len(D) - 1 and D.C.iat[j] > sma.iat[j]:
                j += 1
            r = D.C.iat[j] / D.C.iat[i] - 1 - cost(sym, D.C.iat[i]) - FIN_PER_NIGHT * nights(D.t_close.iat[i], D.t_close.iat[j])
            tr.append({"t": D.t_close.iat[j], "ret": r, "side": 1, "t0": D.t_close.iat[i]})
            i = j + 1
        else:
            i += 1
    return tr


def s_rsi2(D, sym):
    r2 = rsi(D.C, 2)
    sma200, sma5 = D.C.rolling(200).mean(), D.C.rolling(5).mean()
    tr, i = [], 0
    while i < len(D) - 1:
        if r2.iat[i] < 10 and D.C.iat[i] > sma200.iat[i]:
            j = i + 1
            while j < len(D) - 1 and not (D.C.iat[j] > sma5.iat[j]) and j - i < 10:
                j += 1
            r = D.C.iat[j] / D.C.iat[i] - 1 - cost(sym, D.C.iat[i]) - FIN_PER_NIGHT * nights(D.t_close.iat[i], D.t_close.iat[j])
            tr.append({"t": D.t_close.iat[j], "ret": r, "side": 1, "t0": D.t_close.iat[i]})
            i = j + 1
        else:
            i += 1
    return tr


def s_tom(D, sym):
    """McConnell-Xu: hold from the close before the last trading day to the close of trading day +3."""
    d = pd.to_datetime(D.d)
    ym = d.dt.to_period("M")
    tr = []
    idx = np.arange(len(D))
    for p in ym.unique():
        pos = idx[(ym == p).values]
        if len(pos) < 3:
            continue
        entry = pos[-2]                     # close of the day before the last trading day
        exit_ = entry + 4                   # last day, +1, +2, +3
        if exit_ >= len(D):
            continue
        r = D.C.iat[exit_] / D.C.iat[entry] - 1 - cost(sym, D.C.iat[entry]) - FIN_PER_NIGHT * nights(D.t_close.iat[entry], D.t_close.iat[exit_])
        tr.append({"t": D.t_close.iat[exit_], "ret": r, "side": 1, "t0": D.t_close.iat[entry]})
    return tr


def s_last_half_hour(D, sym):
    """Gao-Han-Li-Zhou / Baltussen et al: sign of prior close -> 15:30 return, held 15:30 -> 16:00."""
    tr = []
    for i in range(1, len(D)):
        p, c0, c = D.P1530.iat[i], D.C.iat[i - 1], D.C.iat[i]
        if not (p > 0 and c0 > 0) or p == c0:
            continue
        side = 1 if p > c0 else -1
        r = side * (c / p - 1) - cost(sym, p)
        tr.append({"t": D.t_close.iat[i], "ret": r, "side": side, "t0": D.t_close.iat[i] - pd.Timedelta(minutes=30)})
    return tr


def s_overnight(D, sym):
    tr = []
    for i in range(len(D) - 1):
        r = D.O.iat[i + 1] / D.C.iat[i] - 1 - cost(sym, D.C.iat[i]) - FIN_PER_NIGHT * nights(D.t_close.iat[i], D.t_open.iat[i + 1])
        tr.append({"t": D.t_open.iat[i + 1], "ret": r, "side": 1, "t0": D.t_close.iat[i]})
    return tr


def _at(m30, hh, mm):
    """price at NY hh:mm each NY date (open of the M30 bar starting then)."""
    x = m30[(m30.ny.dt.hour == hh) & (m30.ny.dt.minute == mm)]
    return pd.Series(x.open.values, index=x.ny.dt.date.values), pd.Series(x.ny.values, index=x.ny.dt.date.values)


def s_gold_window(m30, sym, h0, m0, h1, m1, side=1, overnight=True):
    """Hold gold from NY h0:m0 to h1:m1 (next trading day when overnight)."""
    p0, t0 = _at(m30, h0, m0)
    p1, t1 = _at(m30, h1, m1)
    p0 = p0[~p0.index.duplicated()]
    t0 = t0[~t0.index.duplicated()]
    p1 = p1[~p1.index.duplicated()]
    t1 = t1[~t1.index.duplicated()]
    days1 = list(p1.index)
    tr = []
    k = 0
    for d in p0.index:
        if overnight:
            while k < len(days1) and days1[k] <= d:
                k += 1
            if k >= len(days1):
                break
            e = days1[k]
            if (pd.Timestamp(e) - pd.Timestamp(d)).days > 4:
                continue
        else:
            e = d
            if e not in p1.index:
                continue
        a, b = p0[d], p1[e]
        ta, tb = pd.Timestamp(t0[d]), pd.Timestamp(t1[e])
        if tb <= ta:
            continue
        r = side * (b / a - 1) - cost(sym, a) - FIN_PER_NIGHT * nights(ta, tb)
        tr.append({"t": tb, "ret": r, "side": side, "t0": ta})
    return tr


def s_london_breakout(m30, sym):
    """Asian range 19:00-02:00 NY; first break 02:00-07:00 NY; stop = other side, target 1.5x range, exit 12:00 NY."""
    m = m30.copy()
    m["sd"] = (m.ny + pd.Timedelta(hours=5)).dt.date     # session day starting 19:00 NY
    hm = m.ny.dt.hour * 60 + m.ny.dt.minute
    tr = []
    for sd, g in m.groupby("sd"):
        h = (g.ny.dt.hour * 60 + g.ny.dt.minute).values
        asia = g[(h >= 19 * 60) | (h < 120)]
        if len(asia) < 10:
            continue
        hi, lo = asia.high.max(), asia.low.min()
        rng = hi - lo
        if rng <= 0:
            continue
        win = g[(h >= 120) & (h < 420)]
        side, entry, t0 = 0, None, None
        for _, b in win.iterrows():
            if b.high > hi and b.low < lo:
                break                                     # both sides in one bar: skip day
            if b.high > hi:
                side, entry, t0 = 1, hi, b.ny
                break
            if b.low < lo:
                side, entry, t0 = -1, lo, b.ny
                break
        if not side:
            continue
        stop = lo if side == 1 else hi
        tgt = entry + side * 1.5 * rng
        after = g[(g.ny > t0) & (h_after := (g.ny.dt.hour * 60 + g.ny.dt.minute) < 720)]
        exit_p, t1 = None, None
        for _, b in after.iterrows():
            hit_s = b.low <= stop if side == 1 else b.high >= stop
            hit_t = b.high >= tgt if side == 1 else b.low <= tgt
            if hit_s:
                exit_p, t1 = stop, b.ny
                break
            if hit_t:
                exit_p, t1 = tgt, b.ny
                break
        if exit_p is None:
            if len(after) == 0:
                continue
            exit_p, t1 = after.close.iloc[-1], after.ny.iloc[-1]
        r = side * (exit_p / entry - 1) - cost(sym, entry)
        tr.append({"t": t1, "ret": r, "side": side, "t0": t0, "risk": rng / entry})
    return tr


def s_orb5(m5, sym):
    """Zarattini-Aziz 5-minute ORB: direction of the 9:30 bar, stop at its other end, 10R target, exit 16:00."""
    m = m5.copy()
    m["d"] = m.ny.dt.date
    hm = m.ny.dt.hour * 60 + m.ny.dt.minute
    tr = []
    for d, g in m[(hm >= 570) & (hm < 960)].groupby("d"):
        first = g[(g.ny.dt.hour == 9) & (g.ny.dt.minute == 30)]
        if first.empty or len(g) < 60:
            continue
        f = first.iloc[0]
        if f.close == f.open:
            continue
        side = 1 if f.close > f.open else -1
        rest = g[g.ny > f.ny]
        entry = rest.open.iloc[0]
        stop = f.low if side == 1 else f.high
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        tgt = entry + side * 10 * risk
        exit_p, t1 = rest.close.iloc[-1], rest.ny.iloc[-1] + pd.Timedelta(minutes=5)
        for _, b in rest.iterrows():
            if (side == 1 and b.low <= stop) or (side == -1 and b.high >= stop):
                exit_p, t1 = stop, b.ny
                break
            if (side == 1 and b.high >= tgt) or (side == -1 and b.low <= tgt):
                exit_p, t1 = tgt, b.ny
                break
        R = (side * (exit_p - entry) - SPREAD[sym] * SLIP) / risk
        tr.append({"t": t1, "ret": 0.01 * R, "side": side, "t0": f.ny, "R": R})   # 1% risk per trade
    return tr


# ── metrics ───────────────────────────────────────────────────────────────────
def metrics(tr, start, end):
    if not tr:
        return {"n": 0}
    df = pd.DataFrame(tr)
    df["t"] = pd.to_datetime(df.t, utc=True).dt.tz_convert(NY)
    df = df.sort_values("t")
    r = df.ret.values
    days = df.groupby(df.t.dt.date).ret.sum()
    eq = days.cumsum()
    dd = float((eq.cummax() - eq).max()) if len(eq) else 0.0
    mon = df.groupby(df.t.dt.strftime("%Y-%m")).ret.sum()
    yrs = df.groupby(df.t.dt.year).ret.sum()
    span = max((end - start).days / 365.25, 0.1)
    gw, gl = r[r > 0].sum(), -r[r < 0].sum()
    allmon = pd.period_range(start.strftime("%Y-%m"), end.strftime("%Y-%m"), freq="M").astype(str)
    monf = mon.reindex(allmon, fill_value=0.0)
    last12 = df[df.t >= (end - pd.Timedelta(days=365))].ret.sum()
    dly = days.reindex(pd.date_range(start.date(), end.date(), freq="B").date, fill_value=0.0)
    sharpe = float(dly.mean() / dly.std() * math.sqrt(252)) if dly.std() > 0 else 0.0
    expo = None
    if "t0" in df:
        t0 = pd.to_datetime(df.t0, utc=True)
        expo = float(((df.t.dt.tz_convert("UTC") - t0).dt.total_seconds().sum()) / ((end - start).total_seconds()))
    return {"n": int(len(r)), "win": float((r > 0).mean()), "avg_bps": float(r.mean() * 1e4),
            "pf": float(gw / gl) if gl > 0 else float("inf"), "total": float(r.sum()), "ann": float(r.sum() / span),
            "dd": dd, "mon_pos": float((monf > 0).mean()), "mon_n": int(len(monf)), "worst_m": float(monf.min()),
            "best_m": float(monf.max()), "sharpe": sharpe, "last12": float(last12), "exposure": expo,
            "years": {int(k): float(v) for k, v in yrs.items()}, "monthly": {k: float(v) for k, v in monf.items()}}


def bh(series_close, t):
    return float(series_close.iloc[-1] / series_close.iloc[0] - 1)


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/algoedge/research_out/qdata")
    out = {}

    def run(name, tr, start, end, extra=None):
        m = metrics(tr, start, end)
        if extra:
            m.update(extra)
        out[name] = {**m, "trades": [{"t": str(x["t"]), "ret": x["ret"], "side": x["side"]} for x in tr]}
        if m["n"] == 0:
            print(f"{name:34s} no trades")
            return
        yrs = " ".join(f"{k}:{v * 100:+.1f}" for k, v in m["years"].items())
        print(f"{name:34s} n{m['n']:4d} win{m['win'] * 100:3.0f}% avg{m['avg_bps']:+6.1f}bp PF{min(m['pf'], 99):5.2f} "
              f"ann{m['ann'] * 100:+6.1f}% DD{m['dd'] * 100:5.1f}% mon+{m['mon_pos'] * 100:3.0f}% "
              f"worstM{m['worst_m'] * 100:+5.1f}% SR{m['sharpe']:5.2f} L12{m['last12'] * 100:+6.1f}% "
              f"exp{(m['exposure'] or 0) * 100:3.0f}% | {yrs}", flush=True)

    for sym in ("US_Tech_100", "US_SP_500", "Wall_Street_30"):
        m30 = load(d, sym, "M30")
        D = cash_days(m30)
        s, e = D.t_open.iat[0], D.t_close.iat[-1]
        print(f"== {sym} cash days {len(D)} {s:%Y-%m-%d}..{e:%Y-%m-%d} buy&hold {bh(D.C, None) * 100:+.1f}%")
        run(f"{sym}|IBS<0.2 (next close)", s_ibs(D, sym), s, e)
        run(f"{sym}|IBS<0.2 + >SMA200", s_ibs(D, sym, trend=True), s, e)
        for thr in (0.1, 0.15, 0.25, 0.3):
            run(f"{sym}|grid IBS<{thr} + >SMA200", s_ibs(D, sym, trend=True, thr=thr), s, e)
        run(f"{sym}|RSI2<10 >SMA200 exit>SMA5", s_rsi2(D, sym), s, e)
        run(f"{sym}|Turn of month (-2..+3)", s_tom(D, sym), s, e)
        run(f"{sym}|Last half-hour momentum", s_last_half_hour(D, sym), s, e)
        run(f"{sym}|Overnight close->open", s_overnight(D, sym), s, e)
        m5 = load(d, sym, "M5")
        s5, e5 = m5.ny.iat[0], m5.ny.iat[-1]
        run(f"{sym}|ORB-5 10R (1% risk)", s_orb5(m5, sym), s5, e5)

    g30 = load(d, "XAUUSD", "M30")
    G = ny_days(g30)
    s, e = g30.ny.iat[0], g30.ny.iat[-1]
    print(f"== XAUUSD NY days {len(G)} {s:%Y-%m-%d}..{e:%Y-%m-%d} buy&hold {bh(G.C, None) * 100:+.1f}%")
    run("XAUUSD|IBS<0.2 (next close)", s_ibs(G, "XAUUSD"), s, e)
    run("XAUUSD|RSI2<10 >SMA200 exit>SMA5", s_rsi2(G, "XAUUSD"), s, e)
    run("XAUUSD|IBS<0.2 + >SMA200", s_ibs(G, "XAUUSD", trend=True), s, e)
    run("XAUUSD|Trend hold >SMA200", s_trend_hold(G, "XAUUSD"), s, e)
    run("XAUUSD|Overnight long 13:30->08:30", s_gold_window(g30, "XAUUSD", 13, 30, 8, 30, 1, True), s, e)
    run("XAUUSD|Day short 08:30->13:30", s_gold_window(g30, "XAUUSD", 8, 30, 13, 30, -1, False), s, e)
    run("XAUUSD|Asia long 19:00->02:00", s_gold_window(g30, "XAUUSD", 19, 0, 2, 0, 1, True), s, e)
    run("XAUUSD|London breakout 1.5R", s_london_breakout(g30, "XAUUSD"), s, e)

    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, default=str))
    print("LAB_DONE")


if __name__ == "__main__":
    main()
