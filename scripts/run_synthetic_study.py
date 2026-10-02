#!/usr/bin/env python
"""
scripts/run_synthetic_study.py

Every strategy against every new Deriv synthetic, through the APP'S OWN ENGINE.

WHY NOT A RESEARCH REIMPLEMENTATION
-----------------------------------
`run_app_form_check.py` rewrites each strategy in numpy to sweep parameters
quickly. That is the right tool for a sweep and the wrong one for "what would
the app actually have done", because the rewrite does not carry the app's
fills, costs, sizing floors or exit handling — and those are where the edge
goes (Implementation/STRATEGIES-SHIPPED-2026-09-25.md).

So this drives the REAL strategy classes through `BarFeed` — the same feeder
the live scan loop uses, so the bar sequence is identical — and then the REAL
`BacktestEngine` via `backtester/runner.run_backtest`. The numbers it prints
are the numbers the Backtester page would print for the same settings.

TWO RISK MODES, BOTH REPORTED
-----------------------------
A compounded headline on a few hundred trades is dominated by its final
trades: at 2% risk, 1:3, a 37% win rate over 402 trades compounds $10,000 to
roughly $465,000 with no bug anywhere. That number says almost nothing about
the edge, and everything about the exponent.

So every run is reported twice — compounded (what the app shows) and FLAT (a
fixed dollar risk from the opening balance). Flat is the one to read when
comparing strategies; compounded is the one to read when sizing expectations,
and only alongside the drawdown.

    py -3.12 scripts/run_synthetic_study.py --list
    py -3.12 scripts/run_synthetic_study.py --out data/synthetic_study
    py -3.12 scripts/run_synthetic_study.py --symbols "Vol over Crash 750" \\
        --strategies DriftJumpAlpha_v1 --risk 2.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "deriv_new"

# The book as it stands, minus the four being retired (IVW_v1,
# OvernightSession_v1, OpeningDrive_v1, HTFFVGFlip_v1).
DEFAULT_STRATEGIES = [
    "DriftJumpAlpha_v1",
    "BoomDriftJump_v1",
    "TrendDrift_v1",
    "TrendBreakout_v1",
    "SpikeFade_v1",
    "RangeRevert_v1",
    "RangeBreakout_v1",
]


def load_bars(symbol: str, tf: str) -> pd.DataFrame | None:
    """Exported bars as the engines expect them: UTC index, OHLC + spread."""
    path = DATA / f"{symbol.replace(' ', '_')}_{tf}.npz"
    if not path.exists():
        return None
    z = np.load(path, allow_pickle=False)
    df = pd.DataFrame({k: z[k] for k in z.files})
    if "time" not in df:
        return None
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    for col in ("open", "high", "low", "close"):
        if col not in df:
            return None
    # The exporter writes `spread_points`; the engine reads `spread` and treats
    # it as POINTS (_note_entry_spread -> pts * point / pip). This is a RENAME,
    # not a default: filling a missing column with 0.0 would charge no spread at
    # all, which on a synthetic index is most of the cost and would manufacture
    # an edge out of nothing.
    if "spread" not in df:
        if "spread_points" in df:
            df["spread"] = df["spread_points"].astype(float)
        else:
            raise ValueError(
                f"{path.name} has no spread column — refusing to run a costed "
                f"backtest with no spread rather than silently charging zero")
    if "tick_volume" not in df:
        df["tick_volume"] = 100.0
    return df.sort_index()


def available() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in sorted(DATA.glob("*.npz")):
        stem = p.stem
        sym, _, tf = stem.rpartition("_")
        out.setdefault(sym.replace("_", " "), []).append(tf)
    return out


async def signals_for(symbol: str, strategy_id: str, frames: dict[str, pd.DataFrame],
                      cap: int = 20_000) -> list[dict]:
    """Drive the real engine over the bars and collect what it emits."""
    from backend.core.config_schema import UserConfigV2
    from backend.strategies.bar_feed import BarFeed
    from backend.strategies.registry import get_strategy

    engine = get_strategy(strategy_id)(UserConfigV2())
    needed = engine.get_required_timeframes()
    have = {tf: frames[tf] for tf in needed if tf in frames}
    if len(have) < len(needed):
        return []

    feed = BarFeed(engine, symbol, needed)
    feed.bind(have)
    primary = feed.primary
    times = have[primary].index.values

    out: list[dict] = []
    # 300 bars of warm-up, the same offset the backtest route uses before it
    # starts believing the engine.
    for i in range(300, len(times)):
        sig = await feed.step(i)
        if sig is None:
            continue
        md = dict(getattr(sig, "metadata", None) or {})
        md.setdefault("slot_max_positions", 1)
        out.append({
            "symbol": sig.symbol,
            "_cache_key": f"{symbol.upper()}|{strategy_id}",
            "strategy_name": strategy_id,
            "direction": sig.direction,
            "time": int(pd.Timestamp(times[i]).timestamp()),
            "entry_price": sig.entry_price,
            "stop_loss": sig.stop_loss,
            "take_profit": sig.take_profit,
            "timeframe": sig.timeframe,
            "confluence_score": sig.confluence_score,
            "metadata": md,
        })
        if len(out) >= cap:
            break
    return out, engine


def risk_config(risk_pct: float, compounding: bool, balance: float) -> dict:
    """The app's own risk block, with compounding on or pinned flat.

    `sizing_basis=STATIC` with `sizing_static_balance` is how the app expresses
    "always risk the same dollars" — the flat comparison, not an approximation
    of one.
    """
    cfg = {
        "risk_per_trade_pct": risk_pct,
        "max_risk_hard_cap_pct": max(risk_pct * 1.1, 3.0),
        "min_rr": 0.5,
        "max_daily_drawdown_pct": 100.0,
        "max_weekly_drawdown_pct": 100.0,
        "max_concurrent_positions": 1,
        "max_positions_per_symbol": 1,
        "max_daily_trades": 1000,
        "use_strategy_exit_defaults": True,
    }
    if not compounding:
        cfg["sizing_basis"] = "STATIC"
        cfg["sizing_static_balance"] = balance
    return cfg


def metrics(result: dict, balance: float) -> dict:
    """The columns asked for, plus the ones that decide whether to believe them."""
    trades = result.get("trades") or []
    # `report` is a RiskReport dataclass on some paths and a plain dict on
    # others, so read it through one accessor rather than assuming either.
    report = result.get("report")

    def _rep(key, default=None):
        if report is None:
            return default
        if isinstance(report, dict):
            return report.get(key, default)
        return getattr(report, key, default)

    n = len(trades)
    if not n:
        return {"trades": 0}

    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net = sum(pnls)

    # R per trade, from the risk the trade ACTUALLY took.
    #
    # A trade record carries no risk-dollars field, so this is recomputed from
    # entry, the INITIAL stop (not the trailed one, which would shrink R as the
    # trade went well and flatter every winner) and the filled volume, using the
    # app's own helper so the dollar conversion is identical to the one that
    # sized the position.
    from backend.risk.position_sizer import calculate_risk_dollars

    rs = []
    for t in trades:
        stop = t.get("initial_stop_loss") or t.get("original_sl") or t.get("stop_loss")
        try:
            risk = abs(calculate_risk_dollars(
                float(t.get("volume") or 0.0), float(t.get("entry_price") or 0.0),
                float(stop or 0.0), t.get("symbol") or ""))
        except Exception:
            risk = 0.0
        if risk > 0:
            rs.append(float(t.get("pnl") or 0.0) / risk)

    # equity curve and the drawdown it really suffered
    eq, peak, maxdd = balance, balance, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0.0)

    gross_win, gross_loss = sum(wins), -sum(losses)
    exits: dict[str, int] = {}
    for t in trades:
        exits[t.get("exit_reason", "?")] = exits.get(t.get("exit_reason", "?"), 0) + 1

    return {
        "trades": n,
        "win_rate": len(wins) / n * 100,
        "pnl": net,
        "return_pct": net / balance * 100,
        "final_balance": balance + net,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "max_dd_pct": maxdd * 100,
        "expectancy_r": (sum(rs) / len(rs)) if rs else float("nan"),
        # return per unit of pain — the number that actually ranks strategies
        "return_over_dd": (net / balance * 100) / (maxdd * 100) if maxdd > 0 else float("nan"),
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "payoff": (abs(sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
                  if wins and losses else float("nan"),
        "biggest_5_share": (sum(sorted(pnls, reverse=True)[:5]) / net * 100)
                           if net > 0 else float("nan"),
        "sharpe": _rep("sharpe_ratio"),
        "exits": exits,
    }


async def one(symbol: str, strategy_id: str, frames: dict, balance: float,
              risk_pct: float) -> dict | None:
    from backend.backtester.runner import run_backtest

    got = await signals_for(symbol, strategy_id, frames)
    if not got:
        return None
    sigs, engine = got
    if not sigs:
        return {"symbol": symbol, "strategy": strategy_id, "signals": 0}

    primary = engine.get_required_timeframes()[0]
    candles = frames[primary]
    out = {"symbol": symbol, "strategy": strategy_id, "signals": len(sigs)}
    for mode, compounding in (("compounded", True), ("flat", False)):
        res = await run_backtest(
            user_id="research", strategy_id=strategy_id, symbol=symbol,
            candles=candles, signals=sigs,
            risk_config=risk_config(risk_pct, compounding, balance),
            initial_balance=balance, save_mode="DISCARD", strategy=engine,
        )
        out[mode] = metrics(res, balance)
    return out


def table(rows: list[dict], mode: str, balance: float) -> str:
    head = (f"| Symbol | Strategy | Trades | WR | P&L $ | Return % | Max DD % | "
            f"PF | Exp R | Ret/DD | Payoff |")
    sep = "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [head, sep]
    for r in sorted(rows, key=lambda x: -(x.get(mode, {}).get("pnl") or -1e18)):
        m = r.get(mode) or {}
        if not m.get("trades"):
            lines.append(f"| {r['symbol']} | {r['strategy']} | 0 | — | — | — | — | — | — | — | — |")
            continue
        pf = m["profit_factor"]
        lines.append(
            f"| {r['symbol']} | {r['strategy']} | {m['trades']} | {m['win_rate']:.0f}% | "
            f"${m['pnl']:,.0f} | {m['return_pct']:+.1f}% | {m['max_dd_pct']:.1f}% | "
            f"{'inf' if math.isinf(pf) else f'{pf:.2f}'} | {m['expectancy_r']:+.3f} | "
            f"{m['return_over_dd']:.2f} | {m['payoff']:.2f} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--strategies", nargs="*", default=DEFAULT_STRATEGIES)
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--out", default="data/synthetic_study")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    have = available()
    if args.list or not have:
        print(f"\nExported bars in {DATA}:")
        for sym, tfs in sorted(have.items()):
            print(f"  {sym:<26} {','.join(sorted(tfs))}")
        if not have:
            print("  (none — run scripts/export_new_synthetics.py first)")
        return

    symbols = args.symbols or sorted(have)
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{len(symbols)} symbol(s) x {len(args.strategies)} strategies, "
          f"${args.balance:,.0f} at {args.risk}% risk\n")

    rows: list[dict] = []
    for sym in symbols:
        frames = {}
        for tf in have.get(sym, []):
            df = load_bars(sym, tf)
            if df is not None:
                frames[tf] = df
        if not frames:
            print(f"  {sym}: no usable bars"); continue
        span = next(iter(frames.values()))
        print(f"  {sym}: {', '.join(f'{tf} {len(frames[tf]):,}' for tf in sorted(frames))} "
              f"| {span.index[0]:%Y-%m-%d} -> {span.index[-1]:%Y-%m-%d}")
        for sid in args.strategies:
            t0 = time.time()
            try:
                r = asyncio.run(one(sym, sid, frames, args.balance, args.risk))
            except Exception as exc:
                print(f"      {sid:<22} FAILED: {exc}")
                continue
            if r is None:
                print(f"      {sid:<22} (timeframe not exported)")
                continue
            rows.append(r)
            c = r.get("compounded") or {}
            f = r.get("flat") or {}
            if not c.get("trades"):
                print(f"      {sid:<22} {r['signals']:>5} signals -> 0 trades  "
                      f"({time.time()-t0:.0f}s)")
            else:
                print(f"      {sid:<22} {c['trades']:>4} trades | "
                      f"compounded ${c['pnl']:>12,.0f} ({c['return_pct']:+8.1f}%) | "
                      f"flat ${f.get('pnl', 0):>9,.0f} ({f.get('return_pct', 0):+7.1f}%) | "
                      f"DD {c['max_dd_pct']:.0f}% | ({time.time()-t0:.0f}s)")

    (out_dir / "results.json").write_text(json.dumps(rows, indent=1, default=str))
    md = out_dir / "RESULTS.md"
    md.write_text(
        f"# Synthetic study — {datetime.now(timezone.utc):%Y-%m-%d}\n\n"
        f"${args.balance:,.0f} opening balance, {args.risk}% risk per trade, "
        f"app engine and app fills.\n\n"
        f"## Flat risk (compare strategies on this)\n\n{table(rows, 'flat', args.balance)}\n\n"
        f"## Compounded (what the app shows)\n\n{table(rows, 'compounded', args.balance)}\n",
        encoding="utf-8")
    print(f"\n-> {md}\n-> {out_dir / 'results.json'}")


if __name__ == "__main__":
    main()
