import sys
sys.path.insert(0, r"C:\Users\ikchr\Documents\AlgoEdge\research\data")
import numpy as np, MetaTrader5 as mt5
from mt5_probe import connect
if not connect(): raise SystemExit("no MT5")
SYMS=["Boom 500 Index","Boom 1000 Index","Crash 500 Index","Crash 1000 Index",
      "Volatility 75 Index","Volatility 100 Index","Step Index","Step Index 500",
      "Jump 25 Index","Jump 100 Index","Range Break 100 Index","Range Break 200 Index"]
print(f"{'symbol':24s}{'point':>10s}{'min stop':>11s}{'as % px':>9s}{'M5 ATR%':>9s}{'minstop/ATR':>12s}")
for s in SYMS:
    i=mt5.symbol_info(s); mt5.symbol_select(s,True)
    r=mt5.copy_rates_from_pos(s,mt5.TIMEFRAME_M5,0,200)
    h,l,c=r['high'].astype(float),r['low'].astype(float),r['close'].astype(float)
    pc=np.concatenate(([c[0]],c[:-1]))
    atr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))[-14:].mean()
    ms=i.trade_stops_level*i.point; px=c[-1]
    print(f"{s:24s}{i.point:10g}{ms:11.4f}{ms/px*100:9.4f}{atr/px*100:9.4f}{ms/atr:12.2f}")
mt5.shutdown()
