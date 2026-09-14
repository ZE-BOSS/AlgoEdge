#!/usr/bin/env python
"""
scripts/verify_confluence_picks.py

Re-run each study pick through the APP's own backtester (scripts/run_app_backtest,
the same code path as the Backtester page) and record what it books, with the
exact parameters to type into the Backtester to reproduce it.

    python scripts/verify_confluence_picks.py --summary data/confluence_study/summary.json \
        --out data/confluence_study/app_verification.json [--window 2026-01-01 2026-09-12] [--only-held]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

SYNTH = ("SpikeFade_v1", "RangeRevert_v1", "RangeBreakout_v1", "TrendDrift_v1")
TIME = {"london": "LONDON", "newyork": "NEWYORK", "rth": "RTH", "ny_open": "NY_OPEN"}


def strategy_params(pick: dict) -> dict:
    """Research configuration -> the strategy params block the engine reads."""
    strat, setting = pick["strategy"], dict(pick["setting"])
    gates = [g for g in pick["confluences"] if g != "none"]
    side = pick["side"]
    # every study result closes a position 288 M5 bars after entry (synth_research.MAX_HOLD)
    p: dict = {"max_hold_bars": 288}
    if strat in SYNTH:
        if strat == "TrendDrift_v1":
            adx = setting.pop("min_adx_to_trade", 20)
            p["require_adx"] = adx > 0
            if adx > 0:
                p["min_adx_to_trade"] = int(adx)
        p.update(setting)
        p["stop_atr_multiple"] = pick["stop_atr"]
        p["tp1_rr"] = pick["target_rr"]
        # the study's daily cap: 4 entries per day at 1% risk
        p["max_trades_per_day"] = 4
        p["max_daily_risk_pct"] = 4.0
        p["side"] = side
        for g in gates:
            if g == "trend_with":
                p["require_trend_with"] = True
            elif g == "htf_trend":
                p["require_htf_trend"] = True
            elif g == "adx_trend":
                p["adx_filter"] = "TREND"
            elif g == "adx_range":
                p["adx_filter"] = "RANGE"
            elif g == "vol_high":
                p["require_vol_high"] = True
            elif g == "candle_confirm":
                p["require_candle_confirm"] = True
            elif g == "strong_close":
                p["require_strong_close"] = True
            elif g in TIME:
                p["time_filter"] = TIME[g]
            else:
                raise ValueError(f"{strat}: no engine parameter for {g}")
        return p

    p.update(setting)
    p["target_rr"] = pick["target_rr"]
    p["side"] = side
    if strat == "BiasIFVG_v1":
        p["max_trades_per_day"] = 4
        p["day_stop_enabled"] = False
    for g in gates:
        if g == "first_tap":
            p["require_first_tap"] = True
        elif g == "inv_disp_025":
            p["min_inversion_disp_atr"] = max(p.get("min_inversion_disp_atr", 0.0), 0.25)
        elif g == "inv_disp_050":
            p["min_inversion_disp_atr"] = 0.5
        elif g == "stop_ok":
            p["require_stop_ok"] = True
        elif g == "htf_trend":
            p["require_htf_trend"] = True
        elif g == "adx_trend":
            p["require_adx_trend"] = True
        elif g == "vol_high":
            p["require_vol_high"] = True
        elif g == "h4_aligned":
            p["require_h4_aligned"] = True
        elif g == "levels_conf1":
            p["min_confluent_levels"] = max(p.get("min_confluent_levels", 0), 1)
        elif g == "levels_conf2":
            p["min_confluent_levels"] = 2
        elif g in TIME:
            p["time_filter"] = TIME[g]
        else:
            raise ValueError(f"{strat}: no engine parameter for {g}")
    return p


async def verify(pick: dict, start: str, end: str) -> dict:
    import run_app_backtest as rab

    params = strategy_params(pick)
    args = Namespace(
        strategy=pick["strategy"], symbol=pick["market"], start=start, end=end, balance=10_000.0, risk=1.0,
        tp1_rr=float(pick["target_rr"]), min_rr=min(float(pick["target_rr"]), 1.0), daily_dd=100.0, weekly_dd=100.0,
        max_positions=10, max_per_symbol=1, max_daily_trades=100, sizing_basis="STATIC", pyramiding=False,
        fill_model="CONSERVATIVE", strategy_params=json.dumps(params), dump=None,
        spread_pips=None, commission=None, slippage=None, max_margin_pct=None,
    )
    res = await rab.run(args)
    return {"params": params, "app": {k: res.get(k) for k in (
        "signals", "trades", "net_pnl", "win_rate", "profit_factor", "expectancy_r", "max_dd_pct", "rejections",
        "exit_reasons")}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=str(ROOT / "data" / "confluence_study" / "summary.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "confluence_study" / "app_verification.json"))
    ap.add_argument("--window", nargs=2, default=["2026-01-01", "2026-09-12"])
    ap.add_argument("--only-held", action="store_true", help="only picks that were positive in 2026 research")
    ap.add_argument("--markets", nargs="*", default=None)
    ap.add_argument("--strategies", nargs="*", default=None)
    ap.add_argument("--pooled", action="store_true",
                    help="run each pooled pick (one setting for a whole group) on every market of its group")
    args = ap.parse_args()
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    out_path = Path(args.out)
    done = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    seen = {(d["market"], d["strategy"], args.window[0]) for d in done}
    if args.pooled:
        # one row per (pooled pick, market in its group), carrying that market's own research stats
        picks = [dict(p, market=m, pooled_group=p["group"],
                      research={k: pm.get(k) for k in ("validate_2025", "unseen_2026", "quarters_positive")})
                 for p in summary["pooled"] if "verdict" not in p for m, pm in p["markets"].items()]
    else:
        picks = [dict(p, research={k: p[k] for k in ("select_2023_24", "validate_2025", "unseen_2026",
                                                     "quarters_positive")})
                 for p in summary["per_market"] if "verdict" not in p]
    for pick in picks:
        if args.only_held and not args.pooled and not pick.get("held_in_2026"):
            continue
        if args.markets and pick["market"] not in args.markets:
            continue
        if args.strategies and pick["strategy"] not in args.strategies:
            continue
        if (pick["market"], pick["strategy"], args.window[0]) in seen:
            continue
        try:
            v = asyncio.run(verify(pick, *args.window))
        except Exception as e:  # keep going; record the failure
            v = {"error": f"{type(e).__name__}: {e}"}
        row = {"market": pick["market"], "strategy": pick["strategy"], "window": args.window,
               "research": pick["research"], **v}
        if args.pooled:
            row["pooled_group"] = pick["pooled_group"]
        done.append(row)
        out_path.write_text(json.dumps(done, indent=1, default=str), encoding="utf-8")
        app, unseen = v.get("app", {}), pick["research"].get("unseen_2026") or {}
        print(f"{pick['market']} {pick['strategy']}: app trades={app.get('trades')} net=${app.get('net_pnl')} "
              f"PF={app.get('profit_factor')} | research 2026 avg R={unseen.get('avg_r')} "
              f"n={unseen.get('n')} {v.get('error', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
