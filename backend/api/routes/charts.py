"""
backend/api/routes/charts.py

OHLCV data and snapshot serving endpoints.
Source: TradingBot_MasterPlan-2.md Section 6
"""

from fastapi import APIRouter, Query
from typing import Optional

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
    df = await DataFetcher.get_historical_data(symbol, timeframe, count=count)
    if df is None or df.empty:
        return {"candles": []}

    # lightweight-charts expects UNIX timestamps (seconds), not ISO strings.
    # pd.to_datetime produces Timestamp objects that serialize as ISO strings
    # (e.g. "2026-06-10T22:00:00") which lightweight-charts cannot parse.
    import pandas as pd
    if pd.api.types.is_datetime64_any_dtype(df["time"]):
        df["time"] = df["time"].astype("int64") // 10**9
    elif not pd.api.types.is_integer_dtype(df["time"]):
        # Fallback: try to convert whatever format to epoch seconds
        df["time"] = pd.to_datetime(df["time"]).astype("int64") // 10**9

    candles = df[["time", "open", "high", "low", "close"]].to_dict(orient="records")
    return {"symbol": symbol, "timeframe": timeframe, "count": len(candles), "candles": candles}
