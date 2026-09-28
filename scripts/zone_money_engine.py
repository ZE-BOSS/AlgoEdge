"""
scripts/zone_money_engine.py

Turn a list of level trades into an actual account: dollars, lots, min-lot
rounding, compounding, and the monthly / weekly / streak breakdown.

R-multiples hide two things that decide whether a small account can trade a
setup at all:

  * the MINIMUM LOT. Crash/Boom 1000 is 0.2 lots. With a $250 stop distance that
    is $50 of risk whatever you intended — 0.5% of a $10,000 account is $50, so
    the account has no say below that.
  * LOT GRANULARITY. 0.01 steps mean the realised risk is never exactly the
    target, and on a small account the rounding is a large fraction of it.

So the engine sizes each trade the way the app does, records what was ACTUALLY
risked, and reports money.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPECS = json.loads((ROOT / "data" / "zone_study" / "specs.json").read_text())


def spec(symbol: str) -> dict:
    s = SPECS.get(symbol)
    if not s:
        raise KeyError(f"no contract spec cached for {symbol}")
    return s


def size_lots(symbol: str, risk_dollars: float, stop_distance: float) -> tuple[float, float]:
    """(lots, risk actually taken). Rounds DOWN to the lot step, then applies the
    broker minimum — which can force more risk than asked for."""
    sp = spec(symbol)
    vpp = sp["value_per_price_per_lot"]
    if vpp <= 0 or stop_distance <= 0:
        return 0.0, 0.0
    raw = risk_dollars / (stop_distance * vpp)
    step = sp["lot_step"] or 0.01
    lots = math.floor(raw / step) * step
    forced = False
    if lots < sp["min_lot"]:
        lots, forced = sp["min_lot"], True
    lots = min(lots, sp["max_lot"])
    return round(lots, 4), lots * stop_distance * vpp


def run_account(trades: list[dict], start_balance: float = 10_000.0,
                risk_pct: float = 0.5, compounding: bool = True,
                max_risk_multiple: float = 3.0) -> dict:
    """`trades` need: t (epoch), symbol, r (R multiple, costs included), stop_distance.

    A trade whose forced minimum lot would risk more than `max_risk_multiple` x
    the intended risk is SKIPPED — the account cannot take it responsibly, and
    pretending otherwise is how a $10k backtest quietly trades like a $100k one.
    """
    bal = start_balance
    peak = bal
    equity = [bal]
    rows: list[dict] = []
    skipped = defaultdict(int)

    for tr in sorted(trades, key=lambda x: x["t"]):
        base = bal if compounding else start_balance
        want = base * risk_pct / 100.0
        lots, risked = size_lots(tr["symbol"], want, tr["stop_distance"])
        if lots <= 0:
            skipped[tr["symbol"]] += 1
            continue
        if risked > want * max_risk_multiple:
            skipped[tr["symbol"]] += 1
            continue
        pnl = tr["r"] * risked
        bal += pnl
        peak = max(peak, bal)
        equity.append(bal)
        rows.append({**tr, "lots": lots, "risked": risked, "pnl": pnl,
                     "balance": bal, "dd": peak - bal})
        if bal <= 0:
            break

    return {"rows": rows, "equity": equity, "final": bal, "start": start_balance,
            "skipped": dict(skipped)}


def streaks(flags: list[bool]) -> tuple[int, int]:
    best = cur = worst = curl = 0
    for f in flags:
        if f:
            cur, curl = cur + 1, 0
        else:
            curl, cur = curl + 1, 0
        best, worst = max(best, cur), max(worst, curl)
    return best, worst


def periods(rows: list[dict], fmt: str) -> dict:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[datetime.fromtimestamp(r["t"], timezone.utc).strftime(fmt)].append(r)
    out = {}
    for k, v in sorted(g.items()):
        pnl = sum(x["pnl"] for x in v)
        start = v[0]["balance"] - v[0]["pnl"]
        out[k] = {"pnl": pnl, "n": len(v), "pct": (pnl / start * 100) if start else 0.0,
                  "wins": sum(1 for x in v if x["pnl"] > 0)}
    return out


def report(res: dict, label: str) -> dict:
    rows = res["rows"]
    if not rows:
        return {"label": label, "trades": 0}
    pnl = np.array([r["pnl"] for r in rows])
    wins = pnl > 0
    bal = np.array([r["balance"] for r in rows])
    peak = np.maximum.accumulate(np.r_[res["start"], bal])[1:]
    dd_abs = peak - bal
    dd_pct = dd_abs / peak * 100

    months = periods(rows, "%Y-%m")
    weeks = periods(rows, "%Y-W%W")
    m_pos = [m["pnl"] > 0 for m in months.values()]
    w_pos = [w["pnl"] > 0 for w in weeks.values()]
    mb, mw = streaks(m_pos)
    wb, ww = streaks(w_pos)
    tb, tw = streaks(list(wins))

    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    ret = (res["final"] / res["start"] - 1) * 100
    days = (rows[-1]["t"] - rows[0]["t"]) / 86400 or 1

    return {
        "label": label,
        "start": res["start"], "final": res["final"],
        "return_pct": ret,
        "trades": len(rows),
        "win_rate": float(wins.mean()) * 100,
        "avg_win": float(pnl[pnl > 0].mean()) if wins.any() else 0.0,
        "avg_loss": float(pnl[pnl < 0].mean()) if (~wins).any() else 0.0,
        "profit_factor": float(gains / losses) if losses else float("inf"),
        "expectancy_usd": float(pnl.mean()),
        "max_dd_usd": float(dd_abs.max()),
        "max_dd_pct": float(dd_pct.max()),
        "best_trade": float(pnl.max()), "worst_trade": float(pnl.min()),
        "longest_win_streak": tb, "longest_loss_streak": tw,
        "months": months, "weeks": weeks,
        "month_pos_pct": float(np.mean(m_pos)) * 100,
        "month_best_streak": mb, "month_worst_streak": mw,
        "best_month": max((m["pnl"] for m in months.values()), default=0.0),
        "worst_month": min((m["pnl"] for m in months.values()), default=0.0),
        "week_pos_pct": float(np.mean(w_pos)) * 100,
        "week_best_streak": wb, "week_worst_streak": ww,
        "best_week": max((w["pnl"] for w in weeks.values()), default=0.0),
        "worst_week": min((w["pnl"] for w in weeks.values()), default=0.0),
        "trades_per_week": len(rows) / (days / 7),
        "skipped": res["skipped"],
    }


def print_report(rep: dict) -> None:
    if not rep.get("trades"):
        print(f"\n=== {rep['label']} ===\n  no tradable trades")
        return
    print(f"\n=== {rep['label']} ===")
    print(f"  ${rep['start']:,.0f} -> ${rep['final']:,.2f}   ({rep['return_pct']:+.1f}%)")
    print(f"  {rep['trades']:,} trades ({rep['trades_per_week']:.1f}/week) | win {rep['win_rate']:.1f}% "
          f"| PF {rep['profit_factor']:.2f} | expectancy ${rep['expectancy_usd']:+.2f}/trade")
    print(f"  avg win ${rep['avg_win']:+,.2f} | avg loss ${rep['avg_loss']:+,.2f} "
          f"| best ${rep['best_trade']:+,.2f} | worst ${rep['worst_trade']:+,.2f}")
    print(f"  max drawdown ${rep['max_dd_usd']:,.2f} ({rep['max_dd_pct']:.1f}%)")
    print(f"  streaks: {rep['longest_win_streak']} wins / {rep['longest_loss_streak']} losses")
    print(f"  months: {len(rep['months'])} | {rep['month_pos_pct']:.0f}% green | "
          f"best ${rep['best_month']:+,.0f} worst ${rep['worst_month']:+,.0f} | "
          f"streak +{rep['month_best_streak']}/-{rep['month_worst_streak']}")
    print(f"  weeks:  {len(rep['weeks'])} | {rep['week_pos_pct']:.0f}% green | "
          f"best ${rep['best_week']:+,.0f} worst ${rep['worst_week']:+,.0f} | "
          f"streak +{rep['week_best_streak']}/-{rep['week_worst_streak']}")
    if rep["skipped"]:
        print(f"  skipped (min lot too big for the account): {rep['skipped']}")


def month_table(rep: dict) -> str:
    out = [f"  {'month':10s} {'trades':>7s} {'P&L':>12s} {'%':>8s} {'win%':>6s}"]
    for k, m in rep["months"].items():
        wr = m["wins"] / m["n"] * 100 if m["n"] else 0
        out.append(f"  {k:10s} {m['n']:>7d} {m['pnl']:>+12,.2f} {m['pct']:>+7.2f}% {wr:>5.0f}%")
    return "\n".join(out)
