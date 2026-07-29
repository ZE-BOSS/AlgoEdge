import asyncio
from datetime import datetime, timezone
import pandas as pd
import MetaTrader5 as mt5

from backend.strategies.strategy_two.engine import DriftJumpAlphaEngine
from backend.core.config_schema import UserConfigV2, DriftJumpAlphaParams
from backend.mt5.data_fetcher import DataFetcher

async def run():
    mt5.initialize()
    
    config = UserConfigV2()
    config.drift_jump_alpha = DriftJumpAlphaParams()
    engine = DriftJumpAlphaEngine(config)
    engine.is_backtesting = True
    
    symbol = "Crash 1000 Index"
    tf = "M5"
    
    print("Fetching data...")
    try:
        candles = await DataFetcher.get_historical_data(symbol, tf, count=5000)
    except Exception as e:
        print(f"Fetch failed: {e}")
        return
        
    print(f"Fetched {len(candles)} candles.")
    
    reasons = {}
    signals = 0
    
    for i in range(100, len(candles)):
        slice_df = candles.iloc[:i]
        
        # We need to hack log_event to capture reasons if it's logging them
        # Let's temporarily override it
        engine.run_logs = []
        
        signal = await engine.on_bar(symbol, tf, slice_df)
        
        if signal:
            signals += 1
            print(f"SIGNAL at {slice_df.iloc[-1]['time']}: {signal.direction}")
            
        for log in engine.run_logs:
            msg = log["message"]
            if "Gap pct" in msg and "Blocking" in msg:
                reasons["gap_pct_block"] = reasons.get("gap_pct_block", 0) + 1
            elif "weak trend" in msg:
                reasons["weak_trend"] = reasons.get("weak_trend", 0) + 1
            elif "Drift Regime UP inactive" in msg:
                reasons["regime_inactive"] = reasons.get("regime_inactive", 0) + 1
            elif "Waiting for BULLISH trend" in msg:
                reasons["waiting_bullish_trend"] = reasons.get("waiting_bullish_trend", 0) + 1
            elif "Bullish drift requires bullish close" in msg:
                reasons["not_bullish_close"] = reasons.get("not_bullish_close", 0) + 1
            elif "No previous swing high breached" in msg:
                reasons["no_swing_breach"] = reasons.get("no_swing_breach", 0) + 1
                
        # Manually check silent returns
        current_bar = slice_df.iloc[-1]
        atr_val = current_bar.get('atr', 1)
        if 'ema_fast' in current_bar:
            dist_to_fast = abs(current_bar['close'] - current_bar['ema_fast'])
            max_dist = 1.0 * atr_val
            if dist_to_fast > max_dist:
                reasons["dist_to_fast"] = reasons.get("dist_to_fast", 0) + 1
                
    print(f"Total signals: {signals}")
    for k, v in reasons.items():
        print(f"{k}: {v}")
        
if __name__ == "__main__":
    asyncio.run(run())
