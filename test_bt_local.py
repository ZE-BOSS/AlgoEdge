import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.backtester.engine import run_backtest

class MockRequest:
    strategy_id = 'VWAP_v1'
    symbol = 'USDCHF'
    start_date = '2026-08-01'
    end_date = '2026-08-13'
    candle_count = 5000
    timeframe = 'M5'
    risk_per_trade_pct = 1.0
    min_rr = 1.0
    tp_count = 3
    tp1_rr = 1.0
    tp2_rr = 3.0
    tp3_rr = 5.0
    tp4_rr = 0.0
    tp5_rr = 0.0
    tp_splits = [20, 40, 40]
    be_trigger_rr = 0.0
    be_buffer_pips = 0.0
    be_buffer_atr_mult = 0.0
    trail_method_tp2 = 'ATR_TRAIL'
    trail_method_tp3 = 'ATR_TRAIL'
    trail_method_tp4 = 'NONE'
    trail_method_tp5 = 'NONE'
    trail_pips = 0.0
    atr_trail_multiplier = 0.0
    session_filter_enabled = False
    simulate_wicks = True
    manual_bias_overrides = {}
    strategy_params = {
        'vwap_anchor_minutes': 15,
        'momentum_lookback_bars': 4,
        'momentum_threshold_pct': 0.1,
        'sl_points': 80
    }
    prop_firm = {
        'account_mode': 'prop_firm',
        'challenge_type': '2-step',
        'account_size': 15000,
        'initial_balance': 25000,
        'max_lot_sizes': {}
    }
    risk_config = {
        'prop_firm': prop_firm,
        'max_daily_drawdown_pct': 3.0,
        'max_weekly_drawdown_pct': 5.0,
        'max_concurrent_positions': 5,
        'max_positions_per_symbol': 1,
        'max_daily_trades': 7,
        'is_backtest': True
    }
    slippage_pips = 0.0
    commission_per_lot = 0.0
    spread_pips = 0.0
    initial_balance = 25000.0

print(asyncio.run(run_backtest(MockRequest())))
