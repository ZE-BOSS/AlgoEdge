"""Is the +16,090% real? Decompose the user's backtests into their causes.

Four questions, each of which can independently explain a huge headline number
without any edge being present:

1. **Compounding.** sizing_basis=EQUITY compounds every win. Re-running the SAME
   trade sequence with fixed-dollar risk shows how much of the return is edge and
   how much is the exponent.
2. **Concurrency.** max_concurrent_positions=15 with pyramiding means up to 15
   simultaneous positions at 1.8% each = 27% of equity at risk at once. That is
   not a 1.8%-risk strategy; drawdown and ruin risk scale with the real figure.
3. **Realised risk per trade.** If losing trades do not cost ~1.8% of balance,
   the sizing model and the reported risk disagree.
4. **Exit mix.** research/25 measured break-even harmful on these instruments and
   the engine books intrabar spikes at the stop price (research/24 4.1), so
   TRAIL_SL/BE_SL-heavy P&L is the least trustworthy kind.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

DBG = Path(__file__).resolve().parents[2] / "debug" / "backtest"


def load(f: Path):
    d = json.loads(f.read_text(encoding="utf-8"))
    ps = d.get("params_snapshot") or {}
    tr = sorted((d.get("trades") or []), key=lambda t: t.get("entry_time") or 0)
    return d, ps, tr


def analyse(f: Path) -> dict:
    d, ps, tr = load(f)
    if not tr:
        return {}
    init = float(d.get("initial_balance") or 10000)
    risk_pct = float(ps.get("risk_per_trade_pct") or 1.0)

    # --- realised loss size vs the configured risk
    loss_pct, win_pct = [], []
    for t in tr:
        bb = float(t.get("balance_before") or 0) or init
        pnl = float(t.get("pnl") or 0)
        (loss_pct if pnl < 0 else win_pct).append(pnl / bb * 100)

    # --- concurrency: how many positions open at any moment
    events = []
    for t in tr:
        a, b = t.get("entry_time"), t.get("exit_time")
        if a is None or b is None:
            continue
        events.append((int(a), 1))
        events.append((int(b), -1))
    events.sort()
    cur = peak = 0
    conc_hist = defaultdict(int)
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
        conc_hist[cur] += 1
    # time-weighted average concurrency
    tw = []
    cur = 0
    for i, (ts, delta) in enumerate(events):
        cur += delta
        if i + 1 < len(events):
            tw.append((events[i + 1][0] - ts, cur))
    dur = sum(w for w, _ in tw) or 1
    avg_conc = sum(w * c for w, c in tw) / dur

    # --- re-simulate the same R-sequence with STATIC sizing
    # derive per-trade R from pnl / (risk dollars at the time)
    rs = []
    for t in tr:
        bb = float(t.get("balance_before") or 0) or init
        rd = bb * risk_pct / 100.0
        if rd > 0:
            rs.append(float(t.get("pnl") or 0) / rd)
    rs = np.asarray(rs, dtype=float)
    static_bal = init + (init * risk_pct / 100.0) * rs.sum()

    # --- exit mix
    mix = defaultdict(lambda: [0, 0.0])
    for t in tr:
        er = t.get("exit_reason") or "?"
        mix[er][0] += 1
        mix[er][1] += float(t.get("pnl") or 0)

    return {
        "symbol": tr[0].get("symbol"), "strategy": tr[0].get("strategy_id"),
        "n": len(tr), "init": init, "final": float(d.get("final_balance") or 0),
        "risk_pct": risk_pct, "sizing": ps.get("sizing_basis"),
        "pyramiding": ps.get("allow_pyramiding"),
        "max_conc_cfg": ps.get("max_concurrent_positions"),
        "peak_conc": peak, "avg_conc": avg_conc,
        "median_loss_pct": float(np.median(loss_pct)) if loss_pct else float("nan"),
        "worst_loss_pct": float(np.min(loss_pct)) if loss_pct else float("nan"),
        "total_r": float(rs.sum()), "exp_r": float(rs.mean()),
        "static_final": static_bal,
        "static_ret": (static_bal / init - 1) * 100,
        "equity_ret": (float(d.get("final_balance") or 0) / init - 1) * 100,
        "mix": dict(mix),
    }


def main() -> None:
    files = sorted(DBG.glob("*.json"))
    rows = [a for a in (analyse(f) for f in files) if a]
    rows.sort(key=lambda x: -x["equity_ret"])

    print("COMPOUNDING: what the same trades earn without it")
    print("=" * 118)
    print(f"{'symbol':20s}{'strategy':17s}{'n':>6s}{'expR':>7s}{'totR':>8s}"
          f"{'EQUITY ret%':>13s}{'STATIC ret%':>13s}{'inflation':>11s}")
    print("-" * 118)
    for x in rows:
        infl = (x["equity_ret"] / x["static_ret"]) if x["static_ret"] > 0 else float("nan")
        print(f"{str(x['symbol'])[:19]:20s}{str(x['strategy'])[:16]:17s}{x['n']:6d}"
              f"{x['exp_r']:+7.3f}{x['total_r']:+8.0f}{x['equity_ret']:+13.1f}"
              f"{x['static_ret']:+13.1f}"
              + (f"{infl:10.1f}x" if infl == infl else f"{'-':>11s}"))

    print("\n\nCONCURRENCY: the real risk being run")
    print("=" * 118)
    print(f"{'symbol':20s}{'strategy':17s}{'cfg max':>9s}{'peak':>7s}{'avg':>7s}"
          f"{'risk/trade':>12s}{'peak risk%':>12s}{'med loss%':>11s}{'worst%':>9s}")
    print("-" * 118)
    for x in rows:
        print(f"{str(x['symbol'])[:19]:20s}{str(x['strategy'])[:16]:17s}"
              f"{str(x['max_conc_cfg']):>9s}{x['peak_conc']:7d}{x['avg_conc']:7.1f}"
              f"{x['risk_pct']:11.2f}%{x['peak_conc']*x['risk_pct']:11.1f}%"
              f"{x['median_loss_pct']:11.2f}{x['worst_loss_pct']:9.2f}")

    print("\n\nEXIT MIX: where the P&L comes from")
    print("=" * 118)
    for x in rows[:10]:
        tot = sum(v[1] for v in x["mix"].values()) or 1.0
        parts = sorted(x["mix"].items(), key=lambda kv: -kv[1][1])
        s = "  ".join(f"{k}:{v[0]}({v[1]/tot*100:+.0f}%)" for k, v in parts)
        print(f"  {str(x['symbol'])[:19]:20s} {s}")


if __name__ == "__main__":
    main()
