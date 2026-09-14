#!/usr/bin/env python
"""
scripts/run_app_backtest.py

Run a single-symbol backtest through the SAME code the Backtester page uses —
BacktestRequest defaults, apply_strategy_params (with the per-symbol table),
the route's signal loop and window sizes, build_merged_risk_config, and
BacktestEngine with the strategy's on_position_bar hook — without the web app.

Its purpose is parity: a number printed here is the number the Backtester page
shows for the same inputs. It needs a connected MT5 terminal.

    python scripts/run_app_backtest.py --strategy ORB_v1 --symbol GBPJPY \
        --start 2026-01-10 --end 2026-09-11 --balance 10000 --risk 1.8 \
        --sizing-basis BALANCE --daily-dd 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run(args) -> dict:
    import numpy as np
    import pandas as pd

    from backend.api.routes.backtest import (
        BacktestRequest, apply_strategy_params, build_merged_risk_config,
    )
    from backend.backtester.engine import BacktestEngine
    from backend.core.config_schema import InstrumentSettings, UserConfigV2
    from backend.mt5.data_fetcher import DataFetcher
    from backend.strategies.registry import get_strategy
    from backend.strategies.windows import window_bars

    req = BacktestRequest(
        strategy_id=args.strategy, symbol=args.symbol, start_date=args.start, end_date=args.end,
        initial_balance=args.balance, risk_per_trade_pct=args.risk,
        max_daily_drawdown_pct=args.daily_dd, max_weekly_drawdown_pct=args.weekly_dd,
        max_concurrent_positions=args.max_positions, max_positions_per_symbol=args.max_per_symbol,
        max_daily_trades=args.max_daily_trades, tp_count=1, tp1_rr=args.tp1_rr,
        strategy_params=json.loads(args.strategy_params), replay_enabled=False, min_rr=args.min_rr,
        sizing_basis=args.sizing_basis, allow_pyramiding=args.pyramiding or None,
        stop_fill_model=args.fill_model, max_risk_hard_cap_pct=max(3.0, args.risk),
        # None = let broker_costs resolve it (what the Backtester page does).
        spread_pips=args.spread_pips, commission_per_lot=args.commission,
        slippage_pips=args.slippage, exit_slippage_pips=args.slippage,
        max_margin_utilisation_pct=args.max_margin_pct,
    )

    # ── engine, exactly as the route builds it ──
    config = UserConfigV2()
    config.risk.min_rr = req.min_rr
    config.risk.tp_count = 1
    config.risk.tp1_rr = req.tp1_rr
    config.risk.risk_per_trade_pct = req.risk_per_trade_pct
    config.risk.max_daily_drawdown_pct = req.max_daily_drawdown_pct
    config.risk.max_weekly_drawdown_pct = req.max_weekly_drawdown_pct
    config.risk.max_concurrent_positions = req.max_concurrent_positions
    config.risk.max_positions_per_symbol = req.max_positions_per_symbol
    config.risk.max_daily_trades = req.max_daily_trades
    apply_strategy_params(config, req.strategy_id, req.strategy_params, req.symbol)
    config.instrument_settings = [InstrumentSettings(symbol=req.symbol, strategy_id=req.strategy_id)]
    engine = get_strategy(req.strategy_id)(config)
    engine.is_backtesting = True

    required = engine.get_required_timeframes()
    warmup = {"M1": 1, "M5": 5, "M15": 10, "M30": 15, "H1": 30, "H4": 150, "D1": 365}
    tf_min = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    start_dt, end_dt = datetime.fromisoformat(req.start_date), datetime.fromisoformat(req.end_date)
    by_tf = {}
    for tf in required:
        from backend.strategies.windows import warmup_days
        df = await DataFetcher.get_data_range(req.symbol, tf, start_dt - pd.Timedelta(days=warmup_days(tf, engine, warmup[tf])), end_dt)
        by_tf[tf] = (df.set_index(pd.to_datetime(df["time"], unit="s")) if "time" in df.columns else df).sort_index()
    primary = sorted(required, key=lambda t: tf_min.get(t, 999))[0]

    # ── the route's signal loop, multi-timeframe — the shared feeder ──
    from backend.strategies.bar_feed import BarFeed

    feed = BarFeed(engine, req.symbol, required)
    feed.bind(by_tf)
    df = by_tf[primary]
    times = df.index.values
    cutoff = np.datetime64(start_dt)
    signals = []
    for i in range(300, len(times)):
        current_time = times[i]
        sig = await feed.step(i)
        if sig and current_time >= cutoff:
            # Same stamping rule as the route: the signal belongs to the last bar in
            # the slice (i-1), so the engine fills it at bar i's open — live's price.
            _signal_bar_time = int(times[i - 1].astype("datetime64[s]").astype(int))
            signals.append({
                "symbol": sig.symbol, "direction": sig.direction,
                "time": _signal_bar_time,
                "entry_price": sig.entry_price, "stop_loss": sig.stop_loss, "take_profit": sig.take_profit,
                "timeframe": sig.timeframe, "confluence_score": sig.confluence_score,
                "score_breakdown": sig.metadata.get("score_breakdown", {}), "metadata": sig.metadata,
                "confirmations": sig.metadata.get("confirmations", []),
            })

    risk_config = build_merged_risk_config(req)
    bt = BacktestEngine(risk_config)
    res = bt.run(df, signals, req.initial_balance, by_tf.get("M15", df), by_tf.get("M5", df), engine, None, by_tf.get("H1"))
    out = summarize(res, req, len(signals))
    out["costs_resolved"] = {k: v for k, v in bt._costs_for(req.symbol).items() if not str(k).startswith("_")}
    out["fill_model"] = res.get("fill_model")
    reasons: dict[str, int] = {}
    for t in res.get("trades", []):
        reasons[str(t.get("exit_reason"))] = reasons.get(str(t.get("exit_reason")), 0) + 1
    out["exit_reasons"] = reasons
    if getattr(args, "dump", None):
        keep = ("entry_time", "exit_time", "direction", "entry_price", "exit_price", "stop_loss", "take_profit",
                "volume", "pnl", "exit_reason", "balance_before", "balance_after", "spread_cost", "commission",
                "slippage_cost", "stop_overshoot_r", "gap_fill",
                # Excursion, so one far-target run yields every R:R and scale-out
                # variant analytically: a target is reached exactly when favourable
                # excursion gets there before the stop.
                "mae_pips", "mfe_pips", "mae_r", "mfe_r", "risk_pips", "initial_stop_loss",
                "original_sl", "tp_level", "group_id", "symbol", "confluence_score", "entry_spread_pips")
        Path(args.dump).write_text(json.dumps([{k: t.get(k) for k in keep} for t in res.get("trades", [])],
                                              default=str, indent=1), encoding="utf-8")
        out["dumped_trades_to"] = args.dump
    return out


def summarize(res: dict, req, n_signals: int) -> dict:
    trades = res.get("trades", [])
    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    gw, gl = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
    bal, peak, mdd = req.initial_balance, req.initial_balance, 0.0
    for p in pnls:
        bal += p
        peak = max(peak, bal)
        mdd = max(mdd, (peak - bal) / peak if peak > 0 else 0.0)
    rs = [float(t["pnl_r"]) for t in trades if t.get("pnl_r") is not None]
    return {
        "strategy": req.strategy_id, "symbol": req.symbol, "window": [req.start_date, req.end_date],
        "balance": req.initial_balance, "risk_pct": req.risk_per_trade_pct, "sizing_basis": req.sizing_basis,
        "signals": n_signals, "trades": len(trades),
        "final_balance": round(float(res.get("final_balance", bal)), 2),
        "net_pnl": round(float(res.get("final_balance", bal)) - req.initial_balance, 2),
        "win_rate": round(sum(p > 0 for p in pnls) / len(pnls), 4) if pnls else None,
        "profit_factor": round(gw / gl, 3) if gl > 0 else None,
        "expectancy_usd": round(statistics.mean(pnls), 2) if pnls else None,
        "expectancy_r": round(statistics.mean(rs), 4) if rs else None,
        "max_dd_pct": round(100 * mdd, 2),
        "rejections": res.get("rejection_funnel", {}).get("risk_rejections", {}),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="ORB_v1")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--tp1-rr", type=float, default=3.0)
    ap.add_argument("--min-rr", type=float, default=None,
                    help="RiskEngine minimum R:R; defaults to the target so a chosen 1:2 is not rejected by the 3.0 default")
    ap.add_argument("--daily-dd", type=float, default=10.0)
    ap.add_argument("--weekly-dd", type=float, default=30.0)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--max-per-symbol", type=int, default=1)
    ap.add_argument("--max-daily-trades", type=int, default=10)
    ap.add_argument("--sizing-basis", choices=["STATIC", "BALANCE", "EQUITY"], default="STATIC")
    ap.add_argument("--pyramiding", action="store_true")
    ap.add_argument("--fill-model", default="CONSERVATIVE")
    ap.add_argument("--strategy-params", default="{}")
    ap.add_argument("--dump", default=None, help="write the engine's trades to this JSON file")
    # Cost overrides — omit to use whatever broker_costs resolves (the page's behaviour).
    ap.add_argument("--spread-pips", type=float, default=None)
    ap.add_argument("--commission", type=float, default=None)
    ap.add_argument("--slippage", type=float, default=None)
    ap.add_argument("--max-margin-pct", type=float, default=None)
    args = ap.parse_args()
    if args.min_rr is None:
        args.min_rr = min(args.tp1_rr, 3.0)
    print(json.dumps(asyncio.run(run(args)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
