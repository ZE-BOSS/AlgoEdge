"""Exit Lab, run on YOUR REAL TRADES — no sampled entries anywhere.

Takes every trade the strategies actually produced (entry price, stop, direction,
timestamp from backtest_trades), finds that exact moment in the real MT5 bar
history, and walks the real path forward applying each exit ladder.

So: real strategy entries, real strategy stops, real market bars. The only thing
varied is how the trade is managed after entry.

Comparison is against `pnl_r` — what the trade actually returned under the
current rules (fixed 1:5 target, break-even at 1R).
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import numpy as np

from exit_lab import LADDERS, load

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "algoedge.db"
HORIZON = 400          # bars of M15 to follow a trade (~4 days)
TARGET = 5.0


def bar_index(times: np.ndarray, ts: int) -> int:
    i = int(np.searchsorted(times, ts, side="left"))
    return i if 0 <= i < len(times) else -1


def walk(bars, i0, entry, stop_px, buy, ladder, target_r):
    """Walk the real path from bar i0 applying a ladder. Returns realised R.

    A trade still open at the horizon is marked at the LAST CLOSE, never at its
    best excursion - crediting the peak is the high-water-mark error rule 0.5-1
    exists to stop, and it inflates whichever method resolves least often.
    """
    h, l, c, times = bars["high"], bars["low"], bars["close"], bars["time"]
    risk = abs(entry - stop_px)
    if risk <= 0:
        return None
    stop_r, max_r = -1.0, 0.0
    trig = [t for t, _ in ladder]
    dest = [d for _, d in ladder]
    end = min(i0 + HORIZON, len(h) - 1)
    for j in range(i0 + 1, end + 1):
        if buy:
            fav, adv = (h[j] - entry) / risk, (l[j] - entry) / risk
        else:
            fav, adv = (entry - l[j]) / risk, (entry - h[j]) / risk
        if adv <= stop_r:            # stop first — conservative
            return stop_r
        if fav >= target_r:
            return target_r
        max_r = max(max_r, fav)
        for t, d in zip(trig, dest):
            if max_r >= t:
                stop_r = max(stop_r, d)
    last = (c[end] - entry) / risk if buy else (entry - c[end]) / risk
    return float(min(max(last, stop_r), target_r))


def main():
    con = sqlite3.connect(DB)
    rows = con.execute("""
        select symbol, direction, entry_price, stop_loss, entry_time,
               pnl_r, strategy_id
        from backtest_trades
        where entry_price is not null and stop_loss is not null
    """).fetchall()
    print(f"real trades loaded: {len(rows):,}\n")

    cache, results = {}, {}
    actual = []
    skipped = 0
    for sym, direction, entry, stop, et, pnl_r, sid in rows:
        if sym not in cache:
            cache[sym] = load(sym, "M15")
        b = cache[sym]
        if b is None:
            skipped += 1
            continue
        ts = int(np.datetime64(str(et).replace(" ", "T")[:19]).astype("datetime64[s]").astype(int))
        i0 = bar_index(b["time"], ts)
        if i0 < 0 or i0 >= len(b["time"]) - 5:
            skipped += 1
            continue
        buy = str(direction).upper() in ("BUY", "LONG")
        cost = (b["spread"] * b["point"]) / abs(entry - stop) if entry != stop else 0.0
        ok = False
        for name, lad in LADDERS.items():
            r = walk(b, i0, float(entry), float(stop), buy, lad, TARGET)
            if r is None:
                continue
            results.setdefault(name, []).append(r - cost)
            results.setdefault(name + "::sym", []).append(sym)
            ok = True
        if ok:
            actual.append(pnl_r if pnl_r is not None else 0.0)

    print(f"matched to MT5 bars: {len(actual):,}   skipped: {skipped:,}\n")

    def stats(rs):
        rs = np.asarray(rs, dtype=float)
        wins = rs[rs > 0]
        gl = -rs[rs <= 0].sum()
        eq = np.cumsum(rs)
        dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
        return (len(rs), rs.mean(), rs.sum(), (rs > 0).mean(),
                (wins.sum() / gl if gl > 0 else float("inf")), dd)

    print("=" * 76)
    print("EXIT METHOD, APPLIED TO YOUR REAL STRATEGY ENTRIES")
    print("=" * 76)
    print(f"{'method':20s}{'n':>7s}{'expR':>9s}{'totR':>10s}{'WR':>8s}{'PF':>7s}{'maxDD':>9s}")
    n, e, t, w, pf, dd = stats(actual)
    print(f"{'ACTUAL (as traded)':20s}{n:7d}{e:+9.4f}{t:+10.1f}{w:8.1%}{pf:7.2f}{dd:9.1f}")
    for name in LADDERS:
        if name not in results:
            continue
        n, e, t, w, pf, dd = stats(results[name])
        print(f"{name:20s}{n:7d}{e:+9.4f}{t:+10.1f}{w:8.1%}{pf:7.2f}{dd:9.1f}")

    # per strategy, best ladder
    print("\n" + "=" * 76)
    print("BEST LADDER PER STRATEGY (vs what actually happened)")
    print("=" * 76)
    strat = [r[6] for r in rows]
    # rebuild alignment: recompute per-strategy using stored symbol lists
    per = {}
    idx = 0
    for sym, direction, entry, stop, et, pnl_r, sid in rows:
        b = cache.get(sym)
        if b is None:
            continue
        ts = int(np.datetime64(str(et).replace(" ", "T")[:19]).astype("datetime64[s]").astype(int))
        i0 = bar_index(b["time"], ts)
        if i0 < 0 or i0 >= len(b["time"]) - 5:
            continue
        buy = str(direction).upper() in ("BUY", "LONG")
        cost = (b["spread"] * b["point"]) / abs(entry - stop) if entry != stop else 0.0
        d = per.setdefault(sid, {"actual": []})
        d["actual"].append(pnl_r or 0.0)
        for name, lad in LADDERS.items():
            r = walk(b, i0, float(entry), float(stop), buy, lad, TARGET)
            if r is not None:
                d.setdefault(name, []).append(r - cost)

    print(f"{'strategy':20s}{'n':>6s}{'actual':>10s}{'best ladder':>18s}{'expR':>10s}{'lift':>9s}")
    for sid, d in sorted(per.items()):
        if len(d["actual"]) < 30:
            continue
        a = float(np.mean(d["actual"]))
        cand = {k: float(np.mean(v)) for k, v in d.items()
                if k != "actual" and len(v) > 0}
        if not cand:
            continue
        best = max(cand, key=cand.get)
        print(f"{sid:20s}{len(d['actual']):6d}{a:+10.4f}{best:>18s}"
              f"{cand[best]:+10.4f}{cand[best]-a:+9.4f}")


if __name__ == "__main__":
    main()
