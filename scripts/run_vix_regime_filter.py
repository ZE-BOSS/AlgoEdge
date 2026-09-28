#!/usr/bin/env python
"""
scripts/run_vix_regime_filter.py

Does a volatility-regime filter improve the strategies we already run?

WHY THIS IS THE CHEAP VERSION OF THE GAMMA QUESTION
----------------------------------------------------
The dealer-gamma claim is that when market makers are net long gamma their
hedging leans against price (quiet, mean-reverting tape) and when they are net
short it leans with it (fast, trending tape). Computing dealer gamma properly
needs a full option chain with open interest per strike AND an assumption about
which side dealers are on -- the second of which is inferred, never observed,
and is where most of the error in every public GEX number lives.

Two things about the volatility surface are free, observable, and move with the
same regime:

    VIX LEVEL           how much movement is being paid for
    VIX TERM STRUCTURE  VIX vs 3-month VIX. In CONTANGO (spot below 3-month) the
                        market is calm and vol sellers are being paid to wait;
                        in BACKWARDATION (spot above 3-month) something is
                        breaking and hedging demand is immediate.

Backwardation is the regime that coincides with short-gamma, amplifying tape.
It is a proxy, not the thing -- but if a free proxy for the regime does nothing
for our book, a $599/month chain subscription is not going to rescue it. That is
the decision this script exists to make.

HOW IT IS JUDGED
----------------
A filter is only worth anything if it is chosen on one window and still helps on
another. Every regime split here is chosen on the EARLY window and reported
unchanged on the LATE one, per strategy and per market, never pooled into a
single number.

    py -3.12 scripts/run_vix_regime_filter.py
    py -3.12 scripts/run_vix_regime_filter.py --split 2024-09-01
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_app_form_check import (  # noqa: E402
    app_drive, app_overnight, app_trend, broker_utc_offset_hours, resample,
)
from scripts.run_published_strategies import FEEDS, load, orb_trades  # noqa: E402

OUT = ROOT / "data" / "vix_regime"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"

MARKETS = ["US Tech 100", "US SP 500", "Germany 40", "XAUUSD", "XAGUSD",
           "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY"]
INDEX_MARKETS = ["US Tech 100", "US SP 500", "Germany 40"]


def fred(series_id: str) -> dict[date, float]:
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / f"{series_id}.csv"
    if cache.exists():
        text = cache.read_text()
    else:
        with urllib.request.urlopen(FRED.format(series_id), timeout=120) as r:
            text = r.read().decode()
        cache.write_text(text)
    out: dict[date, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        raw = (row.get(series_id) or "").strip()
        stamp = (row.get("observation_date") or row.get("DATE") or "").strip()
        if raw in ("", "."):
            continue
        try:
            out[date.fromisoformat(stamp)] = float(raw)
        except ValueError:
            continue
    return out


def regimes() -> dict[date, dict]:
    """Per calendar day: the VIX level tercile and the term-structure sign.

    Terciles are computed on an EXPANDING window, never on the whole history: a
    tercile that used the future would tag 2021 with a boundary it could only
    know in 2026, and every "filter" built on it would be reading tomorrow's
    newspaper.
    """
    vix, vix3m = fred("VIXCLS"), fred("VXVCLS")
    days = sorted(vix)
    out: dict[date, dict] = {}
    seen: list[float] = []
    for d in days:
        v = vix[d]
        if len(seen) >= 250:
            lo, hi = np.percentile(seen, [33.3, 66.7])
            level = "low" if v < lo else ("high" if v > hi else "mid")
        else:
            level = "n/a"
        seen.append(v)
        v3 = vix3m.get(d)
        term = "n/a"
        if v3:
            term = "backwardation" if v >= v3 else "contango"
        out[d] = {"vix": v, "level": level, "term": term}
    return out


# ── trade generation from the shipped strategies ────────────────────────────
def strategy_books(feed: str, since: str, slip: float = 1.0) -> dict[str, list[dict]]:
    bars_dir, specs = FEEDS[feed]
    eng.SPECS = json.loads(specs.read_text())
    t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    cache: dict[str, dict | None] = {}

    def get(sym):
        if sym not in cache:
            try:
                b = load(bars_dir, sym)
            except FileNotFoundError:
                cache[sym] = None
                return None
            m = b["time"] >= t0
            b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
            cache[sym] = b if len(b["time"]) >= 20_000 else None
        return cache[sym]

    books: dict[str, list[dict]] = {}
    for sym in MARKETS:
        b = get(sym)
        if b is None:
            continue
        off = broker_utc_offset_hours(b)
        books.setdefault("TrendBreakout", []).extend(
            app_trend(resample(b, 900), sym, slip, 20 * 96, 2.0, 1.5, 20.0, 96, trail_lookback=100))
        books.setdefault("ORB", []).extend(orb_trades(b, sym, 3.0, slip))
        if sym in INDEX_MARKETS:
            books.setdefault("OvernightSession", []).extend(app_overnight(b, sym, slip, off, 0.5))
        books.setdefault("OpeningDrive", []).extend(app_drive(b, sym, slip, off))
    return books


# ── scoring ─────────────────────────────────────────────────────────────────
def stat(trades: list[dict], risk: float = 0.5) -> dict | None:
    if len(trades) < 25:
        return None
    r = np.array([x["r"] for x in trades])
    rep = eng.report(eng.run_account(sorted(trades, key=lambda x: x["t"]), 10_000.0, risk), "x")
    if not rep.get("trades"):
        return None
    return {"n": len(r), "expectancy_r": float(r.mean()),
            "t": float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))) if r.std() else 0.0,
            "win": rep["win_rate"], "pf": rep["profit_factor"],
            "return_pct": rep["return_pct"], "dd": rep["max_dd_pct"]}


def realised_vol_regime(feed: str, since: str, window: int = 20) -> dict[tuple[str, date], str]:
    """Each instrument's OWN realised-volatility tercile, per symbol per day.

    VIX is the right regime measure for US equities and useless for everything
    else -- and worse, it is a daily FRED series, so a live strategy could not
    read today's value in time to act on it. An instrument's own realised
    volatility is computable from the bars the strategy already holds, works on
    gold and BTC and FX as well as indices, and needs no external feed at all.

    The question this answers is whether the cheap self-contained version
    reproduces what VIX found. If it does, the filter can ship inside a strategy;
    if it does not, VIX was measuring something realised vol is not.

    Terciles are EXPANDING-window, so no boundary uses the future.
    """
    bars_dir, specs = FEEDS[feed]
    eng.SPECS = json.loads(specs.read_text())
    t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    out: dict[tuple[str, date], str] = {}
    for sym in MARKETS:
        try:
            b = load(bars_dir, sym)
        except FileNotFoundError:
            continue
        m = b["time"] >= t0
        t, c = b["time"][m], b["close"][m]
        if len(t) < 20_000:
            continue
        day = (t // 86400).astype(np.int64)
        edges = np.r_[0, np.flatnonzero(np.diff(day)) + 1]
        closes = c[np.r_[edges[1:] - 1, len(c) - 1]]
        days = [datetime.fromtimestamp(int(t[i]), timezone.utc).date() for i in edges]
        ret = np.zeros(len(closes))
        ret[1:] = np.diff(closes) / closes[:-1]
        seen: list[float] = []
        for k in range(len(days)):
            if k >= window:
                rv = float(np.std(ret[k - window:k]))       # strictly prior bars
                if len(seen) >= 120:
                    lo, hi = np.percentile(seen, [33.3, 66.7])
                    out[(sym, days[k])] = "low" if rv < lo else ("high" if rv > hi else "mid")
                seen.append(rv)
    return out


def rvol_ratio_regime(feed: str, since: str, short: int = 20,
                      long: int = 60) -> dict[tuple[str, date], str]:
    """Short-window realised vol divided by long-window, bucketed.

    The percentile version above needs a long history of prior daily vols to know
    where today sits, which a strategy's bar window cannot hold: TrendBreakout
    carries 21 days of M15. A RATIO is self-normalising -- it only needs `long`
    days, and it answers "is this instrument quiet relative to its own recent
    normal", which is the same question a tercile answers.

    Tested here rather than assumed, because a filter that reproduces the
    tercile result is shippable inside a strategy and one that does not is a
    different filter wearing the same name.
    """
    bars_dir, specs = FEEDS[feed]
    eng.SPECS = json.loads(specs.read_text())
    t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    out: dict[tuple[str, date], str] = {}
    for sym in MARKETS:
        try:
            b = load(bars_dir, sym)
        except FileNotFoundError:
            continue
        m = b["time"] >= t0
        t, c = b["time"][m], b["close"][m]
        if len(t) < 20_000:
            continue
        day = (t // 86400).astype(np.int64)
        edges = np.r_[0, np.flatnonzero(np.diff(day)) + 1]
        closes = c[np.r_[edges[1:] - 1, len(c) - 1]]
        days = [datetime.fromtimestamp(int(t[i]), timezone.utc).date() for i in edges]
        ret = np.zeros(len(closes))
        ret[1:] = np.diff(closes) / closes[:-1]
        for k in range(long + 1, len(days)):
            sv = float(np.std(ret[k - short:k]))        # strictly prior bars
            lv = float(np.std(ret[k - long:k]))
            if lv <= 0:
                continue
            r = sv / lv
            out[(sym, days[k])] = "low" if r < 0.85 else ("high" if r > 1.15 else "mid")
    return out


def tag_realised(trades: list[dict], reg: dict[tuple[str, date], str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for x in trades:
        d = datetime.fromtimestamp(int(x["t"]), timezone.utc).date()
        bucket = reg.get((x["symbol"], d))
        if bucket:
            out.setdefault(bucket, []).append(x)
    return out


def tag(trades: list[dict], reg: dict[date, dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for x in trades:
        d = datetime.fromtimestamp(int(x["t"]), timezone.utc).date()
        r = reg.get(d)
        if r is None:                      # weekend / holiday: carry the last reading
            for back in range(1, 5):
                r = reg.get(date.fromordinal(d.toordinal() - back))
                if r:
                    break
        if r is None or r[key] == "n/a":
            continue
        out.setdefault(r[key], []).append(x)
    return out


ROW = f"  {'bucket':18s} {'N':>6s} {'expR':>8s} {'win%':>6s} {'PF':>6s} {'t':>7s} {'ret%':>8s} {'DD%':>6s}"


def show(label: str, buckets: dict[str, list[dict]], order: list[str]) -> dict:
    print(f"\n  {label}")
    print(ROW)
    print("  " + "-" * (len(ROW) - 2))
    got = {}
    for name in order:
        st = stat(buckets.get(name, []))
        got[name] = st
        if st is None:
            print(f"  {name:18s} {len(buckets.get(name, [])):>6d}   too few")
            continue
        print(f"  {name:18s} {st['n']:>6d} {st['expectancy_r']:>+8.3f} {st['win']:>5.1f}% "
              f"{st['pf']:>6.2f} {st['t']:>+7.2f} {st['return_pct']:>+7.1f}% {st['dd']:>5.1f}%")
    return got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2021-10-01")
    ap.add_argument("--split", default="2024-09-01", help="in-sample ends here")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    reg = regimes()
    print(f"\nVIX REGIME FILTER -- {len(reg):,} days of VIX, "
          f"{sum(1 for v in reg.values() if v['term'] != 'n/a'):,} with a term structure")
    print("Terciles are expanding-window, so no bucket boundary uses the future.")

    books = strategy_books("deriv", args.since)
    rvol = realised_vol_regime("deriv", args.since)
    rratio = rvol_ratio_regime("deriv", args.since)
    print(f"realised-vol buckets: {len(rvol):,} symbol-days")
    split = int(datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc).timestamp())
    results: dict = {}

    for strategy, trades in sorted(books.items()):
        ins = [x for x in trades if x["t"] < split]
        oos = [x for x in trades if x["t"] >= split]
        print(f"\n{'=' * 78}\n{strategy}  --  {len(trades):,} trades "
              f"({len(ins):,} in-sample to {args.split}, {len(oos):,} after)\n{'=' * 78}")
        base_in, base_out = stat(ins), stat(oos)
        for name, st in (("ALL in-sample", base_in), ("ALL out-of-sample", base_out)):
            if st:
                print(f"  {name:20s} n {st['n']:>5d}  expR {st['expectancy_r']:>+.3f}  "
                      f"win {st['win']:.1f}%  PF {st['pf']:.2f}  t {st['t']:+.2f}")
        for key, order in (("term", ["contango", "backwardation"]),
                           ("level", ["low", "mid", "high"])):
            a = show(f"{key} -- IN-SAMPLE", tag(ins, reg, key), order)
            b = show(f"{key} -- OUT-OF-SAMPLE", tag(oos, reg, key), order)
            results[f"{strategy}|{key}"] = {"in": a, "out": b,
                                            "base_in": base_in, "base_out": base_out}
        order = ["low", "mid", "high"]
        a = show("realised vol (own instrument) -- IN-SAMPLE", tag_realised(ins, rvol), order)
        b = show("realised vol (own instrument) -- OUT-OF-SAMPLE", tag_realised(oos, rvol), order)
        results[f"{strategy}|rvol"] = {"in": a, "out": b,
                                       "base_in": base_in, "base_out": base_out}
        a = show("rv20/rv60 ratio -- IN-SAMPLE", tag_realised(ins, rratio), order)
        b = show("rv20/rv60 ratio -- OUT-OF-SAMPLE", tag_realised(oos, rratio), order)
        results[f"{strategy}|rvratio"] = {"in": a, "out": b,
                                          "base_in": base_in, "base_out": base_out}

    # Which split, chosen in-sample, would still have helped out-of-sample?
    print(f"\n{'=' * 78}\nVERDICT -- the bucket chosen IN-SAMPLE, scored OUT-OF-SAMPLE\n{'=' * 78}")
    print(f"  {'strategy / key':34s} {'pick':16s} {'IN expR':>9s} {'OUT expR':>9s} "
          f"{'OUT base':>9s} {'better?':>8s}")
    for tag_key, got in sorted(results.items()):
        ins, outs = got["in"], got["out"]
        usable = {k: v for k, v in ins.items() if v}
        if not usable or not got["base_out"]:
            continue
        pick = max(usable, key=lambda k: usable[k]["expectancy_r"])
        out_st = outs.get(pick)
        if out_st is None:
            print(f"  {tag_key:34s} {pick:16s} {usable[pick]['expectancy_r']:>+9.3f} "
                  f"{'--':>9s} {got['base_out']['expectancy_r']:>+9.3f} {'no data':>8s}")
            continue
        better = out_st["expectancy_r"] > got["base_out"]["expectancy_r"]
        print(f"  {tag_key:34s} {pick:16s} {usable[pick]['expectancy_r']:>+9.3f} "
              f"{out_st['expectancy_r']:>+9.3f} {got['base_out']['expectancy_r']:>+9.3f} "
              f"{'YES' if better else 'no':>8s}")

    (OUT / "vix_regime.json").write_text(json.dumps(results, indent=1, default=float))
    print(f"\n-> {OUT / 'vix_regime.json'}")


if __name__ == "__main__":
    main()
