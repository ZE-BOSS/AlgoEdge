"""INVALID AS WRITTEN - DO NOT TRUST ITS OUTPUT.

`daily_trade_cap` is a STRATEGY-side gate (strategy_two/engine.py:284), applied
while signals are generated. This script generates signals once and then varies
the ENGINE's `max_daily_trades`, which cannot affect signals that already exist.
Every cap value returned an identical 71 trades / +$417.

To sweep it correctly, regenerate signals inside the loop with the strategy's
own cap parameter changed each time (~4 min per value).
"""

"""Sweep `max_daily_trades` - the gate that blocks 77% of every candidate.

The first real gate telemetry (research/10) showed `daily_trade_cap` discarding
15,263 of 19,700 DriftJumpAlpha candidates, against under 18% for every
technical filter combined. It is the most consequential parameter in the
strategy and has never been measured.

Signals are generated ONCE and reused across every cap value, so the only thing
that varies between runs is the cap itself.
"""
import sys, asyncio
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.strategies.registry import get_strategy
from backend.core.config_schema import UserConfigV2, InstrumentSettings
from backend.backtester.engine import BacktestEngine

CACHE = REPO / "research/data/cache"
SYM, SID = "Crash 1000 Index", "DriftJumpAlpha_v1"
CAPS = [3, 5, 7, 10, 15, 25, 50, 999]

BASE = {
    "risk_per_trade_pct": 1.0, "max_risk_hard_cap_pct": 2.0,
    "max_daily_drawdown_pct": 3.0, "max_weekly_drawdown_pct": 6.0,
    "tp_count": 1, "tp1_rr": 5, "be_mode": "RR", "be_trigger_rr": 1,
    "be_buffer_pips": 5, "trail_method_tp1": "NONE",
    "max_concurrent_positions": 3, "max_positions_per_symbol": 3,
    "min_bars_between_entries": 7, "simulate_wicks": True,
    "min_sl_pips": 10, "max_account_leverage": 100,
}


def load(sym, n=20000):
    z = np.load(CACHE / f"{sym.replace(' ', '_')}__M5.npz")
    df = pd.DataFrame({k: z[k][-n:] for k in
                       ("open", "high", "low", "close", "volume")})
    df.index = pd.to_datetime(z["time"][-n:], unit="s", utc=True)
    df["time"] = z["time"][-n:]
    return df


async def signals(df):
    cfg = UserConfigV2()
    cfg.instrument_settings = [InstrumentSettings(symbol=SYM, strategy_id=SID)]
    eng = get_strategy(SID)(cfg)
    eng.is_backtesting = True
    out = []
    for i in range(300, len(df)):
        s = await eng.on_bar(SYM, "M5", df.iloc[max(0, i - 500):i + 1])
        if s:
            out.append({
                "symbol": SYM, "direction": s.direction,
                "time": int(df["time"].iloc[i]),
                "entry_price": s.entry_price, "stop_loss": s.stop_loss,
                "take_profit": s.take_profit, "timeframe": "M5",
                "confluence_score": s.confluence_score,
                "metadata": s.metadata or {},
                "confirmations": (s.metadata or {}).get("confirmations", []),
            })
    return out, eng


async def main():
    df = load(SYM)
    sigs, eng = await signals(df)
    print(f"{SID} x {SYM}: {len(sigs)} signals from {len(df)} M5 bars")
    print(f"window {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}\n")
    print(f"{'cap':>5} {'trades':>7} {'P&L $':>11} {'expR':>8} {'totR':>9} {'WR':>7} {'PF':>6} {'maxDD%':>8}")
    for cap in CAPS:
        cfg = {**BASE, "max_daily_trades": cap}
        res = BacktestEngine(cfg).run(df, sigs, 10000.0, strategy=eng)
        tr = res.get("trades", [])
        rs = [t["pnl_r"] for t in tr if t.get("pnl_r") is not None]
        pnl = res.get("final_balance", 10000) - 10000
        if not rs:
            print(f"{cap:>5} {len(tr):>7} {pnl:>+11.2f}   no trades")
            continue
        wins = [r for r in rs if r > 0]
        gl = -sum(r for r in rs if r <= 0)
        pf = (sum(wins) / gl) if gl else float("inf")
        dd = res.get("max_drawdown_pct", 0) or 0
        print(f"{cap:>5} {len(tr):>7} {pnl:>+11.2f} {sum(rs)/len(rs):>+8.3f} "
              f"{sum(rs):>+9.1f} {len(wins)/len(rs):>7.1%} {pf:>6.2f} {dd*100:>8.1f}")


if __name__ == "__main__":
    asyncio.run(main())
