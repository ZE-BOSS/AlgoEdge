#!/usr/bin/env python
"""
scripts/run_spike_per_asset.py

The spike-resumption test, PER ASSET, with every assumption stated.

The earlier report pooled eight instruments into one number, which hides exactly
what you need to see: which symbol paid and which did not. This one reports each
symbol on its own and states, for every run, the four things a return is
meaningless without:

    risk per trade      what fraction of the account each loss costs
    compounding         is risk a share of the CURRENT balance or the starting one
    pyramiding          can a second position open while the first is still on
    reward-to-risk      what the target actually was, and what was realised

    py -3.12 scripts/run_spike_per_asset.py --since 2026-01-01
    py -3.12 scripts/run_spike_per_asset.py --since 2026-01-01 --risk 1 2 --no-compound
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_published_strategies import FEEDS  # noqa: E402
from scripts.run_spike_resumption import MARKETS, load_m15, run_variant  # noqa: E402

BASE = dict(k_atr=3.0, trend_mode="ema", trend_look=50, confirm_bars=1,
            stop_atr=2.0, target="spike", target_rr=3.0, max_hold=5)
COMBO = {**BASE, "trend_mode": "below", "stop_atr": 1.0}


def realised_rr(trades: list[dict]) -> tuple[float, float, float]:
    """(average win in R, average loss in R, realised reward:risk).

    The TARGET reward:risk is not the realised one: a trade that leaves on the
    max-hold clock books whatever the market gave it, which on this rule is most
    of them. Quoting the target alone would describe a trade that rarely happens.
    """
    r = np.array([x["r"] for x in trades])
    w, lo = r[r > 0], r[r < 0]
    aw = float(w.mean()) if len(w) else 0.0
    al = float(lo.mean()) if len(lo) else 0.0
    return aw, al, (aw / abs(al) if al else 0.0)


def per_asset(data: dict, params: dict, risk: float, compounding: bool,
              slip: float = 1.0) -> list[dict]:
    rows = []
    for sym, (b, side) in data.items():
        tr = run_variant(b, sym, side, slip_points=slip, **params)
        if not tr:
            rows.append({"symbol": sym, "n": 0})
            continue
        res = eng.run_account(sorted(tr, key=lambda x: x["t"]), 10_000.0, risk,
                              compounding=compounding)
        rep = eng.report(res, sym)
        if not rep.get("trades"):
            rows.append({"symbol": sym, "n": 0, "skipped": res.get("skipped")})
            continue
        r = np.array([x["r"] for x in tr])
        aw, al, rr = realised_rr(tr)
        exits: dict[str, int] = {}
        for x in tr:
            exits[x["exit"]] = exits.get(x["exit"], 0) + 1
        rows.append({
            "symbol": sym, "n": len(tr), "taken": rep["trades"],
            "final": rep["final"], "return_pct": rep["return_pct"],
            "max_dd_usd": rep["max_dd_usd"], "max_dd_pct": rep["max_dd_pct"],
            "win_rate": rep["win_rate"], "profit_factor": rep["profit_factor"],
            "expectancy_r": float(r.mean()), "expectancy_usd": rep["expectancy_usd"],
            "avg_win_r": aw, "avg_loss_r": al, "realised_rr": rr,
            "avg_win_usd": rep["avg_win"], "avg_loss_usd": rep["avg_loss"],
            "best": rep["best_trade"], "worst": rep["worst_trade"],
            "win_streak": rep["longest_win_streak"], "loss_streak": rep["longest_loss_streak"],
            "t_stat": float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))) if len(r) > 2 and r.std() else 0.0,
            "exits": exits, "skipped": res.get("skipped"),
        })
    return rows


HDR = (f"  {'asset':18s} {'N':>4s} {'$ end':>10s} {'return':>9s} {'maxDD$':>9s} {'maxDD%':>7s} "
       f"{'win%':>6s} {'PF':>6s} {'expR':>7s} {'exp$':>8s} {'R:R':>6s} {'t':>6s}")


def show(rows: list[dict]) -> None:
    print(HDR)
    print("  " + "-" * (len(HDR) - 2))
    for r in sorted(rows, key=lambda x: -x.get("return_pct", -999)):
        if not r.get("n"):
            note = f"(min lot skipped {sum((r.get('skipped') or {}).values())})" \
                if r.get("skipped") else "(no trades)"
            print(f"  {r['symbol']:18s}    -  {note}")
            continue
        print(f"  {r['symbol']:18s} {r['n']:>4d} {r['final']:>10,.0f} {r['return_pct']:>+8.2f}% "
              f"{r['max_dd_usd']:>9,.0f} {r['max_dd_pct']:>6.2f}% {r['win_rate']:>5.1f}% "
              f"{r['profit_factor']:>6.2f} {r['expectancy_r']:>+7.3f} {r['expectancy_usd']:>+8.2f} "
              f"{r['realised_rr']:>6.2f} {r['t_stat']:>+6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--risk", nargs="*", type=float, default=[0.5, 1.0, 2.0])
    ap.add_argument("--no-compound", action="store_true")
    ap.add_argument("--params", default="both", choices=["combo", "base", "both"])
    args = ap.parse_args()

    bars_dir, specs = FEEDS["deriv"]
    eng.SPECS = json.loads(specs.read_text())
    t0 = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc).timestamp()) \
        if args.until else None

    data = {}
    for sym, side in MARKETS:
        b = load_m15(bars_dir, sym, t0)
        if b is None:
            continue
        if t1:
            m = b["time"] < t1
            b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        data[sym] = (b, side)

    span = f"{args.since} -> {args.until or 'today'}"
    sets = {"combo": COMBO, "base": BASE} if args.params == "both" \
        else {args.params: (COMBO if args.params == "combo" else BASE)}

    out = {}
    for label, params in sets.items():
        print(f"\n{'=' * 108}")
        print(f"{label.upper()}  {span}")
        print(f"  entry: {params['k_atr']:g}xATR spike, trend={params['trend_mode']}"
              f"({params['trend_look']}), {params['confirm_bars']} confirming bar(s)")
        tgt = ("back past the spike bar" if params["target"] == "spike"
               else f"{params['target_rr']:g}R")
        print(f"  exit : stop {params['stop_atr']:g}xATR, target {tgt}, "
              f"max hold {params['max_hold']} bars")
        print(f"  {'=' * 106}")
        for risk in args.risk:
            for comp in ([True] if not args.no_compound else [True, False]):
                print(f"\n  RISK {risk:g}% per trade  |  compounding "
                      f"{'ON (risk is % of current balance)' if comp else 'OFF (% of the starting $10,000)'}"
                      f"  |  pyramiding OFF (one position per symbol at a time)")
                rows = per_asset(data, params, risk, comp)
                show(rows)
                out[f"{label}|risk{risk}|comp{comp}"] = rows

    OUT = ROOT / "data" / "spike_resumption"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"per_asset_{args.since}.json").write_text(json.dumps(out, indent=1, default=float))
    print(f"\n-> {OUT / f'per_asset_{args.since}.json'}")


if __name__ == "__main__":
    main()
