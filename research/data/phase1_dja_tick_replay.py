"""Phase 1e: replay DriftJumpAlpha's real Crash 1000 trades against raw ticks.

Report 20 section 5 leaves ~0.46 R of DJA's +0.36 R expectancy unexplained. The
process is a fair martingale and optional stopping forbids a stopping-time
strategy from beating zero gross, so the residual has to be measurement. Rather
than keep eliminating candidates one at a time, this replays the actual signals
on 1-second ticks where fills are unambiguous.

The decomposition it produces:

  plain bracket on ticks   -> what the SIGNALS are really worth
  engine's booked result   -> what the harness reported
  difference               -> what EXIT MANAGEMENT (BE / trail / time limit) added

Theory says the first line must come out at -spread/stop, which for DJA's 0.347%
stop and Crash 1000's 0.00099% spread is about -0.003 R. Anything else in that
line means the signals themselves are being mispriced; a large gap between line 1
and line 2 puts it in the exit manager.

Fills are modelled the way a real order would fill:
  * long enters at ask  = mid + half-spread
  * a stop is a market order - it fills at the FIRST tick through the level,
    which on a jump process can be far beyond it
  * a target is a limit order - it fills at the limit price, never better
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DB = HERE.parent.parent / "algoedge.db"
TICKS = HERE / "ticks"

SYMBOL = "Crash 1000 Index"
CODE = "CRASH1000"
SPREAD_PCT = 0.00099          # historical median, synth_spread_audit.py


def load_trades() -> list[dict]:
    c = sqlite3.connect(DB)
    rows = c.execute(
        """SELECT entry_time, exit_time, direction, entry_price, stop_loss,
                  tp1_price, exit_reason, pnl_r, risk_pips
           FROM backtest_trades
           WHERE strategy_id LIKE '%rift%' AND symbol = ?
             AND entry_price IS NOT NULL AND stop_loss IS NOT NULL
             AND pnl_r IS NOT NULL
           ORDER BY entry_time""", (SYMBOL,)).fetchall()
    out = []
    for et, xt, d, ep, sl, tp, er, pr, rp in rows:
        out.append({"entry_time": et, "exit_time": xt, "dir": (d or "").upper(),
                    "entry": float(ep), "sl": float(sl),
                    "tp": float(tp) if tp else None,
                    "exit_reason": er, "pnl_r": float(pr)})
    return out


def to_epoch(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace("T", " ")
    for f in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f%z",
              "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s.split("+")[0] if "%z" not in f else s, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def main() -> None:
    f = sorted(TICKS.glob(f"{CODE}_*d.npz"), key=lambda x: x.stat().st_size)
    if not f:
        raise SystemExit(f"no {CODE} ticks yet - harvest still running")
    z = np.load(f[-1], allow_pickle=True)
    T, P = z["t"], z["p"]
    print(f"ticks: {f[-1].name}  {len(T):,} ticks  "
          f"{datetime.fromtimestamp(T[0], timezone.utc):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(T[-1], timezone.utc):%Y-%m-%d}")

    trades = load_trades()
    print(f"trades: {len(trades)} DriftJumpAlpha {SYMBOL}")

    half = SPREAD_PCT / 100 / 2
    booked, replay, slips = [], [], []
    closed_at, opened_at = [], []
    covered = unresolved = 0

    for t in trades:
        e0 = to_epoch(t["entry_time"])
        if e0 is None or e0 < T[0] or e0 > T[-1]:
            continue
        i = int(np.searchsorted(T, e0))
        if i >= len(T) - 2:
            continue
        covered += 1
        long = t["dir"] in ("BUY", "LONG")
        mid = P[i]
        entry = mid * (1 + half) if long else mid * (1 - half)   # cross the spread
        # keep the strategy's own risk geometry, re-anchored to the real fill
        risk = abs(t["entry"] - t["sl"])
        sl = entry - risk if long else entry + risk
        tp = (entry + (t["tp"] - t["entry"]) if t["tp"] else
              entry + 5 * risk) if long else (
             entry - (t["entry"] - t["tp"]) if t["tp"] else entry - 5 * risk)

        seg = P[i + 1:min(i + 1 + 3_000_000, len(P))]
        if long:
            a = np.flatnonzero(seg <= sl)
            b = np.flatnonzero(seg >= tp)
        else:
            a = np.flatnonzero(seg >= sl)
            b = np.flatnonzero(seg <= tp)
        ja = a[0] if a.size else np.inf
        jb = b[0] if b.size else np.inf
        if ja == np.inf and jb == np.inf:
            unresolved += 1
            continue
        if ja < jb:                       # stop: market order, fills where it lands
            raw = float(seg[int(ja)])
            fill = raw * (1 - half) if long else raw * (1 + half)
            r = (fill - entry) / risk if long else (entry - fill) / risk
            slips.append(r + 1.0)         # how much worse than a clean -1R
        else:                             # target: limit order, no improvement
            fill = tp * (1 - half) if long else tp * (1 + half)
            r = (fill - entry) / risk if long else (entry - fill) / risk
        booked.append(t["pnl_r"])
        replay.append(r)
        # record when this trade closed, so overlap can be filtered below
        closed_at.append(i + 1 + int(min(ja, jb)))
        opened_at.append(i)

    if not replay:
        raise SystemExit("no trades fell inside the tick window")
    bk, rp = np.asarray(booked), np.asarray(replay)
    sl_arr = np.asarray(slips) if slips else np.array([0.0])
    print(f"\nreplayed {len(rp)} of {covered} in-window trades "
          f"({unresolved} unresolved)")
    print("=" * 78)
    print(f"  engine booked expectancy      {bk.mean():+.4f} R   (total {bk.sum():+.1f} R)")
    print(f"  tick replay, plain bracket    {rp.mean():+.4f} R   (total {rp.sum():+.1f} R)")
    print(f"  theory: -spread/stop          {-(SPREAD_PCT/100)/(0.00347):+.4f} R")
    print("=" * 78)
    print(f"  difference (exit mgmt + fill) {bk.mean() - rp.mean():+.4f} R per trade")
    print(f"  SE of replay                  {rp.std()/np.sqrt(len(rp)):.4f}")
    print(f"  replay t vs zero              {rp.mean()/(rp.std()/np.sqrt(len(rp))):+.2f}")
    print(f"\n  stop fills: {len(sl_arr)}, mean overshoot beyond -1R = "
          f"{sl_arr.mean():+.4f} R  (worst {sl_arr.min():+.3f} R)")
    print(f"  replay win rate {(rp > 0).mean():.1%}  vs booked {(bk > 0).mean():.1%}")

    # ---- overlap correction ------------------------------------------------
    # DJA runs concurrent positions, so consecutive trades share price path and
    # are not independent draws. phase1_random_control.py measured the cost of
    # ignoring this: the SAME random long strategy reads +0.1577 R (t=+3.99)
    # overlapping and +0.0499 R (t=+0.56) non-overlapping. The naive standard
    # error below is therefore optimistic; this keeps only trades that opened
    # after the previous kept trade had closed.
    keep, last_close = [], -1
    for k, (o, c_) in enumerate(zip(opened_at, closed_at)):
        if o > last_close:
            keep.append(k)
            last_close = c_
    ind = rp[keep]
    print("\n  --- independent (non-overlapping) subset ---")
    if len(ind) >= 20:
        se_i = ind.std() / np.sqrt(len(ind))
        fair = -(SPREAD_PCT / 100) / 0.003472
        print(f"  n={len(ind)} of {len(rp)}  exp {ind.mean():+.4f} R  SE {se_i:.4f}")
        print(f"  t vs zero {ind.mean()/se_i:+.2f}   t vs fair ({fair:+.4f}) "
              f"{(ind.mean()-fair)/se_i:+.2f}")
        print(f"  win rate {(ind > 0).mean():.1%}")
        print("\n  random-entry baseline at this geometry (non-overlapping):")
        print(f"     long  +0.0499 R (t vs fair +0.56)   short -0.0785 R (t -0.96)")
    else:
        print(f"  only {len(ind)} independent trades - too few to judge")


if __name__ == "__main__":
    main()
