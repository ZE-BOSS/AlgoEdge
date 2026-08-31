"""H.2 (partial): run the strategy x symbol cells the sweep never covered.

Three cells were in the plan's asset list and never executed: DriftJumpAlpha on
Crash 500 and Jump 100, and every strategy on AUDUSD. Report 08 measured Crash
500 at +0.303 R on random entry alone - the strongest untested candidate in the
programme - so it is worth settling.

This drives the real strategy engine and the real BacktestEngine against the
cached MT5 bars, with gate telemetry ON (the B7 fix), so it doubles as the
end-to-end proof that B7 works through a full run rather than a synthetic slice.
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

CELLS = [
    ("DriftJumpAlpha_v1", "Crash 500 Index"),
    ("DriftJumpAlpha_v1", "Jump 100 Index"),
    ("DriftJumpAlpha_v1", "Crash 1000 Index"),   # control: known-good cell
]

RISK = {
    "risk_per_trade_pct": 1.0, "max_risk_hard_cap_pct": 2.0,
    "max_daily_drawdown_pct": 3.0, "max_weekly_drawdown_pct": 6.0,
    "max_daily_trades": 7, "tp_count": 1, "tp1_rr": 5,
    "be_mode": "RR", "be_trigger_rr": 1, "be_buffer_pips": 5,
    "trail_method_tp1": "NONE", "max_concurrent_positions": 3,
    "max_positions_per_symbol": 3, "min_bars_between_entries": 7,
    "simulate_wicks": True, "min_sl_pips": 10, "max_account_leverage": 100,
}


def load(sym, tf, n=20000):
    f = CACHE / f"{sym.replace(' ', '_')}__{tf}.npz"
    z = np.load(f)
    df = pd.DataFrame({k: z[k][-n:] for k in
                       ("open", "high", "low", "close", "volume")})
    df.index = pd.to_datetime(z["time"][-n:], unit="s", utc=True)
    df["time"] = z["time"][-n:]
    return df


async def run_cell(sid, sym, bars=20000):
    df = load(sym, "M5", bars)
    cfg = UserConfigV2()
    cfg.instrument_settings = [InstrumentSettings(symbol=sym, strategy_id=sid)]
    eng = get_strategy(sid)(cfg)
    eng.is_backtesting = True
    eng.gates.enabled = True                       # B7

    sigs = []
    for i in range(300, len(df)):
        s = await eng.on_bar(sym, "M5", df.iloc[max(0, i - 500):i + 1])
        if s:
            sigs.append({
                "symbol": sym, "direction": s.direction,
                "time": int(df["time"].iloc[i]),
                "entry_price": s.entry_price, "stop_loss": s.stop_loss,
                "take_profit": s.take_profit, "timeframe": "M5",
                "confluence_score": s.confluence_score,
                "metadata": s.metadata or {},
                "confirmations": (s.metadata or {}).get("confirmations", []),
            })
    eng.gates.finish()

    bt = BacktestEngine(RISK)
    res = bt.run(df, sigs, 10000.0, strategy=eng)
    return sigs, res, bt, eng


async def main():
    for sid, sym in CELLS:
        print(f"\n{'=' * 62}\n{sid} x {sym}")
        try:
            sigs, res, bt, eng = await run_cell(sid, sym)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"  FAILED: {e}")
            continue
        trades = res.get("trades", [])
        rs = [t.get("pnl_r") for t in trades if t.get("pnl_r") is not None]
        pnl = res.get("final_balance", 10000) - 10000
        print(f"  signals={len(sigs)}  trades={len(trades)}  "
              f"P&L=${pnl:+,.2f}")
        if rs:
            wins = [r for r in rs if r > 0]
            gl = -sum(r for r in rs if r <= 0)
            print(f"  expectancy={sum(rs)/len(rs):+.3f} R   total={sum(rs):+.1f} R"
                  f"   WR={len(wins)/len(rs):.1%}"
                  f"   PF={(sum(wins)/gl if gl else float('inf')):.2f}")
        funnel = getattr(bt, "rejection_funnel", {}) or {}
        gs = funnel.get("gate_stats") or eng.gates.summary().get("gates", {})
        print(f"  --- gate telemetry (B7) ---")
        print(f"  candidates evaluated: {eng.gates.summary()['candidates_recorded']}")
        for g, v in list(gs.items())[:8]:
            ev, ps = v.get("evaluated", 0), v.get("passed", 0)
            print(f"    {g:26s} eval={ev:6d} pass={ps:6d} "
                  f"({ps/ev:.0%})" if ev else f"    {g}")
        rej = eng.gates.strategy_rejections()
        print(f"  blocked by: {dict(list(rej.items())[:6]) or '{}'}")


if __name__ == "__main__":
    asyncio.run(main())
