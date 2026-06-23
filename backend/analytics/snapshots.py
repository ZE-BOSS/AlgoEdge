"""
backend/analytics/snapshots.py

Entry/exit chart snapshot generation using mplfinance.
Source: TradingBot_MasterPlan-2.md Section 8 — Chart Snapshot System
"""

import os
from typing import List, Dict, Any, Optional
from pathlib import Path

import pandas as pd
from backend.utils.logger import get_logger
from backend.config import settings

logger = get_logger(__name__)

try:
    import mplfinance as mpf
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    logger.warning("mplfinance/matplotlib not available — snapshots disabled")


def generate_trade_snapshot(
    symbol: str,
    timeframe: str,
    candles: pd.DataFrame,
    order_blocks: List[Dict[str, Any]],
    fvgs: List[Dict[str, Any]],
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    direction: str,
    snapshot_type: str,
    trade_id: str,
) -> Optional[str]:
    """
    Generate and save a chart snapshot with SMC markup.
    Returns file path or None if matplotlib is unavailable.
    Source: TradingBot_MasterPlan-2.md — generate_trade_snapshot
    """
    if not HAS_MPL:
        return None

    if candles.empty:
        return None

    # Ensure candles have proper index for mplfinance
    df = candles.copy()
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)

    # Rename columns to mplfinance standard
    col_map = {"open": "Open", "high": "High", "low": "Low", "close": "Close"}
    if "tick_volume" in df.columns:
        col_map["tick_volume"] = "Volume"
    elif "volume" in df.columns:
        col_map["volume"] = "Volume"
    df.rename(columns=col_map, inplace=True)

    # Build additional plots for entry/SL/TP lines
    addplots = []

    entry_line = pd.Series(entry_price, index=df.index)
    addplots.append(mpf.make_addplot(entry_line, color='#2196F3', linestyle='--', width=1.5))

    sl_line = pd.Series(stop_loss, index=df.index)
    addplots.append(mpf.make_addplot(sl_line, color='#F44336', linestyle=':', width=1))

    tp_line = pd.Series(take_profit, index=df.index)
    addplots.append(mpf.make_addplot(tp_line, color='#4CAF50', linestyle=':', width=1))

    # Plot
    has_volume = "Volume" in df.columns
    fig, axes = mpf.plot(
        df,
        type='candle',
        style='charles',
        addplot=addplots,
        volume=has_volume,
        returnfig=True,
        figsize=(14, 8),
        title=f"{symbol} {timeframe} — {snapshot_type} | {direction}",
    )

    ax = axes[0]

    # Draw Order Block rectangles
    for ob in order_blocks:
        try:
            start = ob.get("start_idx", 0)
            end = ob.get("end_idx", start + 3)
            rect = mpatches.FancyBboxPatch(
                (start, ob["bottom"]),
                end - start,
                ob["top"] - ob["bottom"],
                boxstyle="square,pad=0",
                linewidth=1,
                edgecolor='#2196F3' if ob.get("type") == "BULLISH" else '#F44336',
                facecolor='#BBDEFB' if ob.get("type") == "BULLISH" else '#FFCDD2',
                alpha=0.3,
            )
            ax.add_patch(rect)
        except (KeyError, TypeError):
            pass

    # Draw FVG zones
    for fvg in fvgs:
        try:
            start = fvg.get("start_idx", 0)
            end = fvg.get("end_idx", start + 3)
            rect = mpatches.Rectangle(
                (start, fvg.get("bottom", fvg.get("low", 0))),
                end - start,
                fvg.get("top", fvg.get("high", 0)) - fvg.get("bottom", fvg.get("low", 0)),
                linewidth=0,
                facecolor='#FFF9C4',
                alpha=0.25,
            )
            ax.add_patch(rect)
        except (KeyError, TypeError):
            pass

    # Save to disk
    snapshot_dir = settings.snapshots_dir / symbol
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    filepath = snapshot_dir / f"{trade_id}_{snapshot_type.lower()}.png"
    fig.savefig(str(filepath), dpi=120, bbox_inches='tight')
    plt.close(fig)

    logger.info(f"Snapshot saved: {filepath}")
    return str(filepath)
