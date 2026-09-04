"""Pull real ticks and read off each index's generative signature."""
import asyncio, json, numpy as np, websockets
from datetime import datetime, timezone
URL="wss://ws.derivws.com/websockets/v3?app_id=1089"
SYMS=["BOOM1000","BOOM500","CRASH1000","CRASH500","R_75","stpRNG","JD100","RB100"]
PAGES=8   # 40k ticks each

async def pull(ws,sym,pages=PAGES):
    end="latest"; T=[]; P=[]
    for _ in range(pages):
        await ws.send(json.dumps({"ticks_history":sym,"end":end,"count":5000,"style":"ticks"}))
        r=json.loads(await ws.recv())
        if "error" in r: return None,None,r["error"]["message"]
        h=r["history"]; t=h["times"]; p=h["prices"]
        if not t: break
        T=t+T; P=p+P; end=t[0]-1
    return np.array(T,dtype=np.int64), np.array(P,dtype=float), None

async def main():
    async with websockets.connect(URL,ping_interval=20,max_size=8*1024*1024) as ws:
        for s in SYMS:
            t,p,err=await pull(ws,s)
            if err: print(f"\n=== {s}: ERROR {err[:60]}"); continue
            d=np.diff(p); r=d/p[:-1]
            dt=np.diff(t)
            up,dn=r[r>0],r[r<0]
            print(f"\n=== {s}  {len(p):,} ticks | {(t[-1]-t[0])/3600:.1f}h | tick={np.median(dt):.0f}s")
            print(f"  up-moves {len(up)/len(r):6.2%}  mean {up.mean()*100:+.5f}%   "
                  f"down-moves {len(dn)/len(r):6.2%}  mean {dn.mean()*100:+.5f}%")
            print(f"  drift per tick {r.mean()*100:+.6f}%   sd {r.std()*100:.5f}%   "
                  f"skew {float(((r-r.mean())**3).mean()/r.std()**3):+.2f}  kurt {float(((r-r.mean())**4).mean()/r.std()**4):.1f}")
            # tail events: how rare, how big, how regular
            for tail,lab in ((np.abs(r)>=8*r.std(),"|move|>=8sd"),):
                idx=np.where(tail)[0]
                if len(idx)>3:
                    gaps=np.diff(idx)
                    sgn = "up" if r[idx].mean()>0 else "down"
                    print(f"  {lab}: {len(idx)} events, 1 per {len(r)/len(idx):,.0f} ticks, "
                          f"dir={sgn}, mean {r[idx].mean()*100:+.4f}%, "
                          f"inter-arrival CV={gaps.std()/gaps.mean():.2f}")
                else:
                    print(f"  {lab}: {len(idx)} events (too few)")
            # unique step sizes -> is it a discrete-step process?
            u=np.unique(np.round(np.abs(d),6))
            print(f"  distinct |price step| values: {len(u):,}"
                  + (f"  -> DISCRETE, steps={u[:5]}" if len(u)<=10 else ""))
asyncio.run(main())
