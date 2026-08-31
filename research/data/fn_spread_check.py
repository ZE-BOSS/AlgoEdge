"""Are FundedNext's zero spreads real, or an artefact of a closed market?

11 of 18 symbols cached with spread == 0. That is exactly the shape of the false
"zero cost model" claim I retracted for Deriv (bug B8), so it gets verified
rather than assumed.

Three readings per symbol:
  symbol_info().spread      - integer points, what the cache stored
  tick.ask - tick.bid       - the live quote, if there is one
  session state             - is the market actually open right now?
"""
import os
from datetime import datetime, timezone
import MetaTrader5 as mt5
from fn_fetch_cache import FN_SYMBOLS
from fn_probe import connect

if __name__ == "__main__":
    if not connect():
        raise SystemExit(f"connect failed: {mt5.last_error()}")
    now = datetime.now(timezone.utc)
    print(f"now: {now:%Y-%m-%d %H:%M} UTC  (weekday {now.strftime('%A')})\n")
    print(f"{'symbol':10s}{'info.spread':>12s}{'ask-bid(px)':>13s}"
          f"{'in pts':>9s}{'last tick':>22s}{'visible':>9s}")
    for s in FN_SYMBOLS:
        if not mt5.symbol_select(s, True):
            print(f"{s:10s}  symbol_select failed")
            continue
        info = mt5.symbol_info(s)
        t = mt5.symbol_info_tick(s)
        if info is None:
            print(f"{s:10s}  no symbol_info")
            continue
        spr_px = (t.ask - t.bid) if t and t.ask and t.bid else float("nan")
        pts = spr_px / info.point if info.point and spr_px == spr_px else float("nan")
        ts = (datetime.fromtimestamp(t.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
              if t and t.time else "-")
        print(f"{s:10s}{info.spread:12d}{spr_px:13.5f}{pts:9.0f}{ts:>22s}"
              f"{str(bool(info.visible)):>9s}")
    mt5.shutdown()
