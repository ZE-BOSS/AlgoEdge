import pandas as pd
import asyncio
from backend.analytics.snapshots import generate_trade_snapshot_b64

async def main():
    # create dummy df
    dates = pd.date_range(start='2026-06-01', periods=80, freq='5min')
    df = pd.DataFrame({
        'time': dates.astype('int64') // 10**9,
        'open': [100]*80,
        'high': [105]*80,
        'low': [95]*80,
        'close': [101]*80,
        'volume': [1000]*80
    })
    
    b64 = generate_trade_snapshot_b64(
        symbol="TEST",
        timeframe="M5",
        candles=df,
        order_blocks=[],
        fvgs=[],
        entry_price=100.5,
        stop_loss=90.0,
        take_profit=110.0,
        direction="BUY",
        snapshot_type="ENTRY",
        trade_id="test1234"
    )
    if b64:
        print(f"B64 generated: {len(b64)} chars")
        print(b64[:50])
    else:
        print("B64 is None")

asyncio.run(main())
