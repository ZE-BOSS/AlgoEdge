"""
backend/strategies/smc/asian_range.py

Module to detect the Asian Range (Accumulation phase) and check for liquidity sweeps
during the London open (Manipulation phase).
"""
import pandas as pd
from datetime import datetime, timezone
from backend.utils.timeutils import get_local_time

class AsianRange:
    def __init__(self, start_hour: int = 18, end_hour: int = 0):
        """
        start_hour: Start of Asian Range (Local Time)
        end_hour: End of Asian Range (Local Time)
        """
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.high = 0.0
        self.low = float("inf")
        self.is_mapped = False
        self.last_mapped_date = None

    def update(self, candles: pd.DataFrame) -> dict:
        """
        Scan recent candles to map the Asian Range.
        Returns a dictionary with the range high, low, and sweep status.
        """
        if candles.empty:
            return {"mapped": False}

        # We look at the last day of candles
        recent = candles.tail(100)  # Enough for M15/M5 to cover a day
        
        current_dt = None
        
        # We need to find candles that fall into the Asian Range window
        range_high = 0.0
        range_low = float("inf")
        found = False
        
        for idx, row in recent.iterrows():
            ts = 0
            if "time" in row:
                ts = row["time"]
            elif hasattr(idx, 'timestamp'):
                ts = idx.timestamp()
            elif isinstance(idx, (int, float)):
                ts = idx
                
            if ts == 0:
                continue
                
            # Handle if ts is already a pandas Timestamp
            if hasattr(ts, 'timestamp'):
                ts = ts.timestamp()
                
            dt = datetime.fromtimestamp(float(ts), timezone.utc)
            local_dt = get_local_time(dt)
            
            # Save the last candle's date to track resets
            current_dt = local_dt
            
            hour = local_dt.hour
            
            # Check if hour is within Asian range
            in_range = False
            if self.start_hour > self.end_hour:
                in_range = hour >= self.start_hour or hour < self.end_hour
            else:
                in_range = self.start_hour <= hour < self.end_hour
                
            if in_range:
                if row["high"] > range_high:
                    range_high = row["high"]
                if row["low"] < range_low:
                    range_low = row["low"]
                found = True

        if found:
            self.high = range_high
            self.low = range_low
            self.is_mapped = True
            
            if current_dt:
                self.last_mapped_date = current_dt.date()

        return {
            "mapped": self.is_mapped,
            "high": self.high,
            "low": self.low
        }

    def check_sweep(self, current_price: float, direction: str) -> bool:
        """
        Check if the current price has swept the Asian range.
        If BUY (long), we want to see price sweep BELOW the Asian low (inducement).
        If SELL (short), we want to see price sweep ABOVE the Asian high.
        """
        if not self.is_mapped:
            return True # If not mapped yet, bypass (or we could return False depending on strictness)
            
        if direction in ("BUY", "BULLISH"):
            return current_price <= self.low
        elif direction in ("SELL", "BEARISH"):
            return current_price >= self.high
        return False
