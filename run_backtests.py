import asyncio
import json
import os
import pandas as pd
from datetime import datetime, timedelta

# Import engines
from backend.strategies.strategy_three_crt.engine import CRTEngine
from backend.strategies.strategy_six_ny_open_retest.engine import NYOpenRetestEngine
from backend.strategies.strategy_five_bias_ifvg.engine import BiasIFVGEngine
from backend.strategies.strategy_four_htf_fvg_flip.engine import HTFFVGFlipEngine
from backend.core.config_schema import UserConfigV2, CRTConfig, NYOpenRetestConfig, BiasIFVGConfig, HTFFVGFlipConfig

def generate_dummy_data(symbol, count):
    # Generate dummy M5, M15, H4 data
    now = datetime.utcnow().replace(second=0, microsecond=0)
    data = []
    for i in range(count):
        t = now - timedelta(minutes=5 * (count - i))
        data.append({
            "time": t,
            "open": 100.0 + (i % 10),
            "high": 105.0 + (i % 10),
            "low": 95.0 + (i % 10),
            "close": 102.0 + (i % 10),
            "tick_volume": 100
        })
    df = pd.DataFrame(data).set_index("time")
    
    # Resample for M15 and H4
    df_m15 = df.resample('15min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'tick_volume': 'sum'}).dropna()
    df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'tick_volume': 'sum'}).dropna()
    
    return {"M5": df, "M15": df_m15, "H4": df_h4}

async def run_strategy_local(strategy_cls, config, symbol, out_file):
    engine = strategy_cls(config)
    engine.is_backtesting = True
    
    # 3 weeks of M5 candles
    candles = generate_dummy_data(symbol, 6000) 
    
    trades = []
    
    # Simulate a loop through time
    # Just a simple check that it doesn't crash
    df_m5 = candles["M5"]
    df_m15 = candles["M15"]
    df_h4 = candles["H4"]
    
    for i in range(50, len(df_m5)):
        current_time = df_m5.index[i]
        
        # H4
        if current_time in df_h4.index:
            h4_idx = df_h4.index.get_loc(current_time)
            if h4_idx > 10:
                h4_slice = df_h4.iloc[:h4_idx+1]
                await engine.on_bar(symbol, "H4", h4_slice)
                
        # M15
        if current_time in df_m15.index:
            m15_idx = df_m15.index.get_loc(current_time)
            if m15_idx > 10:
                m15_slice = df_m15.iloc[:m15_idx+1]
                await engine.on_bar(symbol, "M15", m15_slice)
                
        # M5
        m5_slice = df_m5.iloc[:i+1]
        sig = await engine.on_bar(symbol, "M5", m5_slice)
        if sig:
            trades.append(sig.model_dump())
            
    print(f"[{strategy_cls.__name__}] Produced {len(trades)} trades (dummy data).")
    
    os.makedirs("results", exist_ok=True)
    with open(f"results/{out_file}", "w") as f:
        json.dump({"total_trades": len(trades), "trades": trades}, f, indent=2)

async def main():
    config = UserConfigV2(
        crt=CRTConfig(),
        ny_open_retest=NYOpenRetestConfig(),
        bias_ifvg=BiasIFVGConfig(),
        htf_fvg_flip=HTFFVGFlipConfig()
    )
    
    await run_strategy_local(CRTEngine, config, "Volatility 75 Index", "CRT_result.json")
    await run_strategy_local(NYOpenRetestEngine, config, "XAUUSD", "NYOpenRetest_v1_results.json")
    await run_strategy_local(BiasIFVGEngine, config, "Volatility 75 Index", "BiasIFVG_v1_result.json")
    await run_strategy_local(HTFFVGFlipEngine, config, "Volatility 75 Index", "HTFFVGFlip_v1_result.json")

if __name__ == "__main__":
    asyncio.run(main())
