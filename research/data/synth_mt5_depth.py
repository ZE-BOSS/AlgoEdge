"""Per-timeframe depth for the synthetic universe. count capped at 99,999 (maxbars)."""
import sys, time
sys.path.insert(0, r"C:\Users\ikchr\Documents\AlgoEdge\research\data")
from datetime import datetime, timezone
import MetaTrader5 as mt5
from mt5_probe import connect
if not connect(): raise SystemExit("no MT5")
CAP = 99999
CORE = ["Boom 300 Index","Boom 500 Index","Boom 1000 Index",
        "Crash 300 Index","Crash 500 Index","Crash 1000 Index",
        "Volatility 10 Index","Volatility 25 Index","Volatility 75 Index","Volatility 100 Index",
        "Volatility 75 (1s) Index",
        "Step Index","Step Index 200","Step Index 500",
        "Jump 10 Index","Jump 25 Index","Jump 50 Index","Jump 75 Index","Jump 100 Index",
        "Range Break 100 Index","Range Break 200 Index",
        "DEX 900 UP Index","Drift Switch Index 30"]
TFS=[("M1",mt5.TIMEFRAME_M1),("M5",mt5.TIMEFRAME_M5),("M15",mt5.TIMEFRAME_M15),
     ("H1",mt5.TIMEFRAME_H1),("D1",mt5.TIMEFRAME_D1)]
print(f"{'symbol':26s}" + "".join(f"{n+' bars':>12s}{n+' from':>12s}" for n,_ in TFS[:1])
      + "".join(f"{n:>10s}" for n,_ in TFS[1:]) + f"{'inception':>12s}{'yrs':>6s}")
for s in CORE:
    if mt5.symbol_info(s) is None: print(f"{s:26s} NOT FOUND",flush=True); continue
    mt5.symbol_select(s, True)
    row=f"{s:26s}"; inc=None; last=None
    for n,tf in TFS:
        r = mt5.copy_rates_from_pos(s, tf, 0, CAP)
        c = len(r) if r is not None else 0
        if n=="M1":
            f = datetime.fromtimestamp(r[0][0],tz=timezone.utc).strftime("%Y-%m-%d") if c else "-"
            row += f"{c:12,d}{f:>12s}"
        else:
            row += f"{c:10,d}"
        if c and (n=="D1" or (inc is None and n=="D1")):
            inc = datetime.fromtimestamp(r[0][0],tz=timezone.utc); last = datetime.fromtimestamp(r[-1][0],tz=timezone.utc)
    if inc: row += f"{inc.strftime('%Y-%m-%d'):>12s}{(last-inc).days/365.25:6.1f}"
    else:   row += f"{'-':>12s}{'-':>6s}"
    print(row, flush=True)
mt5.shutdown()
