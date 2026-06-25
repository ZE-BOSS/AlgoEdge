import time
import pandas as pd
import numpy as np
from backend.strategies.smc.market_structure import MarketStructureDetector

detector = MarketStructureDetector()
# Create dummy data
candles = pd.DataFrame({
    'high': np.random.rand(1000),
    'low': np.random.rand(1000),
    'close': np.random.rand(1000),
    'open': np.random.rand(1000),
})
candles.index = pd.date_range("2026-01-01", periods=1000, freq="15min")

start = time.time()
for i in range(5000):
    detector.update(candles)
print(f"Time taken for 5000 updates: {time.time() - start:.2f} seconds")
