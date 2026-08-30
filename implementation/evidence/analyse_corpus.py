import warnings; warnings.filterwarnings("ignore")
import json, glob, os
import pandas as pd, numpy as np
from load_corpus import build
pd.set_option("display.width", 200)
L, G, R, pips = build()

def hdr(t): print("\n" + "="*88 + "\n" + t + "\n" + "="*88)

hdr("0. CORPUS")
print(R[["run","strategy","symbol","start","end","candles","bal1"]].to_string(index=False))
print(f"\nlegs={len(L)}  groups={len(G)}  span {L.entry_dt.min().date()} .. {L.entry_dt.max().date()}")

hdr("1. PER-STRATEGY, true R recomputed against entry-time risk (group level)")
def strat_tbl(df, by="strategy"):
    r = df.groupby(by).apply(lambda g: pd.Series({
        "n": len(g),
        "win%": 100*(g.true_R > 0).mean(),
        "exp_R": g.true_R.mean(),
        "med_R": g.true_R.median(),
        "stdev_R": g.true_R.std(),
        "tot_R": g.true_R.sum(),
        "pnl$": g.pnl.sum(),
        "PF": g.pnl[g.pnl>0].sum() / abs(g.pnl[g.pnl<0].sum() or 1),
        "med_MFE_R": g.mfe_R.median(),
        "p75_MFE_R": g.mfe_R.quantile(0.75),
    }), include_groups=False)
    return r.sort_values("exp_R", ascending=False).round(3)
print(strat_tbl(G))
print("\nPOOLED:", f"n={len(G)} win%={100*(G.true_R>0).mean():.1f} exp_R={G.true_R.mean():+.4f} tot_R={G.true_R.sum():+.1f} pnl=${G.pnl.sum():,.0f}")

hdr("2. EXIT ATTRIBUTION (leg level) — where the money goes and what was left on the table")
ex = L.groupby("exit_reason").apply(lambda g: pd.Series({
    "legs": len(g),
    "share%": 100*len(g)/len(L),
    "med_MFE_R": g.mfe_R.median(),
    "p75_MFE_R": g.mfe_R.quantile(0.75),
    "pct>2R": 100*(g.mfe_R>=2).mean(),
    "pct>3R": 100*(g.mfe_R>=3).mean(),
    "med_realised_R": g.realised_R.median(),
    "pnl$": g.pnl.sum(),
}), include_groups=False).sort_values("legs", ascending=False).round(2)
print(ex)

hdr("3. MFE DISTRIBUTION OF ALL GROUPS (what R levels are actually reachable)")
qs=[0.1,0.25,0.5,0.6,0.7,0.75,0.8,0.9,0.95,0.99]
print("all groups MFE_R quantiles:"); print(G.mfe_R.quantile(qs).round(2).to_string())
print("\nby strategy, share of groups reaching each R level:")
levels=[0.5,1.0,1.5,2.0,2.5,3.0,4.0,5.0,6.0,8.0,10.0]
rows=[]
for s,g in G.groupby("strategy"):
    rows.append({"strategy":s,"n":len(g), **{f">={l}R": round(100*(g.mfe_R>=l).mean(),1) for l in levels}})
rows.append({"strategy":"ALL","n":len(G), **{f">={l}R": round(100*(G.mfe_R>=l).mean(),1) for l in levels}})
print(pd.DataFrame(rows).to_string(index=False))


hdr("4. POOLED SINGLE-TP GRID (dominated by VWAP: 1034/1326 groups)")
def rr_grid(g, levels):
    rows=[]
    for k in levels:
        win=g.mfe_R>=k; loss=(~win)&(g.mae_R>=1.0); cens=~(win|loss); n=len(g)
        base=np.where(win,k,np.where(loss,-1.0,np.nan))
        rows.append({"TP_R":k,"hit%":round(100*win.mean(),1),"SL%":round(100*loss.mean(),1),
            "cens%":round(100*cens.mean(),1),
            "exp_R@cens=0":round(np.nansum(np.where(cens,0.0,base))/n,4),
            "exp_R@cens=real":round(np.nansum(np.where(cens,g.true_R,base))/n,4),
            "exp_R@cens=-1":round(np.nansum(np.where(cens,-1.0,base))/n,4)})
    return pd.DataFrame(rows)
print(rr_grid(G,[0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.5,3.0,4.0,5.0]).to_string(index=False))

hdr("7. EXIT REASON x STRATEGY (leg share %) — why the grid is censored")
piv = pd.crosstab(L.strategy_id, L.exit_reason, normalize="index").mul(100).round(1)
print(piv.to_string())
print("\nleg counts:"); print(pd.crosstab(L.strategy_id, L.exit_reason).to_string())

hdr("8. WHAT BREAK-EVEN COSTS — groups that reached B but finished flat or negative")
for b in [0.5,0.75,1.0,1.25,1.5,2.0,2.5,3.0]:
    sub=G[G.mfe_R>=b]
    if len(sub)<10: continue
    print(f"  reached {b:>4}R: n={len(sub):4d}  finished<=0R: {100*(sub.true_R<=0).mean():5.1f}%   "
          f"mean true_R {sub.true_R.mean():+.3f}   median MFE beyond {sub.mfe_R.median():.2f}R")
print("\n  BE_SL legs only:", len(L[L.exit_reason=='BE_SL']),
      " median MFE", round(L[L.exit_reason=='BE_SL'].mfe_R.median(),2),
      "R;  share that had exceeded 2R:", round(100*(L[L.exit_reason=='BE_SL'].mfe_R>=2).mean(),1),"%")

hdr("9. SESSION-END FORCE CLOSE — the largest single exit bucket after SL")
se=L[L.exit_reason=='SESSION_END']
print(f"legs={len(se)} ({100*len(se)/len(L):.1f}%)  total pnl=${se.pnl.sum():,.0f}  "
      f"median realised R={se.realised_R.median():+.3f}  mean={se.realised_R.mean():+.3f}")
print("by strategy:")
print(se.groupby('strategy_id').apply(lambda g: pd.Series({
    "legs":len(g), "med_R":g.realised_R.median(), "mean_R":g.realised_R.mean(),
    "pnl$":g.pnl.sum(), "med_MFE_R":g.mfe_R.median(), "%MFE>1R":100*(g.mfe_R>=1).mean(),
}), include_groups=False).round(3).to_string())
print("\nbars held at session-end vs other exits:")
print(L.groupby('exit_reason').bars_held.describe()[['count','25%','50%','75%','max']].round(1).to_string())

hdr("10. ENTRY HOUR (stored feed clock, UTC field) — pooled group win rate & expectancy")
h=G.groupby('hour').apply(lambda g: pd.Series({"n":len(g),"win%":100*(g.true_R>0).mean(),
    "exp_R":g.true_R.mean(),"pnl$":g.pnl.sum()}), include_groups=False).round(3)
print(h[h.n>=20].to_string())

hdr("11. INSTRUMENT x STRATEGY expectancy (true R, group level)")
ix=G.groupby(['strategy','symbol']).apply(lambda g: pd.Series({"n":len(g),
    "win%":100*(g.true_R>0).mean(),"exp_R":g.true_R.mean(),"pnl$":g.pnl.sum()}),
    include_groups=False).round(3)
print(ix.to_string())


# resolved cost model per run
costs={}
for p in sorted(glob.glob("/home/user/AlgoEdge/debug/*/*.json")):
    d=json.load(open(p)); cm=d.get("cost_model") or {}
    for sym,c in cm.items(): costs[sym]=c

hdr("12. BROKER FRICTION vs. THE STOP IT IS BEING CHARGED AGAINST")
rows=[]
for (strat,sym),g in G.groupby(["strategy","symbol"]):
    c=costs.get(sym)
    if not c: continue
    pip=pips[sym]
    rt_price=(c["spread_pips"] + 2*c["slippage_pips"])*pip      # spread once + slippage both sides
    med_risk=g.risk_price.median()
    rows.append({"strategy":strat,"symbol":sym,"n":len(g),
        "spread_pips":c["spread_pips"],"slip_pips":c["slippage_pips"],
        "round_trip_price":round(rt_price,5),
        "median_SL_price":round(med_risk,5),
        "friction_R":round(rt_price/med_risk,3),
        "geo_exp_R":round(g.true_R.mean(),3),
        "cash_exp_R":round((g.pnl/(0.01*25000)).mean(),3),
        "gap_R":round((g.pnl/(0.01*25000)).mean()-g.true_R.mean(),3),
        "pnl$":round(g.pnl.sum())})
F=pd.DataFrame(rows).sort_values("friction_R",ascending=False)
print(F.to_string(index=False))
print("""
  friction_R = (spread + 2x slippage) / median stop distance = the fraction of ONE R
               handed to the broker on every single round trip, before the market moves.
  geo_exp_R  = expectancy measured on price alone (costs excluded).
  cash_exp_R = realised P&L / (1% of 25k). gap_R = cash - geo = all-in realised drag.""")

hdr("13. SAME, POOLED PER STRATEGY")
rows=[]
for strat,g in G.groupby("strategy"):
    fr=[]
    for sym,gs in g.groupby("symbol"):
        c=costs.get(sym)
        if c: fr.append(((c["spread_pips"]+2*c["slippage_pips"])*pips[sym]/gs.risk_price.median(), len(gs)))
    wf=sum(a*b for a,b in fr)/sum(b for _,b in fr) if fr else np.nan
    rows.append({"strategy":strat,"n":len(g),"wtd_friction_R":round(wf,3),
                 "geo_exp_R":round(g.true_R.mean(),3),
                 "cash_exp_R":round((g.pnl/(0.01*25000)).mean(),3),
                 "gap_R":round((g.pnl/(0.01*25000)).mean()-g.true_R.mean(),3)})
print(pd.DataFrame(rows).sort_values("gap_R").to_string(index=False))

hdr("14. STOP DISTANCE IN R-TERMS OF FRICTION — how wide must a stop be to be tradeable?")
print("For friction to cost less than 10% of one R, the stop must be >= 10 x round-trip cost.")
for sym,c in sorted(costs.items()):
    pip=pips.get(sym)
    if not pip: continue
    rt=(c["spread_pips"]+2*c["slippage_pips"])*pip
    med=L[L.symbol==sym].risk_price.median()
    print(f"  {sym:8} round-trip {rt:>10.5f}   median stop used {med:>10.5f}  "
          f"({med/rt:>5.1f}x cost)   min viable stop @10x = {10*rt:>10.5f}")

hdr("15. HOLDING TIME vs SWAP — is overnight financing material?")
for sym,c in sorted(costs.items()):
    sub=L[L.symbol==sym]
    if not len(sub): continue
    days=sub.duration_minutes.median()/1440
    print(f"  {sym:8} median hold {days*24:>6.1f}h  swap_long {c['swap_long_per_lot_per_day']:>8}  "
          f"swap_short {c['swap_short_per_lot_per_day']:>8} /lot/day   "
          f"share held >24h: {100*(sub.duration_minutes>1440).mean():>4.1f}%")


costs={}
for p in sorted(glob.glob("/home/user/AlgoEdge/debug/*/*.json")):
    for s,c in (json.load(open(p)).get("cost_model") or {}).items(): costs[s]=c

# friction per group, split into spread and modelled slippage
def fric(g):
    """Total modelled round-trip cost (spread + slippage both sides) as a fraction of one R."""
    return frac(g,"spread")+frac(g,"slip")

def frac(g,which):
    out=[]
    for sym,gs in g.groupby("symbol"):
        c=costs.get(sym)
        if not c: continue
        pip=pips[sym]
        v = c["spread_pips"]*pip if which=="spread" else 2*c["slippage_pips"]*pip
        out.append((v/gs.risk_price.median(), len(gs)))
    return sum(a*b for a,b in out)/sum(b for _,b in out) if out else np.nan

hdr("16. NET EXPECTANCY AFTER FRICTION — 'which risk:reward is better', per strategy")
levels=[0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.5,3.0,4.0,5.0]
for s,g in G.groupby("strategy"):
    fs, fl = frac(g,"spread"), frac(g,"slip")
    f = fs+fl
    rows=[]
    for k in levels:
        win=g.mfe_R>=k; loss=(~win)&(g.mae_R>=1.0); cens=~(win|loss); n=len(g)
        base=np.where(win,k,np.where(loss,-1.0,np.nan))
        gross=np.nansum(np.where(cens,0.0,base))/n
        rows.append({"TP_R":k,"hit%":round(100*win.mean(),1),"cens%":round(100*cens.mean(),1),
                     "gross_R":round(gross,4),"net_R(spread only)":round(gross-fs,4),
                     "net_R(all-in)":round(gross-f,4)})
    t=pd.DataFrame(rows)
    b=t.loc[t["net_R(all-in)"].idxmax()]
    print(f"\n--- {s}  n={len(g)}  friction: spread {fs:.3f}R + modelled slippage {fl:.3f}R = {f:.3f}R/round trip")
    print(t.to_string(index=False))
    print(f"    best all-in TP = {b.TP_R}R -> {b['net_R(all-in)']:+.3f}R   "
          f"(spread-only best = {t.loc[t['net_R(spread only)'].idxmax(),'TP_R']}R -> "
          f"{t['net_R(spread only)'].max():+.3f}R)")

hdr("17. HOW MUCH OF MFE EACH EXIT ACTUALLY CAPTURES")
cap=L[L.mfe_R>0.05].groupby("exit_reason").apply(lambda g: pd.Series({
    "legs":len(g),"med_MFE_R":g.mfe_R.median(),"med_realised_R":g.realised_R.median(),
    "capture%":100*(g.realised_R/g.mfe_R).median()}), include_groups=False).round(2)
print(cap.to_string())
print("\ntrailing capture by MFE bucket (TRAIL_SL legs only):")
tr=L[(L.exit_reason=='TRAIL_SL')&(L.mfe_R>0.05)].copy()
tr["bucket"]=pd.cut(tr.mfe_R,[0,1,1.5,2,3,5,100])
print(tr.groupby("bucket").apply(lambda g: pd.Series({"legs":len(g),
    "med_MFE_R":g.mfe_R.median(),"med_realised_R":g.realised_R.median(),
    "capture%":100*(g.realised_R/g.mfe_R).median()}), include_groups=False).round(2).to_string())

hdr("18. IS THE CONFLUENCE SCORE PREDICTIVE?")
print(G.groupby("strategy").confluence.describe()[["count","min","50%","max","std"]].round(2).to_string())
v=G[G.confluence.notna()]
v=v[v.groupby("strategy").confluence.transform("std")>0]
if len(v):
    v["bucket"]=pd.qcut(v.confluence,4,duplicates="drop")
    print("\n(only strategies whose score actually varies)")
    print(v.groupby(["strategy","bucket"]).apply(lambda g: pd.Series({"n":len(g),
        "win%":100*(g.true_R>0).mean(),"exp_R":g.true_R.mean()}), include_groups=False).round(3).to_string())
else:
    print("\nNo strategy emits a varying confluence score -> the score cannot be predictive of anything.")

hdr("19. DIRECTION AND MONTH STABILITY (is any of this a single lucky window?)")
print(G.groupby(["strategy","direction"]).apply(lambda g: pd.Series({"n":len(g),
    "win%":100*(g.true_R>0).mean(),"exp_R":g.true_R.mean()}), include_groups=False).round(3).to_string())
print("\nmonthly pooled expectancy:")
print(G.groupby("month").apply(lambda g: pd.Series({"n":len(g),"exp_R":g.true_R.mean(),
    "pnl$":g.pnl.sum()}), include_groups=False).round(3).to_string())


hdr("16b. FRICTION PER STRATEGY (all six, the two cut off above included)")
rows=[]
for s,g in G.groupby("strategy"):
    fs,fl=frac(g,"spread"),frac(g,"slip")
    rows.append({"strategy":s,"n":len(g),"spread_R":round(fs,3),"slip_R":round(fl,3),
                 "total_friction_R":round(fs+fl,3)})
print(pd.DataFrame(rows).sort_values("total_friction_R",ascending=False).to_string(index=False))

CAP=0.60  # measured trailing capture of MFE, stable across every MFE bucket (§17)
hdr("20. FIXED TP vs TRAILING vs HYBRID — measured capture of 60%% applied to the runner")
def policy(g, mode, k=None, w=None, act=1.0, cap=CAP, friction=0.0):
    mfe,mae,n = g.mfe_R.values, g.mae_R.values, len(g)
    stopped = (mae>=1.0)
    if mode=="fixed":
        r=np.where(mfe>=k, k, np.where(stopped,-1.0,0.0))
    elif mode=="trail":
        # trail arms at `act`; below that the trade is a stop-out or a scratch
        r=np.where(mfe>=act, np.maximum(cap*mfe, 0.0), np.where(stopped,-1.0,0.0))
    else: # hybrid: w at fixed TP k, (1-w) trailed, runner protected at BE once TP1 fills
        hit=mfe>=k
        runner=np.where(mfe>=max(k,act), cap*mfe, 0.0)
        r=np.where(hit, w*k+(1-w)*runner, np.where(stopped,-1.0,0.0))
    return r.mean()-friction

for s,g in list(G.groupby("strategy"))+[("ALL",G)]:
    f=(frac(g,"spread")+frac(g,"slip")) if s!="ALL" else (frac(G,"spread")+frac(G,"slip"))
    best_fixed=max(((k,policy(g,"fixed",k=k,friction=f)) for k in [0.5,0.75,1,1.25,1.5,2,2.5,3,4,5]),key=lambda x:x[1])
    best_trail=max(((a,policy(g,"trail",act=a,friction=f)) for a in [0.5,0.75,1.0,1.25,1.5,2.0]),key=lambda x:x[1])
    best_hyb=max(((k,w,a,policy(g,"hybrid",k=k,w=w,act=a,friction=f))
                  for k in [0.5,0.75,1.0,1.25,1.5,2.0] for w in [0.4,0.5,0.6,0.7,0.8] for a in [0.5,0.75,1.0,1.5]),
                 key=lambda x:x[3])
    print(f"\n{s:16} n={len(g):4d}  friction {f:.3f}R   as-traded {g.true_R.mean():+.3f}R (3TP 1.5/3/5 50/30/20 BE@1.5)")
    print(f"   best FIXED  TP={best_fixed[0]}R                      -> net {best_fixed[1]:+.3f}R")
    print(f"   best TRAIL  arm@{best_trail[0]}R, 60% capture         -> net {best_trail[1]:+.3f}R")
    print(f"   best HYBRID TP1={best_hyb[0]}R w={best_hyb[1]:.0%} trail arm@{best_hyb[2]}R -> net {best_hyb[3]:+.3f}R")

hdr("21. HYBRID GRID, POOLED EX-VWAP (the five low-frequency strategies together)")
S=G[G.strategy!="VWAP_v1"]
f=frac(S,"spread")+frac(S,"slip")
rows=[]
for k in [0.5,0.75,1.0,1.25,1.5,2.0]:
    for w in [0.4,0.5,0.6,0.7,0.8,1.0]:
        rows.append({"TP1_R":k,"w_TP1":w,"net_R":round(policy(S,"hybrid",k=k,w=w,act=1.0,friction=f),4)})
t=pd.DataFrame(rows).pivot(index="TP1_R",columns="w_TP1",values="net_R")
print(f"n={len(S)}  friction={f:.3f}R   runner trailed from 1.0R at 60% capture")
print(t.to_string())
print(f"\nsame five strategies as traded today: {S.true_R.mean():+.3f}R   "
      f"cash {S.pnl.sum():,.0f} over {len(S)} trades")

hdr("22. WHAT A STOP-DISTANCE FLOOR WOULD DO — reject trades whose stop is < N x round-trip cost")
for N in [0,4,6,8,10,12,15,20]:
    keep=[]
    for sym,gs in G.groupby("symbol"):
        c=costs.get(sym)
        if not c: continue
        rt=(c["spread_pips"]+2*c["slippage_pips"])*pips[sym]
        keep.append(gs[gs.risk_price>=N*rt])
    K=pd.concat(keep) if keep else G.iloc[:0]
    if not len(K): continue
    print(f"  N={N:>2}x  kept {len(K):>5}/{len(G)} ({100*len(K)/len(G):>5.1f}%)  "
          f"geo_exp_R {K.true_R.mean():+.4f}  cash ${K.pnl.sum():>9,.0f}  "
          f"cash/trade ${K.pnl.mean():>7.2f}")


hdr("23. VALIDATING THE 60%-CAPTURE TRAIL MODEL AGAINST WHAT ACTUALLY HAPPENED")
print(" for groups that reached X, compare the model's prediction to their real outcome today")
for x in [0.5,0.75,1.0,1.5,2.0,3.0]:
    sub=G[G.mfe_R>=x]
    if len(sub)<20: continue
    print(f"  reached {x:>4}R  n={len(sub):4d}  mean MFE {sub.mfe_R.mean():.2f}R   "
          f"model 0.60xMFE = {0.6*sub.mfe_R.mean():+.3f}R   actually realised {sub.true_R.mean():+.3f}R")

hdr("24. TRAIL SENSITIVITY — capture ratio is the one assumption; here is the whole range")
rows=[]
for cap in [0.40,0.50,0.60,0.70]:
    for act in [0.5,0.75,1.0,1.5]:
        r=[]
        for s,g in G.groupby("strategy"):
            f=fric(g)
            v=np.where(g.mfe_R>=act, cap*g.mfe_R, np.where(g.mae_R>=1.0,-1.0,0.0)).mean()-f
            r.append((s,round(v,3)))
        rows.append({"capture":cap,"arm_R":act, **dict(r),
                     "ALL":round(np.where(G.mfe_R>=act,cap*G.mfe_R,np.where(G.mae_R>=1.0,-1.0,0.0)).mean()-fric(G),3)})
print(pd.DataFrame(rows).to_string(index=False))
print("\n  (as traded today, same groups: " + ", ".join(f"{s} {g.true_R.mean()-fric(g):+.3f}" for s,g in G.groupby('strategy')) + ")")

hdr("25. VWAP SESSION-END: what the 5-hour force-flat is doing")
v=L[L.strategy_id=="VWAP_v1"]
se=v[v.exit_reason=="SESSION_END"]
print(f"  VWAP legs {len(v)}, session-end {len(se)} ({100*len(se)/len(v):.1f}%), max bars_held {v.bars_held.max()} (M5 -> {v.bars_held.max()*5/60:.1f}h)")
print(f"  session-end legs: median MFE {se.mfe_R.median():.2f}R, median realised {se.realised_R.median():+.2f}R, "
      f"{100*(se.realised_R>0).mean():.0f}% closed positive, total ${se.pnl.sum():,.0f}")
print("\n  of the session-end legs, how far had they got when the clock ran out:")
print(pd.cut(se.mfe_R,[0,0.25,0.5,0.75,1.0,1.5,2.0,100]).value_counts().sort_index().to_string())
print("\n  bars held distribution for VWAP by exit reason:")
print(v.groupby("exit_reason").bars_held.describe()[["count","50%","max"]].round(0).to_string())

hdr("26. THE FULL PROPOSAL, SCORED — every configuration side by side (net of friction)")
def sim(g,cfg):
    mfe,mae=g.mfe_R.values,g.mae_R.values; st=mae>=1.0
    if cfg["kind"]=="as_traded": return g.true_R.mean()-fric(g)
    if cfg["kind"]=="fixed":
        return (np.where(mfe>=cfg["k"],cfg["k"],np.where(st,-1.0,0.0))).mean()-fric(g)
    if cfg["kind"]=="trail":
        return (np.where(mfe>=cfg["act"],cfg["cap"]*mfe,np.where(st,-1.0,0.0))).mean()-fric(g)
    if cfg["kind"]=="hybrid":
        hit=mfe>=cfg["k"]
        run=np.where(mfe>=max(cfg["k"],cfg["act"]),cfg["cap"]*mfe,0.0)
        return (np.where(hit,cfg["w"]*cfg["k"]+(1-cfg["w"])*run,np.where(st,-1.0,0.0))).mean()-fric(g)
cfgs=[("as traded (3TP 1.5/3/5, 50/30/20, BE@1.5R)",{"kind":"as_traded"}),
      ("single TP 1.5R",{"kind":"fixed","k":1.5}),
      ("single TP 1.0R",{"kind":"fixed","k":1.0}),
      ("single TP 0.75R",{"kind":"fixed","k":0.75}),
      ("pure trail, arm 1.0R, cap 0.50",{"kind":"trail","act":1.0,"cap":0.5}),
      ("pure trail, arm 0.75R, cap 0.50",{"kind":"trail","act":0.75,"cap":0.5}),
      ("pure trail, arm 0.75R, cap 0.60",{"kind":"trail","act":0.75,"cap":0.6}),
      ("hybrid TP1 0.75R @40%, trail 60% rest",{"kind":"hybrid","k":0.75,"w":0.4,"act":0.75,"cap":0.6}),
      ("hybrid TP1 1.0R @50%, trail 50% rest",{"kind":"hybrid","k":1.0,"w":0.5,"act":1.0,"cap":0.5})]
out=[]
for name,c in cfgs:
    row={"configuration":name}
    for s,g in G.groupby("strategy"): row[s.replace("_v1","")]=round(sim(g,c),3)
    row["POOLED"]=round(sim(G,c),3)
    out.append(row)
print(pd.DataFrame(out).to_string(index=False))

hdr("27. TESTING PHASE-14 PART B3 ('MFE is ~0R; entries are wrong immediately') AT CORPUS SCALE")
print("B3 measured 5 APA trades in one window. Here is every group in the corpus.\n")
print(G.groupby("strategy").apply(lambda g: pd.Series({
  "n":len(g),"median_MFE_R":g.mfe_R.median(),"mean_MFE_R":g.mfe_R.mean(),
  "%MFE<0.1R":100*(g.mfe_R<0.1).mean(),"%reach_0.5R":100*(g.mfe_R>=0.5).mean(),
  "%reach_1R":100*(g.mfe_R>=1).mean(),"%reach_2R":100*(g.mfe_R>=2).mean()}),
  include_groups=False).round(2).to_string())
sl=L[L.exit_reason=="SL"]
print("\nStopped-out legs only - the 'stopped on bar 1 with no favourable tick' signature:")
print(sl.groupby("strategy_id").apply(lambda g: pd.Series({
  "SL_legs":len(g),"median_bars":g.bars_held.median(),
  "%stopped<=3bars":100*(g.bars_held<=3).mean(),
  "%stopped<=3bars_AND_MFE<0.1R":100*((g.bars_held<=3)&(g.mfe_R<0.1)).mean(),
  "median_MFE_R":g.mfe_R.median()}), include_groups=False).round(2).to_string())
