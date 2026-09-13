#!/usr/bin/env python
"""
scripts/run_edge_lab.py

Published VWAP / price-action setups, every confluence combination, one shared
configuration across markets (backend/analytics/edge_lab.py).

    # 1. cache M5 bars + contract specs from MT5 (sequential: MT5 dislikes parallel clients)
    python scripts/run_edge_lab.py fetch --markets "US Tech 100" XAUUSD BTCUSD GBPJPY

    # 2. build candidates per market (safe to run in parallel, one process per market)
    python scripts/run_edge_lab.py build --markets XAUUSD

    # 3. search, ablate, and turn the picks into dollars on the last 8 months
    python scripts/run_edge_lab.py search --markets "US Tech 100" XAUUSD BTCUSD GBPJPY

    # 4. re-run the chosen configurations unchanged on other markets
    python scripts/run_edge_lab.py confirm --markets EURJPY USDJPY ...
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics import edge_lab as lab  # noqa: E402
from backend.analytics.money_sim import AccountRules, Contract, shuffled_paths, simulate_account  # noqa: E402
from backend.analytics.strategy_search import Bars  # noqa: E402

OUT = ROOT / "data" / "edge_lab"
BARS = OUT / "bars"
CANDS = OUT / "cands"
MAIN = ["US Tech 100", "XAUUSD", "BTCUSD", "GBPJPY"]


def slug(sym: str) -> str:
    return sym.replace(" ", "_")


def cmd_fetch(markets: list[str]) -> None:
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 not available: {mt5.last_error()}")
    BARS.mkdir(parents=True, exist_ok=True)
    specs = json.loads((OUT / "specs.json").read_text()) if (OUT / "specs.json").exists() else {}
    for sym in markets:
        if not mt5.symbol_select(sym, True):
            print(f"{sym}: not on this account")
            continue
        info = mt5.symbol_info(sym)
        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, datetime(2021, 10, 1, tzinfo=timezone.utc),
                                     datetime(2026, 9, 13, tzinfo=timezone.utc))
        if rates is None or len(rates) < 20000:
            print(f"{sym}: not enough M5 history")
            continue
        np.savez_compressed(BARS / f"{slug(sym)}.npz", time=rates["time"], open=rates["open"], high=rates["high"],
                            low=rates["low"], close=rates["close"], spread=rates["spread"],
                            tick_volume=rates["tick_volume"], point=info.point)
        specs[sym] = {"value_per_price_per_lot": info.trade_tick_value / info.trade_tick_size if info.trade_tick_size else 0.0,
                      "min_lot": info.volume_min, "lot_step": info.volume_step, "max_lot": info.volume_max}
        print(f"{sym}: {len(rates)} M5 bars from {datetime.fromtimestamp(int(rates['time'][0]), timezone.utc).date()}")
    mt5.shutdown()
    (OUT / "specs.json").write_text(json.dumps(specs, indent=1))


def load_bars(sym: str) -> Bars:
    z = np.load(BARS / f"{slug(sym)}.npz")
    point = float(z["point"])
    return Bars(sym, "M5", z["time"].astype(np.int64), z["open"].astype(float), z["high"].astype(float),
                z["low"].astype(float), z["close"].astype(float),
                np.maximum(z["spread"].astype(float) * point, 0.0), z["tick_volume"].astype(float))


def cmd_build(markets: list[str], families: list[str] | None) -> None:
    CANDS.mkdir(parents=True, exist_ok=True)
    for sym in markets:
        t0 = time.time()
        b = load_bars(sym)
        ctxs = {w: lab.build_ctx(b, lab.session_for(sym, w)) for w in ("native", "alt")}
        path = CANDS / f"{slug(sym)}.pkl"
        blob = pickle.loads(path.read_bytes()) if path.exists() else {}
        for fam in lab.FAMILIES:
            if families and fam.name not in families:
                continue
            blob[fam.name] = lab.build_family(ctxs, fam)
            print(f"{sym} {fam.name}: " + ", ".join(f"{k}={len(v)}" for k, v in blob[fam.name].items()), flush=True)
        path.write_bytes(pickle.dumps(blob))
        print(f"{sym}: built in {time.time() - t0:.0f}s", flush=True)


def fmt(s: dict | None) -> str:
    if not s or not s.get("n"):
        return f"{'-':>4} {'':>8} {'':>5} {'':>5}"
    return f"{s['n']:>4} {s['avg_r']:+.3f}R {s['pf'] or 0:>5.2f} {s['t'] or 0:>+5.1f}"


def contracts() -> dict[str, Contract]:
    specs = json.loads((OUT / "specs.json").read_text())
    return {k: Contract(v["value_per_price_per_lot"], v["min_lot"], v["lot_step"], v["max_lot"]) for k, v in specs.items()}


def money_rows(name: str, legs: list, cons: dict[str, Contract]) -> list[dict]:
    rows = []
    for cap, risk, comp in product([350.0, 10000.0], [1.0, 1.8], [False, True]):
        s = simulate_account(legs, cons, AccountRules(cap, risk, compounding=comp, daily_dd_cap_pct=10.0))
        rows.append({"book": name, "capital": cap, "risk_pct": risk, "compounding": comp, **s})
    return rows


def print_money(rows: list[dict]) -> None:
    print(f"    {'capital':>8} {'risk':>4} {'comp':>4} | {'net $':>9} {'ret%':>7} {'maxDD%':>6} {'PF':>5} "
          f"{'expR':>6} {'exp$':>7} {'Sharpe':>6} {'Sortino':>7} {'trades':>6} {'+mo':>5} {'skip':>4}")
    for s in rows:
        print(f"    {s['capital']:>8.0f} {s['risk_pct']:>4} {str(s['compounding'])[0]:>4} | {s['net_pnl']:>9,.0f} "
              f"{s['return_pct']:>7.1f} {s['max_dd_pct']:>6.1f} {s['profit_factor'] or 0:>5.2f} "
              f"{s['expectancy_r'] or 0:>+6.3f} {s['expectancy_usd'] or 0:>7.2f} {s['sharpe'] or 0:>6.2f} "
              f"{s['sortino'] or 0:>7.2f} {s['trades']:>6} {s['positive_months']:>2}/{s['months']:<2} "
              f"{s['skipped_unsizable']:>4}")


def load_cands(markets: list[str]) -> dict[str, dict]:
    return {sym: pickle.loads((CANDS / f"{slug(sym)}.pkl").read_bytes()) for sym in markets
            if (CANDS / f"{slug(sym)}.pkl").exists()}


def cmd_search(markets: list[str], families: list[str] | None, robust: bool = False) -> None:
    blobs = load_cands(markets)
    cons = contracts()
    select_windows = (lab.HOLDOUT, lab.SELECT) if robust else (lab.SELECT,)
    report = {"generated": datetime.now(timezone.utc).isoformat(), "windows": lab.WINDOWS,
              "select": list(select_windows), "markets": list(blobs), "families": {}}
    oos = lab.WINDOWS["last_8m_2026"]
    for fam in lab.FAMILIES:
        if families and fam.name not in families:
            continue
        data = {sym: blob[fam.name] for sym, blob in blobs.items() if fam.name in blob}
        t0 = time.time()
        res = lab.search(fam, data, select_windows=select_windows)
        ch = res["chosen"]
        print(f"\n=== {fam.name} — {fam.source} | {res['configs_tried']} configs, "
              f"{res['configs_eligible']} pass the rule ({time.time() - t0:.0f}s)")
        if ch is None:
            report["families"][fam.name] = res
            continue
        print(f"  chosen: session={ch['axis']} gates={ch['use'] or 'none'} exit={ch['exit']}"
              f"{'' if res['chosen_passed_rule'] else '   (NO CONFIG PASSED — best by t shown)'}")
        print(f"  {'market':<12} " + " | ".join(f"{w:^26}" for w in lab.WINDOWS))
        for sym in list(data) + ["POOLED"]:
            cells = [fmt(ch["pooled"][w] if sym == "POOLED" else ch["per_market"][w].get(sym)) for w in lab.WINDOWS]
            print(f"  {sym:<12} " + " | ".join(cells))
        print("  top 10 by selection t -> last 8 months pooled:")
        for c in res["top10"]:
            print(f"    {c['axis']:<14} {','.join(c['use']) or 'none':<40} {c['exit']:<6} "
                  f"sel {fmt(c['pooled'][lab.SELECT])}  8m {fmt(c['pooled']['last_8m_2026'])}")
        e = lab.EXITS.index(ch["exit"])
        res["ablation"] = lab.ablation(fam, data, ch["axis"], e)
        print("  each confluence alone (edge vs none, pooled) | markets helped on last 8m:")
        for row in res["ablation"]:
            print(f"    {row['confluence']:<14} sel {row[lab.SELECT]['edge'] if row[lab.SELECT]['edge'] is not None else float('nan'):+.3f}R"
                  f"  8m {row['last_8m_2026']['edge'] if row['last_8m_2026']['edge'] is not None else float('nan'):+.3f}R"
                  f"  helped {row['last_8m_2026']['markets_helped']}")
        use = tuple(fam.features.index(u) for u in ch["use"])
        money = []
        book = []
        for sym, sets in data.items():
            legs = lab.legs_for(sets[ch["axis"]], oos, use, e)
            book += legs
            rows = money_rows(f"{fam.name} | {sym} | last 8m", legs, cons)
            money += rows
            print(f"  $ {sym} last 8 months ({len(legs)} trades)")
            print_money(rows)
        rows = money_rows(f"{fam.name} | ALL 4 | last 8m", book, cons)
        money += rows
        print(f"  $ all markets together, last 8 months ({len(book)} trades)")
        print_money(rows)
        if book:
            res["shuffled_10k_1pct"] = shuffled_paths(book, cons, AccountRules(10000.0, 1.0, daily_dd_cap_pct=10.0), n=200, seed=5)
        res["money"] = money
        report["families"][fam.name] = res
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"search_{'robust_' if robust else ''}{'_'.join(slug(m) for m in blobs)[:60]}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\nsaved {path}")


def cmd_confirm(markets: list[str], source: Path) -> None:
    rep = json.loads(source.read_text())
    blobs = load_cands(markets)
    cons = contracts()
    out = {}
    for name, res in rep["families"].items():
        ch = res.get("chosen")
        if not ch:
            continue
        fam = lab.FAMILY_BY_NAME[name]
        data = {sym: blob[name] for sym, blob in blobs.items() if name in blob}
        use = tuple(fam.features.index(u) for u in ch["use"])
        e = lab.EXITS.index(ch["exit"])
        cfg = lab.evaluate(fam, data, ch["axis"], use, e, lab.WINDOWS)
        print(f"\n=== {name} unchanged: session={ch['axis']} gates={ch['use'] or 'none'} exit={ch['exit']}")
        print(f"  {'market':<16} " + " | ".join(f"{w:^26}" for w in lab.WINDOWS))
        for sym in list(data) + ["POOLED"]:
            cells = [fmt(cfg.pooled[w] if sym == "POOLED" else cfg.per_market[w].get(sym)) for w in lab.WINDOWS]
            print(f"  {sym:<16} " + " | ".join(cells))
        out[name] = lab.asdict_cfg(cfg)
    path = OUT / "confirm.json"
    path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"\nsaved {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "build", "search", "confirm"])
    ap.add_argument("--markets", nargs="+", default=MAIN)
    ap.add_argument("--families", nargs="*", default=None)
    ap.add_argument("--source", default=None, help="confirm: the search JSON whose picks to re-run")
    ap.add_argument("--robust", action="store_true",
                    help="search: must be positive on 2022-23 AND 2024-25; ranked by the worse of the two")
    a = ap.parse_args()
    if a.cmd == "fetch":
        cmd_fetch(a.markets)
    elif a.cmd == "build":
        cmd_build(a.markets, a.families)
    elif a.cmd == "search":
        cmd_search(a.markets, a.families, a.robust)
    else:
        cmd_confirm(a.markets, Path(a.source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
