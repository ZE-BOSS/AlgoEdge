"""Scalper backtests on stored ticks (run on the server, see backend/scalping).

    TICK_STORE=/opt/algoedge/ticks python scripts/scalp_bt.py <broker> <symbol> <S1|S2|...|S7|hazard> [--days N]

Every setting of the strategy's grid runs on the same ticks. Settings are
ranked on the first 70% of the days ("tune") and each is also scored on the
last 30% it was not chosen on ("unseen"). Prints one RES| line per setting.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.scalping import engine as E  # noqa: E402
from backend.scalping import strategies as S  # noqa: E402
from backend.scalping import ticks as T  # noqa: E402

GRIDS = {
    "S1": dict(tf=[60, 300], stop_atr=[1.0, 1.5, 2.0], entries=[("A", "B", "C"), ("A", "B"), ("C",)], tp_r=[1.5, 2.0]),
    "S2": dict(sl_tp=[(1.2, 1.6), (1.0, 2.0), (1.5, 1.5)], adx_min=[18.0, 22.0, 26.0], clv=[0.6, 0.7]),
    "S3": dict(tf=[30, 60, 180], key=[1.0, 2.0, 3.0], exit=["flip", "target"], use_trend=[True, False]),
    "S4": dict(impulse_atr=[4.0, 6.0, 8.0], impulse_bars=[5, 10, 20]),
    "S5": dict(mode=["fade", "continuation"], vol_mult=[1.5, 2.0, 3.0], tp_r=[1.5, 2.0, 3.0]),
    "S6": dict(tf=[180, 60], exit=["ema", "atr"], use_chop=[False, True]),
    "S7": dict(wait_ticks=[0, 100, 300, 600, 1000], hold_ticks=[50, 200, 500]),
}
RULES = {
    "S1": E.Rules(daily_loss_r=6.0, pause_after_losses=4, pause_s=1800, min_gap_s=300),
    "S2": E.Rules(daily_loss_r=10.0),
    "S3": E.Rules(), "S4": E.Rules(daily_loss_r=4.0), "S5": E.Rules(daily_loss_r=4.0),
    "S6": E.Rules(daily_loss_r=3.0), "S7": E.Rules(),
}


def expand(grid: dict) -> list[dict]:
    keys = list(grid)
    out = []
    for vals in itertools.product(*(grid[k] for k in keys)):
        p = dict(zip(keys, vals))
        if "sl_tp" in p:
            p["sl_atr"], p["tp_atr"] = p.pop("sl_tp")
        out.append(p)
    return out


def brief(s: dict) -> dict:
    keep = ("trades", "per_day", "win_rate", "avg_r", "net_r", "pf", "max_dd_r", "cost_share", "median_hold_min",
            "max_losing_streak", "gross_r")
    return {k: s.get(k) for k in keep}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("broker")
    ap.add_argument("symbol")
    ap.add_argument("strategy")
    ap.add_argument("--days", type=int, default=0, help="only the last N stored days (0 = all)")
    ap.add_argument("--kind", default=None, help="S7: boom or crash (default from the symbol name)")
    ap.add_argument("--latency", type=int, default=250)
    a = ap.parse_args()
    days = T.stored_days(a.broker, a.symbol)
    if a.days:
        days = days[-a.days:]
    if not days:
        raise SystemExit("no ticks stored")
    t0 = time.time()
    tk = T.load(a.broker, a.symbol, days[0], days[-1])
    cut_day = days[int(len(days) * 0.7)]
    cut_ms = int(T.datetime(cut_day.year, cut_day.month, cut_day.day, tzinfo=T.UTC).timestamp() * 1000)
    head = {"broker": a.broker, "symbol": a.symbol, "strategy": a.strategy, "days": len(days),
            "from": str(days[0]), "to": str(days[-1]), "unseen_from": str(cut_day), "ticks": len(tk),
            "median_spread": round(float(T.np.median(tk.ask - tk.bid)), 5) if len(tk) else None,
            "gaps_over_1h": len(T.gaps(tk, 3600))}
    print("HEAD|" + json.dumps(head), flush=True)
    inst = S.Instrument(a.symbol)
    costs = E.Costs(latency_ms=a.latency)
    if a.strategy == "hazard":
        for kind in ("boom", "crash"):
            if kind in a.symbol.lower():
                print("HAZ|" + json.dumps({"kind": kind, **S.hazard(tk, kind)}), flush=True)
        return
    rows = []
    if a.strategy == "S7":
        kind = a.kind or ("boom" if "boom" in a.symbol.lower() else "crash")
        for p in expand(GRIDS["S7"]):
            fills = E.run(S.s7_spike_count(tk, {**p, "kind": kind}, inst), tk, costs, RULES["S7"])
            rows.append((p, fills))
    else:
        bc = S.BarCache(tk)
        fn = S.STRATEGIES[a.strategy]
        for p in expand(GRIDS[a.strategy]):
            fills = E.run(fn(bc, p, inst), tk, costs, RULES[a.strategy])
            rows.append((p, fills))
    for p, fills in rows:
        sp = E.split_stats(fills, cut_ms)
        print("RES|" + json.dumps({"p": p, "tune": brief(sp["tune"]), "unseen": brief(sp["unseen"]),
                                   "exits": E.stats(fills).get("exits")}, default=str), flush=True)
    print("DONE|" + json.dumps({"seconds": round(time.time() - t0, 1), "settings": len(rows)}), flush=True)


if __name__ == "__main__":
    main()
