import asyncio
from datetime import datetime, timedelta
import pandas as pd

from backend.api.routes.backtest import BacktestRequest, get_db
from backend.core.config_schema import UserConfigV2, DriftJumpAlphaParams
from backend.strategies.strategy_two.engine import DriftJumpAlphaEngine

async def run():
    # Setup test config
    config = UserConfigV2()
    config.drift_jump_alpha = DriftJumpAlphaParams()
    engine = DriftJumpAlphaEngine(config)
    engine.is_backtesting = True
    
    # We don't have MT5 connection in this simple script easily without initialization,
    # so we'll mock the data fetcher or just see if the engine can be instantiated and we can mock on_bar.
    print("Engine instantiated.")

if __name__ == "__main__":
    asyncio.run(run())
