"""Combine quant-lab sleeves with AlgoEdge strategies on one account.

    python3 quant_portfolio.py <lab.json> <deep dir> [scale]

Sleeves and weights are fixed up front (see SLEEVES); `scale` multiplies them all
(e.g. to aim the whole book at a drawdown budget). Lab returns are fractions of
notional at 1x; AlgoEdge deep-run trades are $ on a static $10,000 at 1% risk.
Prints the common-period monthly table, drawdown, win rate and each sleeve alone.
"""
import json
import math
import sys
from pathlib import Path

import pandas as pd

LAB = json.loads(Path(sys.argv[1]).read_text())
DEEP = Path(sys.argv[2])
SCALE = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
NY = "America/New_York"

# (label, source, key, weight): lab weight = x notional; deep weight = x its 1%-risk sizing
SLEEVES = [
    ("IBS+trend US Tech 100", "lab", "US_Tech_100|IBS<0.2 + >SMA200", 2.0),
    ("IBS+trend US SP 500", "lab", "US_SP_500|IBS<0.2 + >SMA200", 2.0),
    ("IBS+trend Wall St 30", "lab", "Wall_Street_30|IBS<0.2 + >SMA200", 2.0),
    ("RSI2 US Tech 100", "lab", "US_Tech_100|RSI2<10 >SMA200 exit>SMA5", 1.0),
    ("T-Mean Reversion US Tech 100", "deep", "RQ_TMeanReversion_v1__US_Tech_100", 0.5),
    ("Sigma US Tech 100 (day)", "deep", "RQ_SigmaBreakout_v1__US_Tech_100", 1.0),
    ("VWAP_v1 gold (day)", "deep", "VWAP_v1__XAUUSD", 1.0),
]


def daily(label, src, key, w):
    if src == "lab":
        tr = LAB.get(key, {}).get("trades", [])
        s = pd.Series([x["ret"] * w for x in tr],
                      index=pd.to_datetime([x["t"] for x in tr], utc=True).tz_convert(NY).normalize().tz_localize(None))
    else:
        f = DEEP / f"{key}.json"
        if not f.exists():
            return None
        rows = json.loads(f.read_text()).get("rows", [])
        s = pd.Series([x["pnl"] / 10000.0 * w for x in rows],
                      index=pd.to_datetime([x["x"] for x in rows], unit="s", utc=True).tz_convert(NY).normalize().tz_localize(None))
    return s.groupby(level=0).sum() * SCALE


def stats(d: pd.Series, start, end):
    idx = pd.date_range(start, end, freq="B")
    d = d.reindex(idx, fill_value=0.0)
    eq = d.cumsum()
    dd = float((eq.cummax() - eq).max())
    mon = d.groupby(d.index.strftime("%Y-%m")).sum()
    yrs = (end - start).days / 365.25
    sr = float(d.mean() / d.std() * math.sqrt(252)) if d.std() > 0 else 0.0
    return {"total": float(d.sum()), "ann": float(d.sum() / yrs), "dd": dd, "mon": mon, "sr": sr,
            "pos": float((mon > 0).mean()), "worst": float(mon.min())}


ser = {}
for lab, src, key, w in SLEEVES:
    s = daily(lab, src, key, w)
    if s is not None and len(s):
        ser[lab] = s
start = max(s.index.min() for s in ser.values())
end = min(s.index.max() for s in ser.values())
start = max(start, pd.Timestamp(LAB and "2025-11-20"))       # deep runs begin 20 Nov 2025
print(f"common period {start:%Y-%m-%d} .. {end:%Y-%m-%d}  scale {SCALE}")
for k, s in ser.items():
    st = stats(s[(s.index >= start) & (s.index <= end)], start, end)
    print(f"  {k:32s} ann {st['ann'] * 100:+6.1f}%  DD {st['dd'] * 100:5.1f}%  months+ {st['pos'] * 100:3.0f}%  SR {st['sr']:4.2f}")
book = pd.concat(ser.values(), axis=1).fillna(0).sum(axis=1)
book = book[(book.index >= start) & (book.index <= end)]
st = stats(book, start, end)
print(f"BOOK ann {st['ann'] * 100:+.1f}%  total {st['total'] * 100:+.1f}%  maxDD {st['dd'] * 100:.1f}%  SR {st['sr']:.2f}  "
      f"months+ {st['pos'] * 100:.0f}%  worst month {st['worst'] * 100:+.1f}%")
print("MONTHS " + " ".join(f"{k[2:]}:{v * 100:+.1f}" for k, v in st["mon"].items()))
# correlation of sleeves (weekly)
wk = pd.concat({k: v.resample("W").sum() for k, v in ser.items()}, axis=1).fillna(0)
wk = wk[(wk.index >= start) & (wk.index <= end)]
c = wk.corr().values
n = len(c)
offs = [c[i][j] for i in range(n) for j in range(i + 1, n)]
print(f"avg weekly correlation between sleeves {sum(offs) / len(offs):.2f} (max {max(offs):.2f})")
