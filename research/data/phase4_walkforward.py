"""Phase 4: the index reversal effect on 19-57 years of cash-index history.

Report 22 left one question: is the intraday reversal real, or the best result of a
wide search on 2.6 years? Deriv's CFDs cannot answer it - they do not go back far
enough. Cash indices do.

`cash_vs_cfd_check.py` first established what this data can and cannot be used for.
The two series correlate 0.876 close-to-close on the S&P and 0.171 on Japan 225,
because Deriv cuts its daily bars on a server-day boundary that lines up with the
US session and slices straight through the Asian one. So cash history is the right
tool for asking **whether the phenomenon exists and persists**, and the wrong tool
for predicting CFD execution P&L on a per-day basis.

Two things are tested here, and they are different questions:

* **Persistence.** Decade by decade, does the effect survive? Short-term reversal
  is documented to have decayed as markets became more efficient and as trading
  costs fell, so a 40-year average could easily be carried entirely by the 1990s
  while being dead today. An average is the wrong statistic; the time path is the
  right one.
* **Walk-forward.** Fit the sign of the effect on data up to a cutoff, trade it
  after. This is the report 18 discipline applied properly: no parameter chosen on
  data it is then scored on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

CASH = Path(__file__).resolve().parent / "bars_cash"
COST = 0.00012          # 1.2 bp round trip, the median Deriv CFD index spread


def load(f: Path):
    z = np.load(f, allow_pickle=True)
    t, o, c = z["t"].astype(np.int64), z["o"].astype(float), z["c"].astype(float)
    cc = np.diff(np.log(c))
    oc = np.log(c / o)[1:]
    yr = np.array([datetime.fromtimestamp(int(x), timezone.utc).year for x in t[1:]])
    return t[1:], yr, cc, oc


def strat(cc, oc, cost=COST):
    """Yesterday's close-to-close sign, traded open->close today."""
    return (-np.sign(cc[:-1])) * oc[1:] - cost


def stats(x):
    if len(x) < 30:
        return np.nan, np.nan, np.nan
    se = x.std(ddof=1) / np.sqrt(len(x))
    return x.mean(), (x.mean() / se if se else np.nan), \
        x.mean() / x.std(ddof=1) * np.sqrt(252)


def main() -> None:
    files = sorted(CASH.glob("*_D1.npz"))
    data = {f.name.replace("_D1.npz", ""): load(f) for f in files}
    print(f"{len(data)} indices, {sum(len(v[2]) for v in data.values()):,} index-days\n")

    # ---------- 1. full-history, per index
    print("=" * 100)
    print("1. FULL HISTORY, per index (cost 1.2 bp/day charged)")
    print("=" * 100)
    print(f"{'index':18s}{'years':>7s}{'n':>7s}{'ACF1':>8s}{'%/day':>9s}"
          f"{'t':>8s}{'ann%':>8s}{'Sharpe':>8s}")
    for name, (t, yr, cc, oc) in sorted(data.items()):
        v = strat(cc, oc)
        m, tt, sh = stats(v)
        a1 = float(np.corrcoef(cc[:-1], cc[1:])[0, 1])
        years = (t[-1] - t[0]) / 86400 / 365.25
        print(f"{name[:17]:18s}{years:7.1f}{len(v):7,d}{a1:8.3f}{m*100:9.4f}"
              f"{tt:8.2f}{m*252*100:8.1f}{sh:8.2f}")

    # ---------- 2. decade by decade (the real question)
    print("\n" + "=" * 100)
    print("2. BY DECADE - has it decayed? (equal-weight across all indices)")
    print("=" * 100)
    decades = [(1970, 1979), (1980, 1989), (1990, 1999), (2000, 2009),
               (2010, 2019), (2020, 2026)]
    print(f"{'decade':12s}{'indices':>9s}{'n':>9s}{'ACF1':>9s}{'%/day':>10s}"
          f"{'t':>8s}{'ann%':>9s}{'Sharpe':>8s}")
    for lo, hi in decades:
        pool, acfs, nidx = [], [], 0
        for name, (t, yr, cc, oc) in data.items():
            sel = (yr[1:] >= lo) & (yr[1:] <= hi)
            if sel.sum() < 200:
                continue
            nidx += 1
            pool.append(strat(cc, oc)[sel])
            c2 = cc[1:][sel]
            c1 = cc[:-1][sel]
            if len(c1) > 30:
                acfs.append(float(np.corrcoef(c1, c2)[0, 1]))
        if not pool:
            continue
        v = np.concatenate(pool)
        m, tt, sh = stats(v)
        print(f"{f'{lo}-{hi}':12s}{nidx:9d}{len(v):9,d}{np.mean(acfs):9.3f}"
              f"{m*100:10.4f}{tt:8.2f}{m*252*100:9.1f}{sh:8.2f}")

    # ---------- 3. walk-forward
    print("\n" + "=" * 100)
    print("3. WALK-FORWARD - sign fitted before the cutoff, traded after")
    print("=" * 100)
    print(f"{'cutoff':10s}{'train n':>10s}{'train ACF1':>12s}{'test n':>9s}"
          f"{'test %/day':>12s}{'test t':>9s}{'test ann%':>11s}")
    for cut in (1990, 1995, 2000, 2005, 2010, 2015, 2020):
        tr, te, acfs = [], [], []
        for name, (t, yr, cc, oc) in data.items():
            y = yr[1:]
            a = y < cut
            b = y >= cut
            if a.sum() < 500 or b.sum() < 250:
                continue
            c1, c2 = cc[:-1], cc[1:]
            if a.sum() > 30:
                acfs.append(float(np.corrcoef(c1[a], c2[a])[0, 1]))
            tr.append(strat(cc, oc)[a])
            te.append(strat(cc, oc)[b])
        if not te:
            continue
        vtr, vte = np.concatenate(tr), np.concatenate(te)
        mtr = np.mean(acfs)
        # only trade the effect if the training period says it is there
        direction = -1.0 if mtr < 0 else 1.0
        vte = vte * (1.0 if direction < 0 else -1.0)
        m, tt, sh = stats(vte)
        print(f"{cut:<10d}{len(vtr):>10,d}{mtr:12.3f}{len(vte):9,d}"
              f"{m*100:12.4f}{tt:9.2f}{m*252*100:11.1f}")


if __name__ == "__main__":
    main()
