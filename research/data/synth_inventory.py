"""Inventory every synthetic index Deriv-MT5 exposes, and how deep its history goes."""
import sys, re
sys.path.insert(0, r"C:\Users\ikchr\Documents\AlgoEdge\research\data")
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
from mt5_probe import connect

if not connect():
    raise SystemExit("no MT5")

syms = mt5.symbols_get()
pat = re.compile(r"(Volatility|Boom|Crash|Jump|Step|Range Break|DEX|Drift Switch|Multi Step|Skewness|Bear Market|Bull Market)", re.I)
synth = sorted({s.name for s in syms if pat.search(s.name)})
print(f"TOTAL SYMBOLS={len(syms)}  SYNTHETIC MATCHES={len(synth)}\n")
for s in synth:
    print("   ", s)
