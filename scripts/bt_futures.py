"""
scripts/bt_futures.py - run AlgoEdge strategies on Databento futures bars, with the
same signal loop and BacktestEngine the Backtester page uses (scripts/run_app_backtest.py),
but candles from of_fetch.py's 1-minute files instead of MT5.

    python scripts/bt_futures.py --data /opt/algoedge/research_data --market gold \
        --strategies VWAP_v1,ORB_v1 --start 2026-01-01 --end 2026-11-26 --out /opt/algoedge/research_data/bt

Each trade is written with its R: (exit - entry) * side / (entry - initial stop), minus the
round-trip futures cost (a tick of slippage each side plus commission) in R. The engine
itself runs cost-free so contract specs never matter: results are in R, per 1 R risked.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# market -> (symbol name the strategies' per-symbol tables know, round-trip cost in price points)
MARKETS = {"gold": ("XAUUSD", 0.25), "silver": ("XAGUSD", 0.012), "nasdaq": ("US Tech 100", 0.75),
           "sp500": ("US SP 500", 0.5)}
RULE = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1D"}


def load_m1(data: Path, market: str) -> pd.DataFrame:
    p = data / f"{market}_m1.parquet"
    df = pd.read_parquet(p) if p.exists() else pd.read_csv(data / f"{market}_m1.csv.gz")
    return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def resample(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "M1":
        out = m1.copy()
    else:
        x = m1.set_index(pd.to_datetime(m1["time"], unit="s"))
        o = x.resample(RULE[tf], label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "tick_volume": "sum", "real_volume": "sum"})
        o = o.dropna(subset=["open"])
        o["spread"] = 0
        o["time"] = (o.index.astype("int64") // 1_000_000_000).astype("int64")
        out = o.reset_index(drop=True)
    return out.set_index(pd.to_datetime(out["time"], unit="s")).sort_index()


async def run_one(strategy: str, market: str, m1: pd.DataFrame, start: str, end: str, tp1_rr: float) -> dict:
    from backend.api.routes.backtest import BacktestRequest, apply_strategy_params, build_merged_risk_config
    from backend.backtester.engine import BacktestEngine
    from backend.backtester.intrabar import IntrabarResolver
    from backend.core.config_schema import InstrumentSettings, UserConfigV2
    from backend.strategies.bar_feed import BarFeed
    from backend.strategies.registry import get_strategy

    symbol, cost_pts = MARKETS[market]
    req = BacktestRequest(strategy_id=strategy, symbol=symbol, start_date=start, end_date=end, initial_balance=10000,
                          risk_per_trade_pct=1.0, max_daily_drawdown_pct=100, max_weekly_drawdown_pct=100,
                          max_concurrent_positions=10, max_positions_per_symbol=1, max_daily_trades=20, tp_count=1,
                          tp1_rr=tp1_rr, strategy_params={}, replay_enabled=False, sizing_basis="STATIC",
                          stop_fill_model="CONSERVATIVE", max_risk_hard_cap_pct=3.0, spread_pips=0.0,
                          commission_per_lot=0.0, slippage_pips=0.0, exit_slippage_pips=0.0)
    config = UserConfigV2()
    config.risk.tp_count, config.risk.tp1_rr = 1, tp1_rr
    config.risk.risk_per_trade_pct = 1.0
    config.risk.max_daily_drawdown_pct = config.risk.max_weekly_drawdown_pct = 100
    config.risk.max_concurrent_positions, config.risk.max_positions_per_symbol, config.risk.max_daily_trades = 10, 1, 20
    apply_strategy_params(config, strategy, {}, symbol)
    config.instrument_settings = [InstrumentSettings(symbol=symbol, strategy_id=strategy)]
    engine = get_strategy(strategy)(config)
    engine.is_backtesting = True
    required = engine.get_required_timeframes()
    tf_min = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    by_tf = {tf: resample(m1, tf) for tf in required}
    primary = sorted(required, key=lambda t: tf_min.get(t, 999))[0]
    feed = BarFeed(engine, symbol, required)
    feed.bind(by_tf)
    df = by_tf[primary]
    times = df.index.values
    cutoff = np.datetime64(datetime.fromisoformat(start))
    signals = []
    for i in range(300, len(times)):
        sig = await feed.step(i)
        if sig and times[i] >= cutoff:
            signals.append({"symbol": sig.symbol, "direction": sig.direction,
                            "time": int(times[i - 1].astype("datetime64[s]").astype(int)),
                            "entry_price": sig.entry_price, "stop_loss": sig.stop_loss, "take_profit": sig.take_profit,
                            "timeframe": sig.timeframe, "confluence_score": sig.confluence_score,
                            "score_breakdown": sig.metadata.get("score_breakdown", {}), "metadata": sig.metadata,
                            "confirmations": sig.metadata.get("confirmations", [])})
    bt = BacktestEngine(build_merged_risk_config(req))
    bt.intrabar = {symbol: IntrabarResolver(m1.reset_index(drop=True))}
    res = bt.run(df, signals, 10000, by_tf.get("M15", df), by_tf.get("M5", df), engine, None, by_tf.get("H1"))
    out = []
    for t in res.get("trades", []):
        e, x = float(t.get("entry_price") or 0), float(t.get("exit_price") or 0)
        stop = float(t.get("initial_stop_loss") or t.get("original_sl") or t.get("stop_loss") or 0)
        side = 1 if str(t.get("direction")).upper() in ("BUY", "LONG") else -1
        risk = abs(e - stop)
        if risk <= 0:
            continue
        out.append({"entry_time": t.get("entry_time"), "exit_time": t.get("exit_time"), "side": side, "entry": e,
                    "exit": x, "stop": stop, "risk_pts": risk, "exit_reason": t.get("exit_reason"),
                    "r_gross": (x - e) * side / risk, "r": (x - e) * side / risk - cost_pts / risk,
                    "mfe_r": t.get("mfe_r"), "mae_r": t.get("mae_r")})
    return {"strategy": strategy, "market": market, "symbol": symbol, "primary_tf": primary, "signals": len(signals),
            "trades": out, "cost_pts": cost_pts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--market", required=True)
    ap.add_argument("--strategies", required=True)
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-11-26")
    ap.add_argument("--tp1-rr", type=float, default=3.0)
    ap.add_argument("--out", default="/opt/algoedge/research_data/bt")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    m1 = load_m1(Path(a.data), a.market)
    for s in a.strategies.split(","):
        t0 = time.time()
        try:
            r = asyncio.run(run_one(s, a.market, m1, a.start, a.end, a.tp1_rr))
            (out / f"{a.market}__{s}.json").write_text(json.dumps(r, default=str))
            rs = [t["r"] for t in r["trades"]]
            print(f"{a.market:7s} {s:24s} trades {len(rs):5d}  net {sum(rs):8.1f}R  "
                  f"exp {np.mean(rs) if rs else 0:+.3f}R  {time.time() - t0:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{a.market:7s} {s:24s} ERROR {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=3)
    print("BT_DONE", a.market)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
