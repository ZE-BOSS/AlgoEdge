"""
scripts/bt_audit.py - audit saved backtest runs trade by trade. Read-only.

For each run: totals in R and money, max drawdown (money and R), winning / losing streaks,
per strategy x symbol breakdown, and integrity checks on every trade:
  * exit before entry, stop on the wrong side of the entry, weekend fills (non-synthetics)
  * pnl and pnl_r disagreeing in sign; pnl_r against the price move for single-target exits
  * balance_after - balance_before != pnl
  * realised risk per full-stop loss as % of balance (sizing), against the run's setting
  * the most positions / risk open at once
  * the same pairing entering twice at the same time

    cd /opt/algoedge/<inst>/app && PYTHONPATH=. ../venv/bin/python scripts/bt_audit.py <id> [<id> ...] [--pairs]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

SYNTH = ("Volatility", "Boom", "Crash", "Jump", "Step", "Range Break", "DEX", "Drift")


def streaks(rs):
    best_w = best_l = cw = cl = 0
    for r in rs:
        if r > 0:
            cw, cl = cw + 1, 0
        elif r < 0:
            cl, cw = cl + 1, 0
        best_w, best_l = max(best_w, cw), max(best_l, cl)
    return best_w, best_l


def dd_r(rs):
    eq = np.cumsum(rs) if len(rs) else np.array([0.0])
    peak = np.maximum.accumulate(np.r_[0.0, eq])
    return float((peak - np.r_[0.0, eq]).max())


def stats(ts):
    rs = [t.pnl_r or 0.0 for t in ts]
    n = len(rs)
    if not n:
        return "0 trades"
    w = sum(1 for r in rs if r > 0)
    gp, gl = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    sw, sl = streaks(rs)
    return (f"{n:4d} tr win {w / n:5.1%} sumR {sum(rs):+7.1f} avgR {sum(rs) / n:+.3f} PF(R) "
            f"{gp / gl if gl else float('inf'):.2f} maxDD {dd_r(rs):5.1f}R streaks W{sw}/L{sl}")


async def main() -> int:
    import bt_runs as B
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--pairs", action="store_true")
    a = ap.parse_args()
    runs = await B.load(days=30)
    for rid in a.ids:
        r0 = next((r for r in runs if r.id.startswith(rid)), None)
        if r0 is None:
            print("unknown", rid)
            continue
        run = (await B.load(ids=[r0.id]))[0]
        p = B._p(run)
        ts = sorted(run.trades, key=lambda t: (t.exit_time, t.id))
        print(f"\n######## {run.id[:8]} {run.title or ''} | {run.start_date:%Y-%m-%d}..{run.end_date:%Y-%m-%d} "
              f"balance {p.get('initial_balance')} risk% {p.get('risk_per_trade_pct')} sizing {p.get('sizing_basis')} "
              f"maxconc {p.get('max_concurrent_positions')}")
        rows = p.get("symbols") or []
        for x in rows:
            rk = x.get("risk") or {}
            print(f"   row {x.get('symbol')} {x.get('strategy_id')} risk%={rk.get('risk_per_trade_pct')} "
                  f"be={rk.get('be_mode')} trail={rk.get('trail_mode')}/{rk.get('trail_method_tp1')} tp1={rk.get('tp1_rr')} "
                  f"tpn={rk.get('tp_count')}")
        bal0 = float(p.get("initial_balance") or 0)
        bals = [t.balance_after for t in ts if t.balance_after is not None]
        peak, mdd = bal0, 0.0
        for b in bals:
            peak = max(peak, b)
            mdd = max(mdd, (peak - b) / peak if peak else 0)
        print(f"ALL  {stats(ts)} | money {bal0:.0f} -> {bals[-1] if bals else bal0:,.0f} maxDD {mdd:.1%}")
        # integrity
        issues = Counter()
        ex = defaultdict(list)
        risk_pct = []
        for t in ts:
            d = 1 if (t.direction or "").upper().startswith("B") else -1
            if t.exit_time and t.entry_time and t.exit_time < t.entry_time:
                issues["exit before entry"] += 1
                ex["exit before entry"].append(t)
            if t.stop_loss and t.entry_price and (t.entry_price - t.stop_loss) * d <= 0:
                issues["stop on the wrong side"] += 1
                ex["stop on the wrong side"].append(t)
            if t.entry_time and t.entry_time.weekday() >= 5 and not any(s in (t.symbol or "") for s in SYNTH):
                issues["weekend fill"] += 1
                ex["weekend fill"].append(t)
            if t.pnl is not None and t.pnl_r is not None and abs(t.pnl) > 1e-6 and abs(t.pnl_r) > 0.05 \
                    and np.sign(t.pnl) != np.sign(t.pnl_r):
                issues["pnl / pnl_r signs differ"] += 1
                ex["pnl / pnl_r signs differ"].append(t)
            if (t.tp_level_hit or 0) <= 1 and not t.be_applied and t.stop_loss and t.exit_price and t.entry_price \
                    and t.pnl_r is not None and t.exit_reason not in ("PARTIAL",):
                risk = abs(t.entry_price - t.stop_loss)
                if risk > 0:
                    rp = (t.exit_price - t.entry_price) * d / risk
                    if abs(rp - t.pnl_r) > 0.35 + 0.1 * abs(rp):
                        issues["pnl_r far from the price move"] += 1
                        ex["pnl_r far from the price move"].append(t)
            if t.balance_before is not None and t.balance_after is not None and t.pnl is not None \
                    and abs((t.balance_after - t.balance_before) - t.pnl) > max(0.01, 1e-6 * abs(t.balance_before)):
                issues["balance step != pnl"] += 1
                ex["balance step != pnl"].append(t)
            if t.pnl_r is not None and -1.15 <= t.pnl_r <= -0.85 and t.balance_before and t.pnl:
                risk_pct.append(-t.pnl / t.balance_before * 100 / -t.pnl_r)
        seen = Counter((t.strategy_id, t.symbol, t.entry_time, t.direction) for t in ts)
        dup = sum(c - 1 for c in seen.values() if c > 1)
        if dup:
            issues["same pairing entered twice at once"] = dup
        # concurrency
        ev = sorted([(t.entry_time, 1) for t in ts] + [(t.exit_time, -1) for t in ts], key=lambda x: (x[0], x[1]))
        cur = mx = 0
        for _, k in ev:
            cur += k
            mx = max(mx, cur)
        if risk_pct:
            q = np.percentile(risk_pct, [5, 50, 95])
            print(f"     sizing: a full-stop loss cost {q[0]:.2f}% / {q[1]:.2f}% / {q[2]:.2f}% of the balance (5th/50th/95th pct)"
                  f" over {len(risk_pct)} stops; max {max(risk_pct):.2f}%")
        print(f"     most positions open at once: {mx}")
        print("     exit reasons:", dict(Counter(t.exit_reason for t in ts).most_common(8)))
        print("     integrity:", dict(issues) if issues else "no problems found")
        for k, v in ex.items():
            for t in v[:2]:
                print(f"       e.g. {k}: {t.symbol} {t.strategy_id} {t.direction} {t.entry_time} -> {t.exit_time} "
                      f"entry {t.entry_price} sl {t.stop_loss} exit {t.exit_price} {t.exit_reason} pnl {t.pnl:.2f} R {t.pnl_r}")
        if a.pairs:
            g = defaultdict(list)
            for t in ts:
                g[(t.strategy_id, t.symbol)].append(t)
            for k in sorted(g, key=lambda k: -sum((t.pnl_r or 0) for t in g[k])):
                print(f"   {k[0][:26]:26s} {k[1][:14]:14s} {stats(g[k])}")
            months = defaultdict(float)
            for t in ts:
                months[t.exit_time.strftime("%Y-%m")] += t.pnl_r or 0
            print("   R by month:", " ".join(f"{m[2:]}:{v:+.0f}" for m, v in sorted(months.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
