"""Smoke-run every roboquant port through base_signals.generate on recent bars."""
import sys, time
sys.path.insert(0, "research/roboquant"); sys.path.insert(0, ".")
import pandas as pd
import compare_trades as ct
from backend.core.config_schema import UserConfigV2
from backend.rl import base_signals as bs

RUNS = [("RQ_FlashTriggerORB_v1", "M15"), ("RQ_ScarfaceORB_v1", "M1"), ("RQ_IVB_v1", "M5"),
        ("RQ_GigaORB_v1", "M15"), ("RQ_SigmaBreakout_v1", "M30"), ("RQ_DailyVWAP_v1", "M1"),
        ("RQ_FridayRevisit_v1", "M5"), ("RQ_M30ORB_v1", "M5"), ("RQ_SRSweep_v1", "M5")]
only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
start = pd.Timestamp(sys.argv[2] if len(sys.argv) > 2 else "2026-06-01")
sym = sys.argv[3] if len(sys.argv) > 3 else "NDX100"

def frame(tf):
    f = ct.bars(sym, tf)
    idx = f.set_index(pd.to_datetime(f["time"], unit="s"))
    if "tick_volume" not in idx.columns and "volume" in idx.columns:
        idx["tick_volume"] = idx["volume"]
    return idx

for sid, tf in RUNS:
    if only and sid not in only:
        continue
    fr = frame(tf)
    fr = fr[fr.index >= start - pd.Timedelta(days=20)]
    frames = {tf: fr, "D1": frame("D1")}
    t0 = time.time()
    sigs = bs.generate(sym, tf, bs.index_frames(frames), sid, UserConfigV2(), start_index=60)
    dt = time.time() - t0
    print(f"{sid:24} {tf:4} bars {len(fr):7}  signals {len(sigs):5}  {dt:6.1f}s  {dt / max(1, len(fr)) * 1000:.2f} ms/bar", flush=True)
    for s in sigs[:3]:
        print("    ", pd.Timestamp(int(fr['time'].iloc[s['bar_index']]), unit='s'), s["direction"], round(s["entry_price"], 2),
              "sl", round(s["stop_loss"], 2), "tp", round(s["take_profit"], 2))
