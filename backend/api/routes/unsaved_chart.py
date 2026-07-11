@router.get("/backtest_result/trade/{group_id}/chart")
async def get_unsaved_trade_chart(
    group_id: str,
    current_user: User = Depends(get_current_user),
):
    """Fetch massive chart data for an unsaved trade from the current running/completed backtest state."""
    state = await _get_state()
    if not state or not state.get("result"):
        raise HTTPException(status_code=404, detail="No active or completed backtest found")
        
    result = state["result"]
    trades = result.get("grouped_trades", [])
    
    # Find the trade by group_id
    trade = next((t for t in trades if t.get("group_id") == group_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found in current backtest")
        
    return {
        "chart_data": trade.get("chart_data", []),
        "chart_data_m15": trade.get("chart_data_m15", []),
        "chart_data_m5": trade.get("chart_data_m5", [])
    }
