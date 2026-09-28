#!/usr/bin/env python
"""
scripts/run_expiry_study.py

Do options expiries move the index CFDs we already trade?

WHY THIS IS THE FIRST OPTIONS EXPERIMENT AND NOT THE LAST
----------------------------------------------------------
The expensive version of the options question needs a chain subscription, dealer
positioning assumptions and a multi-leg fill model. This one needs none of it,
because THE DATES ARE DETERMINISTIC: monthly OPEX is the third Friday, quarterly
triple witching is the third Friday of March, June, September and December, and
0DTE is every session. So "does options expiry move the index" is a pure event
study on daily bars, and if the answer is no, no chain subscription will conjure
an effect that is not there.

WHAT IS BEING TESTED
--------------------
The dealer-gamma story says that when dealers are long gamma they hedge by
selling rallies and buying dips, which suppresses realised volatility, and that
expiry releases that pin. If true it should show up as:

  * a different realised range on OPEX day,
  * a different return in the week OF expiry versus the week AFTER,
  * something larger on triple witching than on an ordinary month.

THE CONTROL THAT MATTERS
------------------------
OPEX is ALWAYS a Friday. Comparing OPEX days against all other days measures the
Friday effect and the expiry effect together, and reports their sum as if it were
the second one. Every test below compares OPEX Fridays against OTHER FRIDAYS.

FX is the second control: EURUSD and friends have no equity-options expiry on
that calendar, so an "expiry effect" that shows up there is a calendar artefact.

DATA, ALL FREE
--------------
  * FRED (no API key): 10 years of daily S&P 500, Nasdaq 100 and Dow.
  * The Deriv and FundedNext terminals for the CFDs we would actually trade,
    to check the effect survives onto the instrument rather than only the index.

    py -3.12 scripts/run_expiry_study.py
    py -3.12 scripts/run_expiry_study.py --cfd
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "expiry"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
FRED_SERIES = {"S&P 500": "SP500", "Nasdaq 100": "NASDAQ100", "Dow 30": "DJIA"}


# ── the calendar ────────────────────────────────────────────────────────────
def third_friday(year: int, month: int) -> date:
    """Monthly OPEX: the third Friday. Standard US equity-option expiry."""
    d = date(year, month, 1)
    fridays = 0
    while True:
        if d.weekday() == 4:
            fridays += 1
            if fridays == 3:
                return d
        d += timedelta(days=1)


def opex_days(first: date, last: date) -> set[date]:
    out = set()
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.add(third_friday(y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def is_triple_witching(d: date) -> bool:
    return d.month in (3, 6, 9, 12) and d == third_friday(d.year, d.month)


def week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso[0], iso[1])


# ── data ────────────────────────────────────────────────────────────────────
def fred_series(series_id: str) -> tuple[list[date], np.ndarray]:
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / f"fred_{series_id}.csv"
    if cache.exists():
        text = cache.read_text()
    else:
        with urllib.request.urlopen(FRED.format(series_id), timeout=120) as r:
            text = r.read().decode()
        cache.write_text(text)
    days, values = [], []
    for row in csv.DictReader(io.StringIO(text)):
        raw = (row.get(series_id) or row.get(series_id.upper()) or "").strip()
        if raw in ("", "."):                      # FRED marks holidays with a dot
            continue
        stamp = (row.get("observation_date") or row.get("DATE") or "").strip()
        try:
            days.append(date.fromisoformat(stamp))
            values.append(float(raw))
        except ValueError:
            continue
    return days, np.asarray(values, dtype=float)


def mt5_daily(symbol: str, terminal: str) -> tuple[list[date], dict[str, np.ndarray]] | None:
    import os

    import MetaTrader5 as mt5
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    path = os.getenv("DERIV_MT5_PATH" if terminal == "deriv" else "MT5_PATH")
    if not mt5.initialize(path=path):
        return None
    try:
        if not mt5.symbol_select(symbol, True):
            return None
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 5000)
        if rates is None or len(rates) < 200:
            return None
        days = [datetime.fromtimestamp(int(r[0]), timezone.utc).date() for r in rates]
        return days, {k: np.asarray([float(r[i]) for r in rates])
                      for i, k in ((1, "open"), (2, "high"), (3, "low"), (4, "close"))}
    finally:
        mt5.shutdown()


# ── the tests ───────────────────────────────────────────────────────────────
def welch(a: np.ndarray, b: np.ndarray) -> float:
    """t for "a's mean differs from b's", unequal variances."""
    if len(a) < 3 or len(b) < 3:
        return 0.0
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    return float((a.mean() - b.mean()) / np.sqrt(va + vb)) if va + vb > 0 else 0.0


def day_tests(name: str, days: list[date], close: np.ndarray,
              rng: np.ndarray | None = None) -> list[dict]:
    """OPEX Fridays against OTHER Fridays, on return and on realised range."""
    ret = np.zeros(len(close))
    ret[1:] = np.diff(close) / close[:-1] * 100.0
    opex = opex_days(days[0], days[-1])

    fri = np.array([d.weekday() == 4 for d in days])
    is_opex = np.array([d in opex for d in days])
    is_tw = np.array([is_triple_witching(d) for d in days])

    rows = []

    def add(label, mask_a, mask_b, values, unit):
        a, b = values[mask_a], values[mask_b]
        if len(a) < 5 or len(b) < 5:
            return
        rows.append({"market": name, "test": label, "n_event": int(len(a)),
                     "n_control": int(len(b)), "event": float(a.mean()),
                     "control": float(b.mean()), "diff": float(a.mean() - b.mean()),
                     "t": welch(a, b), "unit": unit})

    add("OPEX Friday return vs other Fridays", fri & is_opex, fri & ~is_opex, ret, "%")
    add("OPEX Friday |return| vs other Fridays", fri & is_opex, fri & ~is_opex, np.abs(ret), "%")
    add("Triple witching return vs other Fridays", is_tw, fri & ~is_opex, ret, "%")
    add("Triple witching |return| vs other Fridays", is_tw, fri & ~is_opex, np.abs(ret), "%")
    if rng is not None:
        add("OPEX Friday range vs other Fridays", fri & is_opex, fri & ~is_opex, rng, "%")

    # the week OF expiry vs the week AFTER, on weekly return
    weeks: dict[tuple[int, int], list[float]] = {}
    for d, r in zip(days, ret):
        weeks.setdefault(week_key(d), []).append(r)
    opex_weeks = {week_key(d) for d in opex}
    after = {week_key(d + timedelta(days=7)) for d in opex}
    keys = sorted(weeks)
    wk_ret = {k: float(np.sum(weeks[k])) for k in keys}
    ev = np.array([wk_ret[k] for k in keys if k in opex_weeks])
    ct = np.array([wk_ret[k] for k in keys if k in after and k not in opex_weeks])
    other = np.array([wk_ret[k] for k in keys if k not in opex_weeks and k not in after])
    if len(ev) >= 5 and len(ct) >= 5:
        rows.append({"market": name, "test": "OPEX week vs the week after",
                     "n_event": len(ev), "n_control": len(ct), "event": float(ev.mean()),
                     "control": float(ct.mean()), "diff": float(ev.mean() - ct.mean()),
                     "t": welch(ev, ct), "unit": "%"})
    if len(ev) >= 5 and len(other) >= 5:
        rows.append({"market": name, "test": "OPEX week vs every other week",
                     "n_event": len(ev), "n_control": len(other), "event": float(ev.mean()),
                     "control": float(other.mean()), "diff": float(ev.mean() - other.mean()),
                     "t": welch(ev, other), "unit": "%"})
    return rows


HDR = (f"  {'test':44s} {'n':>5s} {'event':>9s} {'control':>9s} {'diff':>9s} {'t':>7s}")


def report(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    print(HDR)
    print("  " + "-" * (len(HDR) - 2))
    for r in rows:
        star = "  <--" if abs(r["t"]) >= 2.0 else ""
        print(f"  {r['test']:44s} {r['n_event']:>5d} {r['event']:>+8.3f}{r['unit']} "
              f"{r['control']:>+8.3f}{r['unit']} {r['diff']:>+8.3f}{r['unit']} "
              f"{r['t']:>+7.2f}{star}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cfd", action="store_true",
                    help="also run it on the CFDs we trade (needs the terminals)")
    ap.add_argument("--eras", action="store_true",
                    help="split the long series by decade: an effect that only exists "
                         "before 0DTE existed is a different claim from a live one")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []

    print("\nOPTIONS EXPIRY EVENT STUDY")
    print("OPEX is always a Friday, so every control here is OTHER FRIDAYS, not all days.")

    for name, series in FRED_SERIES.items():
        try:
            days, close = fred_series(series)
        except Exception as e:
            print(f"\n{name}: could not fetch ({e})")
            continue
        rows = day_tests(name, days, close)
        report(f"{name} -- FRED {series}, {days[0]} -> {days[-1]}, {len(days):,} sessions", rows)
        all_rows += rows
        if args.eras and len(days) > 3000:
            # An effect present only in the 1990s is a fact about a market that no
            # longer exists. 0DTE did not exist before 2022, so the last era is
            # the one that says whether this is still live.
            for lo, hi in ((1986, 2000), (2000, 2010), (2010, 2020), (2020, 2027)):
                keep = [i for i, d in enumerate(days) if lo <= d.year < hi]
                if len(keep) < 500:
                    continue
                sub_days = [days[i] for i in keep]
                sub = day_tests(f"{name} {lo}-{hi}", sub_days, close[np.array(keep)])
                report(f"  era {lo}-{hi}: {len(keep):,} sessions", sub)
                all_rows += sub

    if args.cfd:
        for terminal, symbols in (("deriv", ["US Tech 100", "US SP 500", "Germany 40",
                                             "EURUSD", "GBPUSD", "USDJPY"]),
                                  ("fundednext", ["US30", "SPX500"])):
            for symbol in symbols:
                got = mt5_daily(symbol, terminal)
                if got is None:
                    print(f"\n{symbol} ({terminal}): no daily data")
                    continue
                days, bars = got
                rng = (bars["high"] - bars["low"]) / bars["close"] * 100.0
                label = f"{symbol} [{terminal}]"
                rows = day_tests(label, days, bars["close"], rng)
                report(f"{label} -- {days[0]} -> {days[-1]}, {len(days):,} sessions", rows)
                all_rows += rows

    fx = [r for r in all_rows if any(p in r["market"] for p in ("EURUSD", "GBPUSD", "USDJPY"))]
    hits = [r for r in all_rows if abs(r["t"]) >= 2.0]
    print(f"\n{'=' * 78}")
    print(f"{len(hits)} of {len(all_rows)} tests reach |t| >= 2.")
    print(f"At the 5% level pure chance produces about {0.05 * len(all_rows):.1f} of them,")
    print("so count the hits before believing any one of them.")
    if fx:
        fx_hits = [r for r in fx if abs(r['t']) >= 2.0]
        print(f"FX control: {len(fx_hits)} of {len(fx)} FX tests also 'significant' "
              f"-- and FX has no equity-options expiry at all.")

    import json
    (OUT / "expiry_tests.json").write_text(json.dumps(all_rows, indent=1, default=float))
    print(f"\n-> {OUT / 'expiry_tests.json'}")


if __name__ == "__main__":
    main()
