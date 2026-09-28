#!/usr/bin/env python
"""
scripts/run_published_strategies.py

Two strategies taken from published research, implemented to their stated rules
and tested on this account's own data — not the repo's existing strategies.

1. FIVE-MINUTE OPENING RANGE BREAKOUT
   Zarattini & Aziz (2023), "Can Day Trading Really Be Profitable?"; Zarattini,
   Barbon & Aziz (2024), "A Profitable Day Trading Strategy For The U.S. Equity
   Market". Reported Sharpe 2.4 (QQQ) and 2.81 (stocks-in-play portfolio).

     signal   the first 5-minute bar of the session. Up bar -> long, down -> short,
              no trade if open == close
     entry    the open of the second 5-minute bar
     stop     the opposite extreme of that first bar
     target   `target_r` x risk, else flat at the session close
   This is NOT the repo's ORB (60-minute range, H1 trend filter, 1:3).

2. INTRADAY MOMENTUM
   Gao, Han, Li & Zhou (2018), "Market Intraday Momentum", Journal of Financial
   Economics. The first half-hour return predicts the last half-hour return;
   predictive R^2 1.6%, rising to 2.6% combined with the penultimate half hour.

     signal   sign of the return over the first 30 minutes of the session
     entry    30 minutes before the session close
     exit     the session close

SESSIONS are detected from the data (the minute-of-day with peak median volume
is the open), so the same code works on a 24-hour CFD and a cash index without
hand-set clock times per broker.

    python scripts/run_published_strategies.py --feed deriv
    python scripts/run_published_strategies.py --feed fundednext
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402

ZS = ROOT / "data" / "zone_study"
FEEDS = {"deriv": (ZS / "bars", ZS / "specs.json"),
         "fundednext": (ZS / "fundednext", ZS / "fundednext" / "specs.json")}

DERIV_MARKETS = ["US Tech 100", "US SP 500", "Germany 40", "XAUUSD", "BTCUSD",
                 "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "XAGUSD"]
FN_MARKETS = ["US30", "SPX500", "XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY"]


def load(bars_dir: Path, sym: str) -> dict:
    z = np.load(bars_dir / f"{sym.replace(' ', '_')}.npz")
    return {"time": z["time"].astype(np.int64), "open": z["open"].astype(float),
            "high": z["high"].astype(float), "low": z["low"].astype(float),
            "close": z["close"].astype(float), "spread_pts": z["spread"].astype(float),
            "vol": z["tick_volume"].astype(float), "point": float(z["point"])}


def session_open_minute(b: dict) -> int:
    """The minute-of-day where volume peaks — the cash open for an index, the
    London/NY handover for FX. Data-driven so it survives DST and broker clocks."""
    mins = (b["time"] % 86400) // 60
    tot = np.zeros(1440)
    np.add.at(tot, mins.astype(int), b["vol"])
    # smooth over 30 minutes so a single spiky bar cannot define the session
    k = np.ones(30) / 30
    sm = np.convolve(np.r_[tot, tot], k, mode="same")[:1440]
    return int(np.argmax(sm))


def day_index(b: dict, open_min: int) -> dict[int, np.ndarray]:
    """Bars grouped into sessions that START at `open_min`."""
    shifted = b["time"] - open_min * 60
    day = (shifted // 86400).astype(int)
    order = np.argsort(day, kind="stable")
    out: dict[int, list[int]] = defaultdict(list)
    for i in order:
        out[int(day[i])].append(int(i))
    return {d: np.array(v) for d, v in out.items() if len(v) >= 20}


def orb_trades(b: dict, sym: str, target_r: float, slip_points: float,
               or_bars: int = 1) -> list[dict]:
    om = session_open_minute(b)
    sessions = day_index(b, om)
    o, h, lo, c = b["open"], b["high"], b["low"], b["close"]
    spread_px = b["spread_pts"] * b["point"]
    slip = slip_points * b["point"]
    out = []
    for _, idx in sorted(sessions.items()):
        if len(idx) < or_bars + 3:
            continue
        rng_idx = idx[:or_bars]
        i0 = rng_idx[0]
        if o[i0] == c[rng_idx[-1]]:
            continue
        long_ = c[rng_idx[-1]] > o[i0]
        entry_i = idx[or_bars]
        entry = o[entry_i]
        stop = lo[rng_idx].min() if long_ else h[rng_idx].max()
        R = abs(entry - stop)
        if R <= 0:
            continue
        tgt = entry + target_r * R if long_ else entry - target_r * R
        rest = idx[or_bars:]
        gross = None
        for j in rest:
            if long_:
                if lo[j] <= stop:
                    gross = stop - entry
                    break
                if h[j] >= tgt:
                    gross = tgt - entry
                    break
            else:
                if h[j] >= stop:
                    gross = entry - stop
                    break
                if lo[j] <= tgt:
                    gross = entry - tgt
                    break
        if gross is None:
            last = rest[-1]
            gross = (c[last] - entry) if long_ else (entry - c[last])
        cost = spread_px[entry_i] + slip
        out.append({"t": int(b["time"][entry_i]), "symbol": sym,
                    "r": (gross - cost) / R, "stop_distance": R})
    return out


def intraday_momentum_trades(b: dict, sym: str, slip_points: float,
                             stop_r: float = 1.0) -> list[dict]:
    """Long/short the last 30 minutes in the direction of the first 30 minutes.

    The paper trades it unlevered with no stop; a stop of `stop_r` x the entry
    ATR proxy is added because a prop account has a drawdown limit and an
    unstopped position is not sizeable in R terms.
    """
    om = session_open_minute(b)
    sessions = day_index(b, om)
    o, h, lo, c = b["open"], b["high"], b["low"], b["close"]
    spread_px = b["spread_pts"] * b["point"]
    slip = slip_points * b["point"]
    out = []
    for _, idx in sorted(sessions.items()):
        if len(idx) < 20:
            continue
        first6 = idx[:6]
        ret = c[first6[-1]] - o[first6[0]]
        if ret == 0:
            continue
        long_ = ret > 0
        last6 = idx[-6:]
        entry_i = last6[0]
        entry = o[entry_i]
        # risk unit: the first half hour's own range, a scale the signal defines
        R = max(h[first6].max() - lo[first6].min(), 1e-12)
        R *= stop_r
        stop = entry - R if long_ else entry + R
        gross = None
        for j in last6:
            if long_ and lo[j] <= stop:
                gross = -R
                break
            if (not long_) and h[j] >= stop:
                gross = -R
                break
        if gross is None:
            last = last6[-1]
            gross = (c[last] - entry) if long_ else (entry - c[last])
        cost = spread_px[entry_i] + slip
        out.append({"t": int(b["time"][entry_i]), "symbol": sym,
                    "r": (gross - cost) / R, "stop_distance": R})
    return out


def summarise(trades: list[dict], label: str) -> dict:
    if len(trades) < 20:
        return {"label": label, "n": len(trades)}
    r = np.array([t["r"] for t in trades])
    days = (max(t["t"] for t in trades) - min(t["t"] for t in trades)) / 86400 or 1
    per_month = len(r) / (days / 30.4)
    return {"label": label, "n": int(len(r)),
            "expectancy_r": float(r.mean()), "win_rate": float((r > 0).mean()),
            "total_r": float(r.sum()),
            "t_stat": float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if r.std() else 0.0,
            "trades_per_month": per_month,
            "r_per_month": float(r.mean()) * per_month}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--target-r", type=float, default=10.0)
    ap.add_argument("--or-bars", type=int, default=1,
                    help="opening range length in M5 bars (1=5min, 3=15min, 6=30min, 12=60min)")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--slip-points", type=float, default=1.0)
    args = ap.parse_args()

    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(specs_path.read_text())
    markets = FN_MARKETS if args.feed == "fundednext" else DERIV_MARKETS
    t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())

    print(f"\nPUBLISHED STRATEGIES on the {args.feed} feed, {args.since} -> today "
          f"| ORB target {args.target_r}R | costs = bar spread + {args.slip_points} pts")
    hdr = (f"{'market':14s} {'strategy':10s} {'N':>5s} {'expR':>7s} {'win%':>6s} "
           f"{'totR':>8s} {'t':>6s} {'trades/mo':>10s} {'R/month':>8s}")
    print(hdr)
    print("-" * len(hdr))

    books: dict[str, list[dict]] = {"orb5": [], "intraday_mom": []}
    rows = []
    for sym in markets:
        try:
            b = load(bars_dir, sym)
        except FileNotFoundError:
            continue
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        if len(b["time"]) < 2000:
            continue
        for name, fn in (("orb5", lambda: orb_trades(b, sym, args.target_r, args.slip_points, args.or_bars)),
                         ("intraday_mom", lambda: intraday_momentum_trades(b, sym, args.slip_points))):
            tr = fn()
            s = summarise(tr, f"{sym}/{name}")
            if s.get("n", 0) >= 20:
                books[name] += tr
                rows.append({"symbol": sym, "strategy": name, **s})
                print(f"{sym:14s} {name:10s} {s['n']:>5d} {s['expectancy_r']:>+7.3f} "
                      f"{s['win_rate'] * 100:>5.1f}% {s['total_r']:>+8.1f} {s['t_stat']:>+6.2f} "
                      f"{s['trades_per_month']:>10.1f} {s['r_per_month']:>+8.2f}")

    print("\nAS A PORTFOLIO (every market together, $10,000 at 0.5% risk)")
    out = {"rows": rows, "books": {}}
    for name, tr in books.items():
        if len(tr) < 20:
            continue
        s = summarise(tr, name)
        res = eng.run_account(tr, start_balance=10_000.0, risk_pct=0.5)
        rep = eng.report(res, f"{name} portfolio")
        eng.print_report(rep)
        print(f"  -> {s['r_per_month']:+.2f} R/month from {s['trades_per_month']:.1f} trades/month")
        out["books"][name] = {"summary": s, "money": rep,
                              "r_sequence": [t["r"] for t in sorted(tr, key=lambda x: x["t"])]}

    p = ZS / f"published_{args.feed}.json"
    p.write_text(json.dumps(out, indent=1, default=float))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
