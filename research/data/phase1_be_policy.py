"""Phase 1h: what is the break-even stop actually worth?

Report 20 section 5.2: a plain bracket on ticks returns +0.4850 R on DJA's Crash
1000 signals while the engine, with its break-even and time-limit exits, books
+0.3597 R. Exit management is destroying 0.125 R per trade. This measures the
policy directly instead of inferring it from the difference.

The mechanism to test: a break-even stop moves the stop to entry once price has
travelled `trigger` R in favour. That truncates the loss distribution, which
feels prudent - but on these instruments the grind carries price back and forth
across the entry level constantly, so the BE stop is hit by noise on trades that
would otherwise have run. The excursion data says exactly that: BE_SL exits book
+0.0531 R after reaching **+2.0807 R** of mean favourable excursion.

Replays every DJA signal on ticks under several policies, holding entry, stop,
target and fills identical. Only the exit rule changes, so the comparison is
clean.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DB = HERE.parents[1] / "algoedge.db"
TICKS = HERE / "ticks"
SYMBOL, CODE = "Crash 1000 Index", "CRASH1000"
SPREAD_PCT = 0.00099
MAX_LOOK = 3_000_000


def to_epoch(v):
    s = str(v).strip().replace("T", " ").split("+")[0]
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(s, f).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def simulate(P, i, entry, risk, tp, be_trigger, long=True):
    """Walk ticks. Returns (R, exit_index). be_trigger=None disables the BE stop."""
    half = SPREAD_PCT / 100 / 2
    sl = entry - risk if long else entry + risk
    hi = min(i + 1 + MAX_LOOK, len(P))
    seg = P[i + 1:hi]
    if seg.size == 0:
        return None, i
    moved = (seg - entry) / risk if long else (entry - seg) / risk
    armed_at = None
    if be_trigger is not None:
        w = np.flatnonzero(moved >= be_trigger)
        armed_at = int(w[0]) if w.size else None

    hit_tp = np.flatnonzero(seg >= tp) if long else np.flatnonzero(seg <= tp)
    hit_sl = np.flatnonzero(seg <= sl) if long else np.flatnonzero(seg >= sl)
    jt = int(hit_tp[0]) if hit_tp.size else 10**18
    js = int(hit_sl[0]) if hit_sl.size else 10**18

    if armed_at is not None and armed_at < min(jt, js):
        # BE stop live from armed_at onward: exit at entry if price returns to it
        after = moved[armed_at:]
        back = np.flatnonzero(after <= 0.0)
        jb = armed_at + int(back[0]) if back.size else 10**18
        if jb < min(jt, js):
            fill = entry * (1 - half) if long else entry * (1 + half)
            r = (fill - entry) / risk if long else (entry - fill) / risk
            return r, i + 1 + jb
    if jt == 10**18 and js == 10**18:
        return None, i
    if js < jt:
        raw = float(seg[js])
        fill = raw * (1 - half) if long else raw * (1 + half)
        r = (fill - entry) / risk if long else (entry - fill) / risk
        return r, i + 1 + js
    fill = tp * (1 - half) if long else tp * (1 + half)
    r = (fill - entry) / risk if long else (entry - fill) / risk
    return r, i + 1 + jt


def main() -> None:
    z = np.load(max(TICKS.glob(f"{CODE}_*d.npz"), key=lambda x: x.stat().st_size),
                allow_pickle=True)
    T, P = z["t"], z["p"]
    c = sqlite3.connect(DB)
    rows = c.execute(
        """SELECT entry_time, direction, entry_price, stop_loss, tp1_price
           FROM backtest_trades WHERE strategy_id LIKE '%rift%' AND symbol = ?
             AND entry_price IS NOT NULL AND stop_loss IS NOT NULL
           ORDER BY entry_time""", (SYMBOL,)).fetchall()

    sigs = []
    half = SPREAD_PCT / 100 / 2
    for et, d, ep, sl, tp1 in rows:
        e0 = to_epoch(et)
        if e0 is None or e0 < T[0] or e0 > T[-1]:
            continue
        i = int(np.searchsorted(T, e0))
        if i >= len(T) - 2:
            continue
        long = (d or "").upper() in ("BUY", "LONG")
        entry = P[i] * (1 + half) if long else P[i] * (1 - half)
        risk = abs(float(ep) - float(sl))
        tp = entry + 5 * risk if long else entry - 5 * risk
        sigs.append((i, entry, risk, tp, long))

    print(f"{len(sigs)} signals replayed on {len(P):,} ticks\n")
    print(f"{'policy':26s}{'n':>5s}{'exp R':>9s}{'SE':>8s}{'win%':>7s}"
          f"{'n_ind':>7s}{'exp_ind':>10s}{'t_ind':>8s}")
    print("-" * 80)
    for label, trig in (("no BE (plain bracket)", None), ("BE at 0.5R", 0.5),
                        ("BE at 1.0R", 1.0), ("BE at 1.5R", 1.5),
                        ("BE at 2.0R", 2.0), ("BE at 3.0R", 3.0)):
        res, ends = [], []
        for i, entry, risk, tp, long in sigs:
            r, end = simulate(P, i, entry, risk, tp, trig, long)
            if r is None:
                continue
            res.append(r)
            ends.append((i, end))
        a = np.asarray(res)
        if not len(a):
            continue
        keep, last = [], -1
        for k, (o, e) in enumerate(ends):
            if o > last:
                keep.append(k)
                last = e
        ind = a[keep]
        se = a.std(ddof=1) / np.sqrt(len(a))
        ti = (ind.mean() / (ind.std(ddof=1) / np.sqrt(len(ind)))
              if len(ind) > 2 and ind.std(ddof=1) > 0 else float("nan"))
        print(f"{label:26s}{len(a):5d}{a.mean():+9.4f}{se:8.4f}{(a>0).mean()*100:7.1f}"
              f"{len(ind):7d}{ind.mean():+10.4f}{ti:+8.2f}")
    print("-" * 80)
    print("engine's booked result with its own BE policy: +0.3597 R (algoedge.db)")

    # Paired comparison. Each policy trades the SAME signals, so the per-trade
    # difference is far better determined than either expectancy on its own -
    # the shared price path cancels. This is the number to act on.
    print("\n--- paired: cost of arming the break-even stop, per trade ---")
    base = {}
    for i, entry, risk, tp, long in sigs:
        r, _ = simulate(P, i, entry, risk, tp, None, long)
        base[i] = r
    print(f"{'policy':16s}{'n':>6s}{'mean diff':>12s}{'SE':>9s}{'t':>8s}  reading")
    for label, trig in (("BE at 0.5R", 0.5), ("BE at 1.0R", 1.0),
                        ("BE at 1.5R", 1.5), ("BE at 2.0R", 2.0),
                        ("BE at 3.0R", 3.0)):
        diff = []
        for i, entry, risk, tp, long in sigs:
            r, _ = simulate(P, i, entry, risk, tp, trig, long)
            b = base.get(i)
            if r is not None and b is not None:
                diff.append(r - b)
        d = np.asarray(diff)
        se = d.std(ddof=1) / np.sqrt(len(d))
        t = d.mean() / se if se else float("nan")
        reading = ("DESTROYS value" if t < -2 else
                   "adds value" if t > 2 else "no clear effect")
        # Most trades are untouched by the policy and contribute an exact zero,
        # diluting the mean. The affected subset describes the mechanism; the
        # unconditional figure above is still the decision-relevant one, because
        # you cannot know in advance which trades the stop will touch.
        aff = d[d != 0]
        extra = (f"   | touched {len(aff)}/{len(d)} ({len(aff)/len(d):.0%}), "
                 f"mean {aff.mean():+.3f} R on those" if len(aff) else "")
        print(f"{label:16s}{len(d):6d}{d.mean():+12.4f}{se:9.4f}{t:+8.2f}  "
              f"{reading}{extra}")
    print("\nAll rows share the same entries, stops, targets and fill rules.")
    print("Only the exit rule differs, so the spread between rows is the policy's")
    print("contribution and nothing else.")


if __name__ == "__main__":
    main()
