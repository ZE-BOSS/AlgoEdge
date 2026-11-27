"""
scripts/av_verify.py - run every AV_ research preset (and VWAP) through the app's own backtest path
(scripts/run_app_backtest.py) on the broker's symbol, with Databento as the data source, and print
one line per preset next to the research number.

    cd <app> && set -a && . <instance.env> && set +a && \
        PYTHONPATH=. python scripts/av_verify.py --start 2026-01-05 --end 2026-11-24
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NAMES = {
    "gold": ["XAUUSD", "GOLD"], "silver": ["XAGUSD", "SILVER"],
    "nasdaq": ["US Tech 100", "USTEC", "NAS100", "US100"], "sp500": ["US SP 500", "US 500", "SPX500", "US500"],
    "us30": ["Wall Street 30", "US 30", "US30"], "oil": ["Oil/USD", "US Oil", "USOIL", "XTIUSD", "WTI"],
    "jp225": ["Japan 225", "JP225"], "ger40": ["Germany 40", "GER40", "DE40"], "gbpusd": ["GBPUSD"],
}
PRESETS = [  # strategy, market, research (trades, win, PF)
    ("AV_GoldenEngulfFlip_v1", "gold", "66 / 44% / PF 2.28"),
    ("AV_GoldenTwinAbsorb_v1", "gold", "132 / 63% / PF 1.56"),
    ("AV_NasdaqRejectionFlip_v1", "nasdaq", "87 / 49% / PF 2.03"),
    ("AV_SPFreshDoji_v1", "sp500", "66 / 55% / PF 1.64"),
    ("AV_SPTwinRejection_v1", "sp500", "85 / 49% / PF 1.59"),
    ("AV_SilverAbsorbFlip_v1", "silver", "72 / 54% / PF 1.89"),
    ("AV_DowBigTradePin_v1", "us30", "85 / 39% / PF 1.74"),
    ("AV_DowAbsorptionZone_v1", "us30", "92 / 53% / PF 1.52"),
    ("AV_NasdaqPrevDayDoji_v1", "nasdaq", "111 / 61% / PF 1.45 (Deriv)"),
    ("AV_DowDojiRetest_v1", "us30", "107 / 36% / PF 1.51 (Deriv)"),
    ("AV_OilTrendEngulf_v1", "oil", "84 / 55% / PF 1.87 (Deriv)"),
    ("AV_OilRetestDoji_v1", "oil", "104 / 40% / PF 1.62"),
    ("AV_NikkeiTrendEngulf_v1", "jp225", "60 / 63% / PF 1.54 (Deriv)"),
    ("AV_DAXRetestRejection_v1", "ger40", "128 / 37% / PF 1.38 (Deriv)"),
    ("AV_CableLiquiditySweep_v1", "gbpusd", "82 / 52% / PF 1.67"),
    ("VWAP_v1", "gold", "Deriv +27.3R PF 1.6"),
    ("VWAP_v1", "nasdaq", "Deriv +26.4R PF 2.22"),
]


def broker_names() -> dict[str, str]:
    import MetaTrader5 as mt5
    mt5.initialize()
    out = {}
    for m, cands in NAMES.items():
        for c in cands:
            if mt5.symbol_info(c) is not None:
                out[m] = c
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-05")
    ap.add_argument("--end", default="2026-11-24")
    ap.add_argument("--only", default="")
    ap.add_argument("--source", default="databento", help="databento | mt5 (sets MARKETDATA_SOURCE)")
    a = ap.parse_args()
    import os
    env = dict(os.environ, MARKETDATA_SOURCE=a.source, PYTHONPATH=str(ROOT))
    names = broker_names()
    print("# broker symbols:", names, flush=True)
    import asyncio

    import pandas as pd

    from backend.marketdata import source
    for m, sym in names.items():
        try:
            f = asyncio.run(source.bars(sym, "M15", start=pd.Timestamp(a.end) - pd.Timedelta(days=5),
                                        end=pd.Timestamp(a.end)))
            print(f"# {sym}: " + ("MT5 fallback" if f is None else
                  f"{len(f)} bars from {f.attrs.get('source')} {f.attrs.get('future')} basis {f.attrs.get('basis')} "
                  f"flow {f['delta'].notna().mean():.0%}"), flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"# {sym}: {type(e).__name__}: {e}", flush=True)
    for sid, market, research in PRESETS:
        if a.only and a.only not in sid and a.only != market:
            continue
        sym = names.get(market)
        if not sym:
            print(f"{sid:28s} {market:7s} not on this account", flush=True)
            continue
        cmd = [sys.executable, str(ROOT / "scripts/run_app_backtest.py"), "--strategy", sid, "--symbol", sym,
               "--start", a.start, "--end", a.end, "--balance", "10000", "--risk", "1"]
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=3600)
        try:
            r = json.loads(p.stdout[p.stdout.index("{"):])
            print(f"{sid:28s} {sym:14s} {r.get('trades'):4} tr  win {r.get('win_rate')}  PF {r.get('profit_factor')}  "
                  f"expR {r.get('expectancy_r')}  net ${r.get('net_pnl')}  DD {r.get('max_dd_pct')}%  | research {research}",
                  flush=True)
        except Exception:  # noqa: BLE001
            tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
            print(f"{sid:28s} {sym:14s} FAILED: {' | '.join(tail)[:300]}", flush=True)
    print("AV_VERIFY_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
