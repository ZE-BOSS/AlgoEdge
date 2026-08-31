"""Drive full_backtest across both brokers, all strategies, all assets, RR 2-5."""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
import numpy as np
from full_backtest import (gen_signals, simulate, metrics, RRS, BALANCE)

HERE = Path(__file__).parent
STRATS = ["APA_v1", "VWAP_v1", "BiasIFVG_v1", "CRT_v1",
          "NYOpenRetest_v1", "HTFFVGFlip_v1", "DriftJumpAlpha_v1"]

BROKERS = {
    "Deriv": dict(cache=HERE / "cache_live_deriv", symbols=[
        "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD",
        "US Tech 100", "US SP 500", "Germany 40", "Netherlands 25",
        "Hong Kong 50",
        "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "AUDUSD",
        "XAUUSD", "XAGUSD", "XPTUSD",
        "Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
        "Jump 100 Index", "Volatility 75 Index"]),
    "FundedNext": dict(cache=HERE / "cache_live_fn", symbols=[
        "BTCUSD", "ETHUSD", "XRPUSD",
        "NDX100", "SPX500", "GER30", "NTH25", "HK50",
        "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "AUDUSD",
        "XAUUSD", "XAGUSD", "XPTUSD",
        "USOUSD", "UKOUSD", "US30", "UK100", "JP225"]),
}

SYNTH = ("Crash", "Jump", "Volatility")


async def main():
    out = {}
    for bname, bcfg in BROKERS.items():
        cache = bcfg["cache"]
        for sym in bcfg["symbols"]:
            for sid in STRATS:
                # DriftJumpAlpha is hard-gated to Crash instruments
                if sid == "DriftJumpAlpha_v1" and not sym.startswith("Crash"):
                    continue
                if sid != "DriftJumpAlpha_v1" and sym.startswith("Crash"):
                    pass  # other strategies may still run on Crash
                try:
                    pdf, sigs, gates, meta = await gen_signals(sid, sym, cache)
                except Exception as e:
                    print(f"{bname:11s} {sym:20s} {sid:18s} ERROR "
                          f"{type(e).__name__}: {str(e)[:50]}", flush=True)
                    continue
                if pdf is None:
                    continue
                if not sigs:
                    print(f"{bname:11s} {sym:20s} {sid:18s} 0 signals",
                          flush=True)
                    continue
                spread = meta.get("spread_px", 0.0)
                row = {"broker": bname, "symbol": sym, "strategy": sid,
                       "signals": len(sigs), "spread_px": spread, "rr": {}}
                for rr in RRS:
                    tr, rej = simulate(pdf, sigs, rr, spread)
                    m = metrics(tr)
                    if m:
                        m["rejected"] = rej
                        m["accepted"] = m["trades"]
                        row["rr"][str(rr)] = m
                out[f"{bname}|{sym}|{sid}"] = row
                best = max(row["rr"].items(),
                           key=lambda kv: kv[1]["pnl"]) if row["rr"] else None
                if best:
                    k, m = best
                    print(f"{bname:11s} {sym:20s} {sid:18s} "
                          f"sig={len(sigs):4d} best 1:{k} "
                          f"P&L=${m['pnl']:+9.2f} DD={m['maxdd_pct']:5.1f}% "
                          f"n={m['trades']:4d} WR={m['wr']:5.1%} "
                          f"PF={m['pf']:.2f}", flush=True)
    (HERE / "full_backtest_results.json").write_text(json.dumps(out, default=str))
    print(f"\nsaved {len(out)} cells", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
