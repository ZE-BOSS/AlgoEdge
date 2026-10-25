"""
Entry-level replication check: does the app's port take the SAME trades as the
template's published backtest (same day, same direction, entry within one bar)?

    py -3.12 research/roboquant/compare_entries.py [symbol]

Writes research/roboquant/results/entries_<symbol>.json.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np, pandas as pd
import compare_trades as ct
from backend.core.config_schema import UserConfigV2
from backend.rl import base_signals as bs

PORTS = [  # template index, strategy id, feed tf, fill seconds after the decision bar opens
    (19, "RQ_FlashTriggerORB_v1", "M15"), (24, "RQ_ScarfaceORB_v1", "M1"), (23, "RQ_IVB_v1", "M5"),
    (6, "RQ_GigaORB_v1", "M15"), (2, "RQ_SigmaBreakout_v1", "M30"), (9, "RQ_DailyVWAP_v1", "M1"),
    (16, "RQ_FridayRevisit_v1", "M5"), (4, "RQ_M30ORB_v1", "M5"), (22, "RQ_SRSweep_v1", "M5"),
    (21, "RQ_CrazyORB_v1", "M15"), (7, "RQ_NYORB_v1", "M15"), (3, "RQ_NQPrecision_v1", "M1"),
    (1, "RQ_TMeanReversion_v1", "H1"), (14, "RQ_CharlesMonger_v1", "H1"),
]
TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}
SPX = {9, 14}                                          # MES / ES templates


def frame(sym, tf):
    f = ct.bars(sym, tf)
    idx = f.set_index(pd.to_datetime(f["time"], unit="s"))
    if "tick_volume" not in idx.columns and "volume" in idx.columns:
        idx["tick_volume"] = idx["volume"]
    return idx


def signals(sym, sid, tf, start):
    p = ct.HERE / "cache" / f"sig2_{sid}_{sym.replace(' ', '_')}_{tf}.pkl"
    if p.exists():
        return pd.read_pickle(p)
    fr = frame(sym, tf)
    fr = fr[fr.index >= pd.Timestamp(start) - pd.Timedelta(days=40)]
    sigs = bs.generate(sym, tf, bs.index_frames({tf: fr, "D1": frame(sym, "D1")}), sid, UserConfigV2(), start_index=60)
    t = fr["time"].to_numpy(np.int64)
    s = pd.DataFrame([{"fill_t": int(t[x["bar_index"]]) + TF_SEC[tf], "long": x["direction"] == "BUY",
                       "entry": x["entry_price"], "sl": x["stop_loss"], "tp": x["take_profit"]} for x in sigs])
    s.to_pickle(p)
    return s


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "NDX100"
    only = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    out = {}
    for k, sid, tf in PORTS:
        if only and sid not in only:
            continue
        sym = ("SPX500" if base == "NDX100" else "US SP 500") if k in SPX else base
        th, title = ct.their_trades(k)
        th["fill_t"] = (th["entry_ms"] // 1000).astype("int64")
        s = signals(sym, sid, tf, str(th["et"].min().date()))
        lo, hi = max(th.fill_t.min(), s.fill_t.min()), min(th.fill_t.max(), s.fill_t.max())
        a = th[(th.fill_t >= lo) & (th.fill_t <= hi)].sort_values("fill_t")
        b = s[(s.fill_t >= lo) & (s.fill_t <= hi)].sort_values("fill_t")
        tol = max(TF_SEC[tf], 60)
        bt, bl = b.fill_t.to_numpy(), b.long.to_numpy()
        hit = 0
        for r in a.itertuples():
            j = np.searchsorted(bt, r.fill_t - tol)
            while j < len(bt) and bt[j] <= r.fill_t + tol:
                if bl[j] == r.long:
                    hit += 1
                    break
                j += 1
        res = {"template": title, "symbol": sym, "window": [str(pd.Timestamp(lo, unit="s")), str(pd.Timestamp(hi, unit="s"))],
               "roboquant_trades": int(len(a)), "our_signals": int(len(b)),
               "roboquant_trades_we_also_take": int(hit),
               "share": round(hit / max(1, len(a)), 3)}
        out[sid] = res
        print(json.dumps({sid: res}), flush=True)
    (ct.HERE / "results").mkdir(exist_ok=True)
    (ct.HERE / "results" / f"entries_{base.replace(' ', '_')}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
