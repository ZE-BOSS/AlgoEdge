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
    count: int = Query(500, le=5000),
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

