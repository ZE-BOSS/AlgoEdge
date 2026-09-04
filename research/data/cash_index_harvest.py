"""Harvest decades of cash-index daily OHLC to settle the reversal question.

Report 22 section 4: the index intraday-reversal candidate has a validated
mechanism but only 2.6 years of history, which leaves t = 2.30 inside the
data-mining band. Thirty-plus years shrinks the standard error enough to make the
answer unambiguous either way.

Source is Yahoo's chart endpoint, which returns open and close - both are required,
since the tradeable formulation is open-to-close. Stooq was tried first and serves
a JavaScript proof-of-work bot check; that is not something to work around, so it
was abandoned rather than defeated.

**The caveat that decides whether this data is usable at all.** A cash index open
is a computed level, not a traded price: on many indices it is assembled from
staggered constituent opens, and constituents that have not yet traded contribute
their previous close. That alone can manufacture open-to-close patterns that no
one could ever have traded. Deriv's CFD open, by contrast, is a live quote. So the
long history is only informative if the two agree where they overlap, which
`cash_vs_cfd_check.py` tests before any of it is believed.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

OUT = Path(__file__).resolve().parent / "bars_cash"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# yahoo symbol -> the Deriv CFD it corresponds to
PAIRS = {
    "^GSPC":    "US_SP_500",
    "^NDX":     "US_Tech_100",
    "^GDAXI":   "Germany_40",
    "^FTSE":    "UK_100",
    "^N225":    "Japan_225",
    "^HSI":     "Hong_Kong_50",
    "^AEX":     "Netherlands_25",
    "^FCHI":    "France_40",
    "^AXJO":    "Australia_200",
    # extra breadth, no CFD counterpart on this server
    "^DJI":     "Dow_Jones",
    "^RUT":     "Russell_2000",
    "^STOXX50E": "Euro_Stoxx_50",
    "^IBEX":    "Spain_35",
    "^SSMI":    "Swiss_20",
    "^KS11":    "Korea_KOSPI",
    "^TWII":    "Taiwan_Weighted",
    "^BSESN":   "India_Sensex",
    "^BVSP":    "Brazil_Bovespa",
}


def fetch(sym: str):
    u = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
         f"{sym.replace('^', '%5E')}?period1=0&period2=9999999999&interval=1d")
    r = requests.get(u, timeout=40, headers=HEADERS)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    d = r.json().get("chart", {}).get("result")
    if not d:
        return None, "no result"
    d = d[0]
    q = d["indicators"]["quote"][0]
    t = np.asarray(d["timestamp"], dtype=np.int64)
    o = np.asarray(q["open"], dtype=object)
    c = np.asarray(q["close"], dtype=object)
    h = np.asarray(q["high"], dtype=object)
    lo = np.asarray(q["low"], dtype=object)

    def clean(a):
        return np.asarray([np.nan if x is None else float(x) for x in a])

    o, c, h, lo = clean(o), clean(c), clean(h), clean(lo)
    ok = np.isfinite(o) & np.isfinite(c) & (o > 0) & (c > 0)
    return (t[ok], o[ok], h[ok], lo[ok], c[ok]), None


def main() -> None:
    OUT.mkdir(exist_ok=True)
    print(f"{'yahoo':12s}{'name':18s}{'bars':>8s}{'from':>13s}{'to':>13s}{'years':>7s}")
    for sym, name in PAIRS.items():
        f = OUT / f"{name}_D1.npz"
        if f.exists():
            z = np.load(f)
            print(f"{sym:12s}{name:18s}{len(z['t']):8,d}   (cached)")
            continue
        try:
            data, err = fetch(sym)
        except Exception as e:
            data, err = None, f"{type(e).__name__}"
        if data is None:
            print(f"{sym:12s}{name:18s}  FAILED: {err}", flush=True)
            time.sleep(1.5)
            continue
        t, o, h, lo, c = data
        a = datetime.fromtimestamp(int(t[0]), timezone.utc)
        b = datetime.fromtimestamp(int(t[-1]), timezone.utc)
        np.savez_compressed(f, t=t, o=o, h=h, l=lo, c=c,
                            meta=np.array([sym, name], dtype=object))
        print(f"{sym:12s}{name:18s}{len(t):8,d}{a.strftime('%Y-%m-%d'):>13s}"
              f"{b.strftime('%Y-%m-%d'):>13s}{(b - a).days / 365.25:7.1f}", flush=True)
        time.sleep(1.5)          # be polite to a free endpoint


if __name__ == "__main__":
    main()
