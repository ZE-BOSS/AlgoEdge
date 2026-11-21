"""Quant lab round 3: combined dip-buy rules that won the decades-long TradingView check.

    python3 quant_lab3.py <qdata dir> [--json out.json]

All long-only, enter at the signal day's close, exit at a later close (max 10 sessions):
  A  IBS<0.2 + SMA200, exit next close            (deployed QL_IBS_v1 baseline)
  D  3 lower closes + SMA200, exit close > SMA5
  E  IBS<0.2 + SMA200, exit close > yesterday's high
  F  any of IBS<0.2 / cumRSI2<35 / 3-down (+SMA200), exit close > SMA5
  H  Connors R3 (RSI2 falls 3 days from <60 to <10, +SMA200), exit RSI2 > 70
Gold: same rules on NY-day bars, plus the weekday test with the weekday taken
from the bar's CLOSE time (fixes round 2, which used the session start date).
Costs as quant_lab.py: spread x1.5 per round trip + financing per night held.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quant_lab as L  # noqa: E402
import quant_lab2 as L2  # noqa: E402

warnings.filterwarnings("ignore")


def features(D):
    c = D.C
    f = pd.DataFrame(index=D.index)
    f["sma200"] = c.rolling(200).mean()
    f["sma5"] = c.rolling(5).mean()
    f["ibs"] = ((c - D.L) / (D.H - D.L)).fillna(0.5)
    f["r2"] = L.rsi(c, 2)
    f["trend"] = c > f.sma200
    f["sigA"] = (f.ibs < 0.2) & f.trend
    f["sigC"] = (f.r2 + f.r2.shift(1) < 35) & f.trend
    f["sigD"] = ((c.diff() < 0).astype(int).rolling(3).sum() == 3) & f.trend
    r2 = f.r2
    f["sigR3"] = f.trend & (r2 < 10) & (r2 < r2.shift(1)) & (r2.shift(1) < r2.shift(2)) & (r2.shift(2) < 60)
    f["hi1"] = D.H.shift(1)
    return f


def hold(D, sym, sig, exit_ok, max_bars=10):
    tr, i, n = [], 0, len(D)
    while i < n - 1:
        if bool(sig.iat[i]):
            j = i + 1
            while j < n - 1 and not exit_ok(j) and j - i < max_bars:
                j += 1
            tr.append(L2._trade(D, sym, i, j))
            i = j + 1
        else:
            i += 1
    return tr


def rules(D, sym):
    f = features(D)
    C = D.C
    out = {
        "A IBS exit next close": hold(D, sym, f.sigA, lambda j: True, 1),
        "D 3down exit >SMA5": hold(D, sym, f.sigD, lambda j: C.iat[j] > f.sma5.iat[j]),
        "E IBS exit >prev high": hold(D, sym, f.sigA, lambda j: C.iat[j] > f.hi1.iat[j]),
        "F union exit >SMA5": hold(D, sym, f.sigA | f.sigC | f.sigD, lambda j: C.iat[j] > f.sma5.iat[j]),
        "H Connors R3": hold(D, sym, f.sigR3, lambda j: f.r2.iat[j] > 70),
    }
    return out


def s_weekday_fixed(G, sym, day, side):
    tr = []
    for i in range(1, len(G)):
        if pd.Timestamp(G.t_close.iat[i]).weekday() == day:
            tr.append(L2._trade(G, sym, i - 1, i, side))
    return tr


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/algoedge/research_out/qdata")
    out = {}

    def run(name, tr, s, e):
        m = L.metrics(tr, s, e)
        out[name] = {**m, "trades": [{"t": str(x["t"]), "t0": str(x.get("t0")), "ret": x["ret"], "side": x["side"]} for x in tr]}
        if m["n"] == 0:
            print(f"{name:36s} no trades")
            return
        mons = " ".join(f"{v * 100:+.1f}" for v in list(m.get("monthly", {}).values())[-12:])
        print(f"{name:36s} n{m['n']:4d} win{m['win'] * 100:3.0f}% avg{m['avg_bps']:+6.1f}bp PF{min(m['pf'], 99):5.2f} "
              f"ann{m['ann'] * 100:+6.1f}% DD{m['dd'] * 100:5.1f}% mon+{m['mon_pos'] * 100:3.0f}% "
              f"worstM{m['worst_m'] * 100:+5.1f}% SR{m['sharpe']:5.2f} L12{m['last12'] * 100:+6.1f}%", flush=True)
        if mons:
            print(f"{'':36s} last 12 months: {mons}", flush=True)

    for sym in ("US_Tech_100", "US_SP_500", "Wall_Street_30"):
        m30 = L.load(d, sym, "M30")
        D = L.cash_days(m30)
        s, e = D.t_open.iat[0], D.t_close.iat[-1]
        print(f"== {sym}")
        for k, tr in rules(D, sym).items():
            run(f"{sym}|{k}", tr, s, e)

    g30 = L.load(d, "XAUUSD", "M30")
    G = L.ny_days(g30)
    s, e = g30.ny.iat[0], g30.ny.iat[-1]
    print("== XAUUSD")
    for k, tr in rules(G, "XAUUSD").items():
        run(f"XAUUSD|{k}", tr, s, e)
    for day, nm in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri")):
        run(f"XAUUSD|{nm} long (close weekday)", s_weekday_fixed(G, "XAUUSD", day, 1), s, e)
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, default=str))
    print("LAB3_DONE")


if __name__ == "__main__":
    main()
