"""
backend/api/routes/charts.py

OHLCV data and snapshot serving endpoints.
Source: TradingBot_MasterPlan-2.md Section 6
"""


from fastapi import APIRouter, Query

from backend.mt5.data_fetcher import DataFetcher
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["charts"])


@router.get("/charts/{symbol}/{timeframe}")
async def get_chart_data(
    symbol: str,
    timeframe: str,
    # The old `le=5000` was self-imposed, not a broker limit. Measured against
    # the live terminal (build 6140, maxbars=100000): copy_rates_from_pos
    # returns a full 50,000 bars for XAUUSD / Crash 300 / US Tech 100 on M1, M5
    # and M15. M5 at 50k reaches back ~8.5 months and M15 reaches ~2 years,
    # which is what makes a full-history chart possible on a live call.
    # Ceiling is the terminal's own maxbars, which is itself user-configurable.
    count: int = Query(1000, ge=1, le=100000),
):
    """Get historical OHLCV data for chart rendering."""
    logger.info(f"Chart data request: {symbol} {timeframe} count={count}")
    df = await DataFetcher.get_historical_data(symbol, timeframe, count=count)
    if df is None or df.empty:
        logger.warning(f"No chart data for {symbol} {timeframe}")
        return {"candles": []}

    # DataFetcher._normalize_df already converts time to epoch seconds,
    # sorts ascending, and removes duplicates.
    candles = df[["time", "open", "high", "low", "close"]].to_dict(orient="records")
    return {"symbol": symbol, "timeframe": timeframe, "count": len(candles), "candles": candles}

