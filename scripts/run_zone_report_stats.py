#!/usr/bin/env python
"""
scripts/run_zone_report_stats.py

Full trade statistics for the round-number continuation edge: monthly and
weekly P&L, winning and losing streaks, drawdown, Sharpe, Sortino, profit
factor — for each market that passed the control test, and for the three
combined as one book.

    python scripts/run_zone_report_stats.py
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_zone_robust import sequential_trades  # noqa: E402
from scripts.run_zone_study import CONTROL_OFFSETS, OUT, load  # noqa: E402

BOOK = [("EURUSD", 0.01), ("XAUUSD", 100.0), ("BTCUSD", 1000.0)]
KW = dict(approach=12, horizon=48, stop_frac=0.25, slip_points=1.0)


def streaks(wins: np.ndarray) -> tuple[int, int]:
    best = cur = 0
    worst = curl = 0
    for w in wins:
        if w:
            cur, curl = cur + 1, 0
        else:
            curl, cur = curl + 1, 0
        best, worst = max(best, cur), max(worst, curl)
    return best, worst


def period_table(tr: list[dict], fmt: str) -> dict[str, dict]:
    g: dict[str, list[float]] = defaultdict(list)
    for x in tr:
        g[datetime.fromtimestamp(x["t"], timezone.utc).strftime(fmt)].append(x["r"])
    out = {}
    for k, v in sorted(g.items()):
        a = np.array(v)
        out[k] = {"r": float(a.sum()), "n": int(len(a)), "win_rate": float((a > 0).mean())}
    return out


def full_stats(tr: list[dict], label: str) -> dict:
    r = np.array([x["r"] for x in tr])
    wins = r > 0
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    downside = r[r < 0]
    best_streak, worst_streak = streaks(wins)

    months = period_table(tr, "%Y-%m")
    weeks = period_table(tr, "%Y-W%W")
    mvals = np.array([m["r"] for m in months.values()])
    wvals = np.array([w["r"] for w in weeks.values()])
    m_wins = mvals > 0
    w_wins = wvals > 0
    m_best, m_worst = streaks(m_wins)
    w_best, w_worst = streaks(w_wins)

    return {
        "label": label,
        "trades": int(len(r)),
        "expectancy_r": float(r.mean()),
        "total_r": float(r.sum()),
        "win_rate": float(wins.mean()),
        "profit_factor": float(gains / losses) if losses > 0 else float("inf"),
        "t_stat": float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))),
        "sharpe_per_trade": float(r.mean() / r.std(ddof=1)),
        "sortino_per_trade": float(r.mean() / downside.std(ddof=1)) if len(downside) > 2 else float("nan"),
        "max_dd_r": float(dd.max()),
        "max_dd_trades": int(np.argmax(dd) - np.argmax(eq[:np.argmax(dd) + 1] == peak[np.argmax(dd)])) if len(dd) else 0,
        "longest_win_streak": best_streak,
        "longest_loss_streak": worst_streak,
        "months": months,
        "weeks": weeks,
        "month_win_rate": float(m_wins.mean()),
        "month_best_streak": m_best,
        "month_worst_streak": m_worst,
        "best_month_r": float(mvals.max()),
        "worst_month_r": float(mvals.min()),
        "week_win_rate": float(w_wins.mean()),
        "week_best_streak": w_best,
        "week_worst_streak": w_worst,
        "best_week_r": float(wvals.max()),
        "worst_week_r": float(wvals.min()),
    }


def main() -> None:
    all_trades: list[dict] = []
    report = {}
    for sym, step in BOOK:
        b = load(sym)
        tr = sequential_trades(b, step, 0.0, **KW)
        for x in tr:
            x["symbol"] = sym
        all_trades += tr
        report[f"{sym} @ {step:g}"] = full_stats(tr, f"{sym} @ {step:g}")

        ctl: list[dict] = []
        for f in CONTROL_OFFSETS:
            ctl += sequential_trades(b, step, f, **KW)
        report[f"{sym} @ {step:g}"]["control_expectancy_r"] = float(
            np.mean([x["r"] for x in ctl]))

    all_trades.sort(key=lambda x: x["t"])
    report["BOOK (3 markets)"] = full_stats(all_trades, "BOOK (3 markets)")

    for name, s in report.items():
        print(f"\n=== {name} ===")
        print(f"  trades {s['trades']:,} | expectancy {s['expectancy_r']:+.4f}R "
              f"| total {s['total_r']:+.1f}R | win {s['win_rate'] * 100:.1f}% "
              f"| PF {s['profit_factor']:.3f} | t {s['t_stat']:+.2f}")
        if "control_expectancy_r" in s:
            print(f"  control expectancy {s['control_expectancy_r']:+.4f}R "
                  f"(edge vs control {s['expectancy_r'] - s['control_expectancy_r']:+.4f}R)")
        print(f"  max DD {s['max_dd_r']:.1f}R | Sharpe/trade {s['sharpe_per_trade']:.4f} "
              f"| Sortino/trade {s['sortino_per_trade']:.4f}")
        print(f"  streaks: {s['longest_win_streak']} wins, {s['longest_loss_streak']} losses")
        print(f"  months: {len(s['months'])} | {s['month_win_rate'] * 100:.0f}% positive "
              f"| best {s['best_month_r']:+.1f}R worst {s['worst_month_r']:+.1f}R "
              f"| streak +{s['month_best_streak']} / -{s['month_worst_streak']}")
        print(f"  weeks:  {len(s['weeks'])} | {s['week_win_rate'] * 100:.0f}% positive "
              f"| best {s['best_week_r']:+.1f}R worst {s['worst_week_r']:+.1f}R "
              f"| streak +{s['week_best_streak']} / -{s['week_worst_streak']}")

    p = OUT / "zone_report_stats.json"
    p.write_text(json.dumps(report, indent=1))
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
