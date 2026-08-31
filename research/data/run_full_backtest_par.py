"""Parallel full-window backtest.

The date-windowed run is ~8.75x the bars of the bar-capped one, which would take
about 9 hours single-threaded. Nothing here touches MT5 — every worker reads the
local npz cache — so the work parallelises cleanly across processes.

Writes one JSON per cell into results_full/ so a crash or interrupt never loses
completed work, and a re-run skips what is already done.
"""
from __future__ import annotations
import asyncio, json, os, sys, time, traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "results_wf"
OUT.mkdir(exist_ok=True)

STRATS = ["APA_v1", "VWAP_v1", "BiasIFVG_v1", "CRT_v1",
          "NYOpenRetest_v1", "HTFFVGFlip_v1", "DriftJumpAlpha_v1"]

BROKERS = {
    "Deriv": ("cache_live_deriv", [
        "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD",
        "US Tech 100", "US SP 500", "Germany 40", "Netherlands 25",
        "Hong Kong 50",
        "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "AUDUSD",
        "XAUUSD", "XAGUSD", "XPTUSD",
        "Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
        "Jump 100 Index", "Volatility 75 Index"]),
    "FundedNext": ("cache_live_fn", [
        "BTCUSD", "ETHUSD", "XRPUSD",
        "NDX100", "SPX500", "GER30", "NTH25", "HK50",
        "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "AUDUSD",
        "XAUUSD", "XAGUSD", "XPTUSD",
        "USOUSD", "UKOUSD", "US30", "UK100", "JP225"]),
}


def cell_id(broker, sym, sid):
    return f"{broker}__{sym.replace(' ', '_')}__{sid}"


def work(args):
    broker, cache_name, sym, sid = args
    fid = cell_id(broker, sym, sid)
    dest = OUT / f"{fid}.json"
    if dest.exists():
        return fid, "cached", 0.0
    t0 = time.time()
    try:
        # Silence the application logger inside workers BEFORE importing any
        # backend module. Three processes sharing logs/backend.log raced on
        # loguru's 10MB rotation and threw PermissionError on every rename
        # (47 in the first two minutes). 86% of the volume was BOS/ChoCH spam
        # from market_structure, which is pure overhead here — the strategies
        # are being measured, not debugged.
        from loguru import logger as _lg
        _lg.remove()
        import logging as _logging
        _logging.disable(_logging.CRITICAL)

        # imported inside the worker so each process gets its own module state
        from full_backtest import gen_signals, simulate, metrics, split_metrics, RRS
        pdf, sigs, gates, meta = asyncio.run(
            gen_signals(sid, sym, HERE / cache_name))
        if pdf is None:
            dest.write_text(json.dumps({"skip": str(meta)}))
            return fid, "skip", time.time() - t0
        row = {"broker": broker, "symbol": sym, "strategy": sid,
               "signals": len(sigs), "spread_px": meta.get("spread_px", 0.0),
               "bars": len(pdf),
               "from": str(pdf.index[0]), "to": str(pdf.index[-1]),
               "days": int((pdf.index[-1] - pdf.index[0]).days),
               "rr": {}}
        for rr in RRS:
            tr, rej = simulate(pdf, sigs, rr, meta.get("spread_px", 0.0))
            # [18.1] Full window PLUS the in-sample / out-of-sample halves, so
            # the selection rule can be chosen on Jan-Apr and scored on May-Aug
            # without a second 3.7-hour run.
            sm = split_metrics(tr)
            if sm and sm["full"]:
                sm["full"]["rejected"] = rej
                row["rr"][str(rr)] = sm["full"]
                row.setdefault("wf", {})[str(rr)] = {
                    "is": sm["is"], "oos": sm["oos"],
                }
        dest.write_text(json.dumps(row, default=str))
        return fid, f"{len(sigs)} sigs", time.time() - t0
    except Exception as e:
        dest.write_text(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return fid, f"ERROR {type(e).__name__}", time.time() - t0


def main():
    jobs = []
    for broker, (cache, syms) in BROKERS.items():
        for sym in syms:
            for sid in STRATS:
                if sid == "DriftJumpAlpha_v1" and not sym.startswith("Crash"):
                    continue
                jobs.append((broker, cache, sym, sid))
    todo = [j for j in jobs if not (OUT / f"{cell_id(j[0], j[2], j[3])}.json").exists()]
    print(f"{len(jobs)} cells total, {len(todo)} to run, "
          f"{len(jobs)-len(todo)} already done", flush=True)

    done = 0
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(work, j): j for j in todo}
        for f in as_completed(futs):
            fid, status, el = f.result()
            done += 1
            rate = (time.time() - t0) / max(done, 1)
            eta = rate * (len(todo) - done) / 60
            print(f"[{done}/{len(todo)}] {fid:58s} {status:14s} "
                  f"{el:6.1f}s  ETA {eta:5.1f}m", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
