"""Spread toll per family: the gate that decides what is worth researching at all."""
import sys
sys.path.insert(0, r"C:\Users\ikchr\Documents\AlgoEdge\research\data")
import numpy as np, MetaTrader5 as mt5
from mt5_probe import connect
if not connect(): raise SystemExit("no MT5")
SYMS=["Boom 300 Index","Boom 500 Index","Boom 1000 Index",
      "Crash 300 Index","Crash 500 Index","Crash 1000 Index",
      "Volatility 10 Index","Volatility 25 Index","Volatility 75 Index","Volatility 100 Index",
      "Volatility 75 (1s) Index","Step Index","Step Index 500",
      "Jump 10 Index","Jump 25 Index","Jump 50 Index","Jump 100 Index",
      "Range Break 100 Index","Range Break 200 Index","Drift Switch Index 30"]
print(f"{'symbol':26s}{'price':>12s}{'spread%':>9s}{'M5 ATR%':>9s}{'cost@1xATR':>11s}{'cost@3xATR':>11s}{'min_lot':>8s}{'stops_lvl':>10s}")
rows=[]
for s in SYMS:
    i=mt5.symbol_info(s)
    if i is None: print(f"{s:26s} NOT FOUND"); continue
    mt5.symbol_select(s,True)
    r=mt5.copy_rates_from_pos(s,mt5.TIMEFRAME_M5,0,5000)
    if r is None or len(r)<100: print(f"{s:26s} no bars"); continue
    h,l,c=r['high'].astype(float),r['low'].astype(float),r['close'].astype(float)
    pc=np.concatenate(([c[0]],c[:-1]))
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    atr=tr[-14:].mean(); px=c[-1]
    sp=i.spread*i.point
    rows.append((s,px,sp/px*100,atr/px*100,sp/atr,sp/(3*atr),i.volume_min,i.trade_stops_level))
    print(f"{s:26s}{px:12,.2f}{sp/px*100:9.4f}{atr/px*100:9.4f}{sp/atr:11.3f}{sp/(3*atr):11.3f}"
          f"{i.volume_min:8g}{i.trade_stops_level:10d}", flush=True)
print("\ncost@NxATR = fraction of R paid to the broker with an N x M5-ATR stop.")
print("report 08 rule: an atom is worth ~0.03R; anything above ~0.10R needs a very strong edge.")
best=sorted(rows,key=lambda x:x[4])
print("\ncheapest at 1xATR:", ", ".join(f"{b[0].replace(' Index','')} {b[4]:.3f}R" for b in best[:6]))
print("dearest  at 1xATR:", ", ".join(f"{b[0].replace(' Index','')} {b[4]:.3f}R" for b in best[-5:]))
mt5.shutdown()
