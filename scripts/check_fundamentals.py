#!/usr/bin/env python3
"""
Probe every fundamentals capability and report what is ACTUALLY reachable.

Run this on the machine that has the MT5 terminal and unrestricted internet:

    python scripts/check_fundamentals.py                    # all capabilities
    python scripts/check_fundamentals.py --symbol XAUUSD    # a specific symbol
    python scripts/check_fundamentals.py --gex-ticker SPX   # a specific index

Why it exists
-------------
"Is the fundamentals layer usable?" has three different answers depending on
where you ask from, and they are easy to confuse:

  * a sandbox with no MT5 and an allow-listed network — everything fails, and
    none of those failures mean anything about your setup;
  * your terminal on a weekend — MT5 answers, but the tick window is empty and
    the calendar is next week's;
  * your terminal in session — the only run that tells you what you have.

The one question that matters most is answered near the top of the output:
whether your broker's tick feed carries TRADED SIZE. If it does not — and on the
Deriv feed it does not — then "order flow" is not order flow. `volume` and
`volume_real` are 0 on every tick and `last` is 0.0, so the classifier falls
back to a tick rule on the mid: it counts which way the QUOTE moved. That is a
momentum series, not a record of who traded. Everything downstream of it (CVD,
delta divergence, absorption, the volume profile, VPOC, the bubble overlay)
inherits that limitation, and this script prints it rather than letting a
plausible-looking number imply otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _hdr(t: str) -> None:
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def probe_tick_feed(symbol: str, minutes: int = 60) -> None:
    """The load-bearing question: does this broker's tick feed carry size?"""
    _hdr(f"1. TICK FEED — does {symbol} carry traded size, or only quotes?")
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("  MetaTrader5 package not installed — cannot answer. Run this on the")
        print("  machine with the terminal.")
        return

    if not mt5.initialize():
        print(f"  mt5.initialize() failed: {mt5.last_error()}")
        return
    try:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        ticks = mt5.copy_ticks_range(
            symbol, now - timedelta(minutes=minutes), now, mt5.COPY_TICKS_ALL,
        )
        if ticks is None or len(ticks) == 0:
            print(f"  0 ticks in the last {minutes} minutes.")
            print("  On a weekend this is expected and says nothing about the feed —")
            print("  re-run during the session before concluding anything.")
            return

        import pandas as pd

        df = pd.DataFrame(ticks)
        n = len(df)
        nz_last = int((df.get("last", pd.Series([0] * n)) != 0).sum())
        nz_vol = int((df.get("volume", pd.Series([0] * n)) != 0).sum())
        nz_volr = int((df.get("volume_real", pd.Series([0.0] * n)) != 0).sum())

        print(f"  ticks in window        : {n:,}")
        print(f"  ticks with last != 0   : {nz_last:,}  ({100 * nz_last / n:.1f}%)")
        print(f"  ticks with volume != 0 : {nz_vol:,}  ({100 * nz_vol / n:.1f}%)")
        print(f"  ticks with volume_real : {nz_volr:,}  ({100 * nz_volr / n:.1f}%)")
        print()
        if nz_last == 0 and nz_vol == 0 and nz_volr == 0:
            print("  VERDICT: quote-only feed. There is no traded size and no aggressor.")
            print("  'Order flow' here is a tick rule on the mid — a momentum series.")
            print("  CVD, delta divergence, absorption, volume profile and VPOC are all")
            print("  derived from QUOTE UPDATE COUNTS, not from volume. Treat VPOC as")
            print("  time-at-price, and do not read CVD as institutional positioning.")
        elif nz_volr > 0:
            print("  VERDICT: real traded size present. Lee-Ready classification applies")
            print("  and the order-flow numbers mean what they normally mean.")
        else:
            print("  VERDICT: partial. Traded prices without size, or size without")
            print("  prices — inspect the columns above before trusting any derived value.")
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


async def probe_providers(symbol: str, gex_ticker: str) -> None:
    from backend.data.providers import (
        CAP_CALENDAR, CAP_CORRELATION, CAP_GEX, CAP_OPTIONS_CHAIN,
        CAP_ORDER_BOOK, CAP_ORDER_FLOW, registry,
    )

    _hdr("2. PROVIDER REACHABILITY — one live call per capability")
    calls = [
        (CAP_ORDER_FLOW, {"symbol": symbol, "minutes": 60}),
        (CAP_ORDER_BOOK, {"symbol": symbol}),
        (CAP_CORRELATION, {"symbols": [symbol, "EURUSD", "XAUUSD"]}),
        (CAP_OPTIONS_CHAIN, {"ticker": gex_ticker}),
        (CAP_GEX, {"ticker": gex_ticker}),
        (CAP_CALENDAR, {}),
    ]
    for cap, kw in calls:
        res = await registry.fetch(cap, use_cache=False, **kw)
        status = "OK  " if res.ok else "FAIL"
        print(f"  [{status}] {cap:15} via {res.provider:12} {res.latency_ms:>6.0f}ms")
        if not res.ok:
            print(f"           {res.error}")
            continue
        data = res.data
        if isinstance(data, dict):
            summary = ", ".join(
                f"{k}={len(v) if isinstance(v, (list, dict)) else v}"
                for k, v in list(data.items())[:5]
                if not isinstance(v, (list, dict)) or len(str(v)) < 200
            )
            print(f"           {summary[:150]}")
            if data.get("caveat"):
                print(f"           CAVEAT: {data['caveat']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD",
                    help="broker symbol for the tick/book/order-flow probes")
    ap.add_argument("--gex-ticker", default="SPX",
                    help="INDEX or ETF ticker for options/GEX. Broker CFD names "
                         "(SPX500, NDX100, USTEC) have no options chain and will "
                         "return nothing — that is the ticker being wrong, not the feed.")
    ap.add_argument("--minutes", type=int, default=60)
    args = ap.parse_args()

    probe_tick_feed(args.symbol, args.minutes)
    asyncio.run(probe_providers(args.symbol, args.gex_ticker))

    _hdr("3. READ THIS BEFORE ACTING ON THE ABOVE")
    print("""  A capability answering OK means the pipe works. It does not mean the
  number carries information. In particular:

    order_flow   Only as good as section 1's verdict. On a quote-only feed it
                 is a momentum proxy — useful, but not order flow.
    order_book   An empty book is a broker limitation (Deriv does not publish
                 depth on CFDs), not a bug in this code.
    gex          Real, and published research supports it — but it exists for
                 index/ETF tickers, not for the CFD you actually trade. Read it
                 as a regime input for the underlying, not a level on your chart.
    calendar     Reliable, and the only one of these that is a genuine
                 fundamental rather than a derived statistic.
    correlation  A portfolio risk input. It has never been an entry signal.
""")


if __name__ == "__main__":
    main()
