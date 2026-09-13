"""Backtest numbers must be the numbers live will see:

  * a trade pays the spread its own entry bar recorded, not one run-wide guess;
  * portfolio runs apply strategy-owned exits exactly like single-symbol runs;
  * Sharpe/Sortino are annualised by the real trade rate, identically in the
    backend (metrics.py) and the frontend (summaryEngine.js);
  * a slot can opt out of the measured per-symbol parameters, and live honours it.
"""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.analytics.metrics import compute_portfolio_stats, trades_per_year
from backend.backtester.engine import BacktestEngine
from backend.core.config_schema import InstrumentSlot, UserConfigV2

ZERO_COSTS = {"commission_per_lot": 0.0, "slippage_pips": 0.0, "exit_slippage_pips": 0.0,
              "stops_level_pips": 0.0, "swap_long_per_lot_per_day": 0.0, "swap_short_per_lot_per_day": 0.0}


def test_trade_pays_its_own_bars_spread_and_a_user_spread_still_wins():
    eng = BacktestEngine({**ZERO_COSTS})              # spread unset -> resolved + per-bar
    pos = {"symbol": "EURUSD", "entry_time": 1_750_000_000}
    eng._note_entry_spread(pos, 7)                     # 7 points = 0.7 pips on EURUSD
    assert pos["entry_spread_pips"] == pytest.approx(0.7)
    costs = eng._costs_for("EURUSD")
    assert eng._spread_pips_for("EURUSD", 1_750_000_000, costs) == pytest.approx(0.7)
    # a different trade (no noted bar spread) falls back to the resolved figure
    assert eng._spread_pips_for("EURUSD", 1_760_000_000, costs) == costs["spread_pips"]
    # floating marks pass no entry time -> resolved figure
    assert eng._spread_pips_for("EURUSD", None, costs) == costs["spread_pips"]

    user = BacktestEngine({**ZERO_COSTS, "spread_pips": 2.5})
    user._note_entry_spread(dict(pos), 7)
    assert user._spread_pips_for("EURUSD", 1_750_000_000, user._costs_for("EURUSD")) == 2.5


def test_the_bar_spread_changes_pnl_by_exactly_the_spread_difference():
    eng = BacktestEngine({**ZERO_COSTS})
    t0, t1 = 1_750_000_000, 1_750_003_600
    base = eng._calc_pnl("BUY", 1.1000, 1.1010, 1.0, "EURUSD", t0, t1)
    eng._note_entry_spread({"symbol": "EURUSD", "entry_time": t0}, 3)     # 0.3 pips
    after = eng._calc_pnl("BUY", 1.1000, 1.1010, 1.0, "EURUSD", t0, t1)
    resolved = eng._costs_for("EURUSD")["spread_pips"]
    # paying 0.3 pips instead of the resolved figure returns (resolved - 0.3) pips
    # of cost: $10 per pip per standard EURUSD lot
    per_pip_per_lot = (after - base) / (resolved - 0.3) if resolved != 0.3 else None
    assert per_pip_per_lot is not None and per_pip_per_lot == pytest.approx(10.0, rel=0.05)


def test_fx_slippage_defaults_are_the_measured_ones_not_the_old_guesses():
    from backend.risk.broker_costs import ASSET_CLASS_COST_DEFAULTS, FOREX_CROSS_COST_DEFAULTS, SYMBOL_COST_OVERRIDES
    assert ASSET_CLASS_COST_DEFAULTS["FOREX"]["slippage_pips"] <= 0.15
    assert FOREX_CROSS_COST_DEFAULTS["slippage_pips"] <= 0.25
    assert SYMBOL_COST_OVERRIDES["NAS100"]["slippage_pips"] <= 2.0


def _trades(n=60, days=180, seed=1):
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2026-01-05", tz="UTC")
    out, bal = [], 10000.0
    for k in range(n):
        e = t0 + pd.Timedelta(days=days * k / n)
        x = e + pd.Timedelta(hours=3)
        pnl = float(rng.normal(20, 120))
        out.append({"entry_time": e.isoformat(), "exit_time": x.isoformat(), "pnl": pnl,
                    "balance_before": bal, "balance_after": bal + pnl})
        bal += pnl
    return out


def test_sharpe_is_annualised_by_the_real_trade_rate():
    tr = _trades(n=60, days=180)
    ppy = trades_per_year(tr)
    assert ppy == pytest.approx(60 / ((179 * 86400 + 3 * 3600) / (365.25 * 86400)), rel=0.02)
    s = compute_portfolio_stats(tr, 10000.0)
    r = np.array([t["pnl"] / 10000.0 for t in tr])
    assert s["sharpe_ratio"] == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(ppy), rel=1e-6)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_frontend_summary_engine_tallies_with_the_backend(tmp_path):
    tr = _trades(n=80, days=240, seed=7)
    for basis in ("STATIC", "BALANCE"):
        py = compute_portfolio_stats(tr, 10000.0, sizing_basis=basis)
        engine = Path("frontend/src/utils/summaryEngine.js").resolve().as_uri()
        script = tmp_path / f"tally_{basis}.mjs"
        script.write_text(
            f"import * as S from '{engine}';\n"
            f"const tr = {json.dumps(tr)};\n"
            f"const s = S.computePeriodStats(tr, 10000, null, '{basis}');\n"
            "console.log(JSON.stringify(s));\n", encoding="utf-8")
        js = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)
        assert js["winRate"] == pytest.approx(py["win_rate"])
        assert js["pnl"] == pytest.approx(py["total_pnl"])
        assert js["profitFactor"] == pytest.approx(py["profit_factor"])
        assert js["sharpe"] == pytest.approx(py["sharpe_ratio"], rel=1e-9)
        assert js["sortino"] == pytest.approx(py["sortino_ratio"], rel=1e-9)
        dd = py["max_drawdown_pct"] if basis == "STATIC" else py["max_drawdown_pct_of_peak"]
        assert js["maxDdPct"] == pytest.approx(dd)
        assert js["maxConsecWins"] == py["max_consecutive_wins"]
        assert js["maxConsecLosses"] == py["max_consecutive_losses"]


def test_slot_can_opt_out_of_measured_params():
    cfg = UserConfigV2.from_dict({"instrument_slots": [
        {"symbol": "GBPJPY", "strategy_id": "ORB_v1", "use_measured_params": False},
        {"symbol": "XAUUSD", "strategy_id": "ORB_v1"},
    ]})
    assert cfg.instrument_slots[0].use_measured_params is False
    assert cfg.instrument_slots[1].use_measured_params is True
    assert InstrumentSlot().use_measured_params is True


def test_portfolio_engine_applies_strategy_owned_exits():
    """An ORB leg in a basket must flatten at its session close, as it does alone."""
    from datetime import datetime

    import pytz

    from backend.backtester.portfolio_engine import PortfolioBacktestEngine
    from backend.strategies.registry import get_strategy
    from backend.strategies.strategy_orb.params import ORBParams

    uk = pytz.timezone("Europe/London")
    idx = pd.date_range(uk.localize(datetime(2025, 7, 2, 7, 0)), periods=120, freq="5min")
    t = (idx.tz_convert("UTC").asi8 // 10**9).astype(np.int64)
    px = np.full(len(t), 190.0)
    df = pd.DataFrame({"time": t, "open": px, "high": px + 0.02, "low": px - 0.02, "close": px,
                       "spread": np.full(len(t), 10)})
    entry_i = int(np.searchsorted(t, int(uk.localize(datetime(2025, 7, 2, 10, 0)).timestamp())))
    sig = {"symbol": "GBPJPY", "_cache_key": "GBPJPY", "strategy_name": "ORB_v1", "direction": "BUY",
           "time": int(t[entry_i - 1]), "entry_price": 190.0, "stop_loss": 189.0, "take_profit": 193.0,
           "timeframe": "M5", "confluence_score": 75, "metadata": {}}
    cfg = UserConfigV2()
    cfg.orb = ORBParams(session="london", breakout_timeframe="M5")
    strat = get_strategy("ORB_v1")(cfg)
    risk = {**ZERO_COSTS, "risk_per_trade_pct": 1.0, "tp_count": 1, "tp1_rr": 3.0, "min_rr": 0.5,
            "max_concurrent_positions": 5, "max_positions_per_symbol": 1, "max_daily_trades": 10,
            "max_daily_drawdown_pct": 50, "max_weekly_drawdown_pct": 50, "be_mode": "NONE",
            "trail_method_tp1": "NONE", "trail_mode": "NONE", "reject_below_confluence": False,
            "confluence_risk_tiers": [(0, 100.0)], "max_risk_hard_cap_pct": 5.0, "multi_position_mode": True}
    res = PortfolioBacktestEngine(risk).run({"GBPJPY": df}, {"GBPJPY": [sig]}, 10000.0,
                                            strategies={"GBPJPY": strat})
    trades = res.get("trades") or []
    assert trades, f"no trade opened: {res.get('rejection_funnel')}"
    assert trades[0]["exit_reason"] == "SESSION_END"
    close_ts = int(uk.localize(datetime(2025, 7, 2, 16, 30)).timestamp())
    exit_ts = pd.Timestamp(trades[0].get("exit_time_iso") or trades[0]["exit_time"]).timestamp() \
        if not isinstance(trades[0]["exit_time"], (int, float)) else trades[0]["exit_time"]
    assert close_ts - 600 <= exit_ts <= close_ts


def test_stop_touched_on_the_entry_bar_fills_at_the_stop_not_on_the_next_bar():
    """Live's stop order exists from the fill. A backtest that first checked it on
    the NEXT bar booked BTCUSD session-pullback stops at -1.35R..-2.37R."""
    eng = BacktestEngine({**ZERO_COSTS, "spread_pips": 0.0})
    pos = {"id": "leg1", "symbol": "EURUSD", "direction": "BUY", "entry_price": 1.1000,
           "stop_loss": 1.0990, "initial_stop_loss": 1.0990, "take_profit": 1.1030,
           "volume": 1.0, "tp_level": 1, "entry_time": 1_750_000_000}
    # entry bar: opens at the fill, trades down through the stop, closes low
    assert eng._check_entry_bar_exit(pos, 1.1000, 1.1002, 1.0985, 1_750_000_000) is True
    assert pos["exit_reason"] == "SL"
    assert pos["exit_price"] >= 1.0985 and pos["exit_price"] <= 1.0990
    assert pos["_exit_time_override"] == 1_750_000_000
    assert pos["pnl"] == pytest.approx((pos["exit_price"] - 1.1000) * 100_000, rel=1e-6)

    untouched = dict(pos, id="leg2")
    for k in ("exit_reason", "exit_price", "_entry_bar_exit", "_exit_time_override", "pnl"):
        untouched.pop(k, None)
    assert eng._check_entry_bar_exit(untouched, 1.1000, 1.1005, 1.0995, 1_750_000_000) is False
    assert "exit_reason" not in untouched
