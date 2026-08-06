import sys
import os

# Add backend to path so we can import
sys.path.insert(0, r"c:\Users\ikchr\Documents\AlgoEdge")

from backend.risk.compounding import INSTRUMENT_PROFILES

for sym, p in INSTRUMENT_PROFILES.items():
    if not hasattr(p, 'point_value_per_lot'): continue
    vpum = p.point_value_per_lot / p.point_size
    print(f"{sym:10} vpum={vpum:<10.2f} (tick_val={p.point_value_per_lot}, tick_size={p.point_size})")
