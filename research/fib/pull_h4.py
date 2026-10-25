"""Pull H4 + M15 history from the local Deriv MT5 terminal into research/fib/cache/*.npz."""
import numpy as np, MetaTrader5 as mt5
from pathlib import Path
OUT = Path(__file__).parent / "cache"; OUT.mkdir(exist_ok=True)
SYMS = ["Step Index", "Step Index 200", "Step Index 300", "Step Index 400", "Step Index 500",
        "Multi Step 2 Index", "Volatility 10 Index", "Volatility 25 Index", "Volatility 50 Index",
        "Volatility 75 Index", "Volatility 100 Index", "Volatility 75 (1s) Index",
        "XAUUSD", "XAGUSD", "US Tech 100", "US SP 500", "Wall Street 30", "GBPJPY", "EURUSD", "BTCUSD"]
assert mt5.initialize(path="C:/Program Files/MetaTrader 5 Terminal/terminal64.exe"), mt5.last_error()
print("maxbars", mt5.terminal_info().maxbars)
for s in SYMS:
    mt5.symbol_select(s, True)
    info = mt5.symbol_info(s)
    if info is None:
        print(s, "MISSING"); continue
    for tfn, tf, n in (("H4", mt5.TIMEFRAME_H4, 20000), ("M15", mt5.TIMEFRAME_M15, 99999)):
        r = mt5.copy_rates_from_pos(s, tf, 0, n)
        if r is None or len(r) == 0:
            print(s, tfn, "none", mt5.last_error()); continue
        np.savez(OUT / f"{s.replace(' ', '_')}_{tfn}.npz", t=r["time"].astype("int64"), o=r["open"], h=r["high"],
                 l=r["low"], c=r["close"], spread=r["spread"].astype("float64") * info.point)
        import datetime as dt
        print(f"{s:26s} {tfn:4s} {len(r):6d} bars  {dt.datetime.utcfromtimestamp(int(r['time'][0])):%Y-%m-%d} -> "
              f"{dt.datetime.utcfromtimestamp(int(r['time'][-1])):%Y-%m-%d}  med spread {np.median(r['spread'])*info.point:g}")
