"""Stage C re-check: do the fundamental providers actually return data right now?

Report 03 concluded Stage C is blocked. That conclusion was reached by reading
the provider code. This tests it against the live services instead, so the claim
rests on observation rather than on my reading.

The question is NOT "is there data today" - it is "is there data with history",
because a backtest needs the value as it stood at each past signal.
"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.abspath("../.."))

from backend.data import providers as P


async def main():
    print("=== provider registry ===")
    names = [n for n in dir(P) if n.endswith("Provider") and not n.startswith("Base")]
    for n in names:
        cls = getattr(P, n)
        key = getattr(cls, "requires_key", None)
        print(f"  {n:32s} note={getattr(cls,'note','')[:60]}")

    print("\n=== live fetch attempts ===")

    # 1. Economic calendar - the only free historical-ish source
    try:
        import httpx
        url = P.ForexFactoryCalendarProvider.URL
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url)
        ev = r.json()
        dates = sorted({e.get("date", "")[:10] for e in ev})
        print(f"  ForexFactory calendar : {len(ev)} events, HTTP {r.status_code}")
        print(f"    date range covered  : {dates[0]} -> {dates[-1]}  ({len(dates)} days)")
        print(f"    url                 : {url}")
    except Exception as e:
        print(f"  ForexFactory calendar : FAILED {e}")

    # 2. Options / GEX
    for cls_name, tick in (("YahooOptionsProvider", "SPY"), ("CBOEOptionsProvider", "SPX")):
        try:
            cls = getattr(P, cls_name)
            inst = cls()
            res = await inst.fetch_options(tick) if hasattr(inst, "fetch_options") else None
            print(f"  {cls_name:22s}: {'data' if res else 'no data'}")
        except Exception as e:
            print(f"  {cls_name:22s}: FAILED {type(e).__name__}: {str(e)[:70]}")

    # 3. Keys
    print("\n=== paid feeds ===")
    for k in ("POLYGON_API_KEY", "DATABENTO_API_KEY"):
        print(f"  {k:20s} {'SET' if os.environ.get(k) else 'NOT SET'}")

    # 4. Storage - the actual blocker
    print("\n=== is any fundamental reading persisted? ===")
    from backend.data import models as M
    tables = [c for c in dir(M) if isinstance(getattr(M, c), type)
              and hasattr(getattr(M, c), "__tablename__")]
    print(f"  tables defined: {len(tables)}")
    for t in tables:
        print(f"    {getattr(M, t).__tablename__}")


if __name__ == "__main__":
    asyncio.run(main())
