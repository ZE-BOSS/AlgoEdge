import asyncio, json, websockets
from datetime import datetime, timezone, timedelta
URL="wss://ws.derivws.com/websockets/v3?app_id=1089"
async def req(ws,p):
    await ws.send(json.dumps(p)); return json.loads(await ws.recv())
async def main():
    async with websockets.connect(URL, ping_interval=20, max_size=8*1024*1024) as ws:
        now=datetime.now(timezone.utc)
        print("--- exact tick horizon (BOOM1000): does the request land where asked? ---")
        for days in (365,400,450,500,550,600,650,700):
            s=int((now-timedelta(days=days)).timestamp())
            r=await req(ws,{"ticks_history":"BOOM1000","start":s,"end":s+3600,"count":5000,"style":"ticks"})
            if "error" in r: print(f"  {days:4d}d: ERR {r['error']['message'][:50]}"); continue
            t=r["history"]["times"]
            if not t: print(f"  {days:4d}d: none"); continue
            got=datetime.fromtimestamp(t[0],timezone.utc)
            want=now-timedelta(days=days)
            drift=abs((got-want).total_seconds())/86400
            print(f"  {days:4d}d: first={got:%Y-%m-%d %H:%M} {'OK' if drift<2 else f'FELL BACK TO LATEST (off by {drift:.0f}d)'}")
        print("\n--- same probe on other families at 365d ---")
        for sym in ("CRASH1000","R_75","1HZ75V","stpRNG","JD100","R_100"):
            s=int((now-timedelta(days=365)).timestamp())
            r=await req(ws,{"ticks_history":sym,"start":s,"end":s+3600,"count":5000,"style":"ticks"})
            if "error" in r: print(f"  {sym:10s} ERR {r['error']['message'][:50]}"); continue
            t=r["history"]["times"]
            got=datetime.fromtimestamp(t[0],timezone.utc) if t else None
            print(f"  {sym:10s} {len(t):5d} ticks, first={got:%Y-%m-%d %H:%M}" if t else f"  {sym:10s} none")
asyncio.run(main())
