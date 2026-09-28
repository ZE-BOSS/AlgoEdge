#!/usr/bin/env python
"""
scripts/run_deribit_flow_study.py

Does options order flow move the underlying? The pipeline, and what it says so far.

WHY THIS CAN DO SOMETHING PUBLIC GEX CANNOT
--------------------------------------------
Every public gamma-exposure number rests on a heuristic: open interest is
observable but WHO IS LONG IT is not, so the convention is "customers buy, dealers
sell" and the sign of dealer positioning is assumed. That assumption is where
most of the error in every GEX figure lives.

Deribit's trade feed carries the AGGRESSOR'S SIDE on every print. When a trade
says `buy`, the taker bought and the maker — the dealer — is short it. The sign
is observed, not assumed. So dealer exposure can be built from flow directly:

    dealer hedge demand  = taker_sign x delta x size
    dealer gamma         = -taker_sign x gamma x size

A taker buying a call leaves the dealer short a call, which the dealer hedges by
BUYING the underlying (positive hedge demand) and which leaves the dealer short
gamma (amplifying). A taker buying a put leaves the dealer short a put, hedged by
SELLING. Deltas and gammas are Black-Scholes from the strike and expiry in the
instrument name, the implied volatility on the trade, and the index price stamped
on the trade itself.

THE HONEST STATE OF THIS
------------------------
Deribit's public trade history reaches back about 36 hours, so this runs on
whatever `collect_deribit_flow.py` has accumulated. Two days of data proves the
pipeline; it does not test the hypothesis. The sample size is printed at the top
of every table for exactly that reason, and no conclusion should be drawn from a
few hundred buckets.

    py -3.12 scripts/collect_deribit_flow.py --seed
    py -3.12 scripts/run_deribit_flow_study.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STORE = ROOT / "data" / "deribit"
OUT = ROOT / "data" / "deribit"
# Deribit options expire at 08:00 UTC on their stated day.
EXPIRY_HOUR = 8
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def parse_instrument(name: str) -> tuple[str, float, float, str] | None:
    """`BTC-27NOV26-99000-C` -> (currency, expiry epoch, strike, "C"/"P")."""
    parts = name.split("-")
    if len(parts) != 4:
        return None
    currency, when, strike, kind = parts
    try:
        day = int(when[:-5])
        month = MONTHS[when[-5:-2]]
        year = 2000 + int(when[-2:])
        expiry = datetime(year, month, day, EXPIRY_HOUR, tzinfo=timezone.utc).timestamp()
        return currency, expiry, float(strike), kind
    except (ValueError, KeyError):
        return None


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def greeks(spot: float, strike: float, years: float, vol: float,
           kind: str) -> tuple[float, float] | None:
    """(delta, gamma), Black-Scholes at zero rates.

    Zero rates because these are crypto options with no carry to speak of over a
    horizon of days, and a rate assumption would add error without adding
    information.
    """
    if spot <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return None
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * vol * vol * years) / (vol * sqrt_t)
    delta = _norm_cdf(d1) if kind == "C" else _norm_cdf(d1) - 1.0
    gamma = _norm_pdf(d1) / (spot * vol * sqrt_t)
    return delta, gamma


def load_trades(currency: str) -> list[dict]:
    folder = STORE / "trades"
    if not folder.exists():
        return []
    rows = []
    for path in sorted(folder.glob("*.jsonl")):
        for line in path.open(encoding="utf-8"):
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            if t.get("instrument_name", "").startswith(currency + "-"):
                rows.append(t)
    rows.sort(key=lambda x: int(x["timestamp"]))
    return rows


def enrich(trades: list[dict]) -> list[dict]:
    """Attach dealer hedge demand and dealer gamma to every trade."""
    out = []
    for t in trades:
        parsed = parse_instrument(t.get("instrument_name", ""))
        if parsed is None:
            continue
        _, expiry, strike, kind = parsed
        stamp = int(t["timestamp"]) / 1000.0
        years = (expiry - stamp) / (365.25 * 86400)
        spot = float(t.get("index_price") or 0.0)
        vol = float(t.get("iv") or 0.0) / 100.0
        g = greeks(spot, strike, years, vol, kind)
        if g is None:
            continue
        delta, gamma = g
        size = float(t.get("amount") or 0.0)
        # `direction` is the TAKER's side; the dealer is on the other one.
        sign = 1.0 if str(t.get("direction")) == "buy" else -1.0
        out.append({
            "t": stamp, "spot": spot, "strike": strike, "kind": kind,
            "size": size, "sign": sign, "delta": delta, "gamma": gamma,
            "hedge": sign * delta * size,        # underlying the dealer must buy(+)/sell(-)
            "dealer_gamma": -sign * gamma * size * spot * spot / 100.0,
            "dte": years * 365.25,
        })
    return out


def bucket(rows: list[dict], seconds: int) -> list[dict]:
    """Aggregate flow into time buckets, with the spot at each bucket's end."""
    if not rows:
        return []
    buckets: dict[int, dict] = {}
    for r in rows:
        key = int(r["t"] // seconds)
        b = buckets.setdefault(key, {"key": key, "hedge": 0.0, "dealer_gamma": 0.0,
                                     "n": 0, "size": 0.0, "spot": r["spot"],
                                     "last_t": r["t"]})
        b["hedge"] += r["hedge"]
        b["dealer_gamma"] += r["dealer_gamma"]
        b["size"] += r["size"]
        b["n"] += 1
        if r["t"] >= b["last_t"]:
            b["last_t"], b["spot"] = r["t"], r["spot"]
    return [buckets[k] for k in sorted(buckets)]


def correlate(rows: list[dict], key: str, horizon: int) -> dict | None:
    """Does `key` in one bucket predict the return `horizon` buckets later?"""
    if len(rows) < horizon + 30:
        return None
    x = np.array([r[key] for r in rows[:-horizon]], dtype=float)
    spot = np.array([r["spot"] for r in rows], dtype=float)
    fwd = (spot[horizon:] - spot[:-horizon]) / spot[:-horizon] * 10_000.0   # bps
    n = min(len(x), len(fwd))
    x, fwd = x[:n], fwd[:n]
    if x.std() == 0 or fwd.std() == 0:
        return None
    r = float(np.corrcoef(x, fwd)[0, 1])
    t = r * math.sqrt(max(n - 2, 1) / max(1e-12, 1 - r * r))
    # what a long/short on the sign of the signal would have earned, in bps
    signal = np.sign(x)
    return {"n": n, "corr": r, "t": t,
            "bps_per_bucket": float((signal * fwd).mean()),
            "hit": float((np.sign(signal * fwd) > 0).mean() * 100)}


HDR = f"  {'signal':26s} {'horizon':>8s} {'n':>6s} {'corr':>8s} {'t':>7s} {'bps':>8s} {'hit%':>7s}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--currency", default="BTC")
    ap.add_argument("--bucket", type=int, default=300, help="seconds per bucket")
    ap.add_argument("--max-dte", type=float, default=0, help="only options expiring within N days")
    args = ap.parse_args()

    trades = load_trades(args.currency)
    if not trades:
        print(f"\nNo {args.currency} trades held yet. Run:")
        print("  py -3.12 scripts/collect_deribit_flow.py --seed")
        return

    rows = enrich(trades)
    if args.max_dte:
        rows = [r for r in rows if r["dte"] <= args.max_dte]
    first = datetime.fromtimestamp(rows[0]["t"], timezone.utc)
    last = datetime.fromtimestamp(rows[-1]["t"], timezone.utc)
    span_h = (rows[-1]["t"] - rows[0]["t"]) / 3600

    print(f"\nDERIBIT OPTION FLOW -> {args.currency}")
    print(f"{len(rows):,} trades priced, {first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M} UTC "
          f"({span_h:.1f}h)")
    if args.max_dte:
        print(f"restricted to options expiring within {args.max_dte:g} days")

    buckets = bucket(rows, args.bucket)
    print(f"{len(buckets):,} buckets of {args.bucket}s\n")

    if span_h < 24 * 14:
        print("  !! THIS IS A PIPELINE PROOF, NOT A RESULT.")
        print(f"  !! {span_h:.0f} hours of flow cannot test anything. Deribit's public")
        print("  !! history reaches ~36h; collect_deribit_flow.py --watch accumulates")
        print("  !! forward. Come back when this says weeks.\n")

    print(HDR)
    print("  " + "-" * (len(HDR) - 2))
    results = {}
    for key, label in (("hedge", "dealer hedge demand"),
                       ("dealer_gamma", "dealer gamma"),
                       ("size", "raw option volume")):
        for horizon in (1, 3, 6, 12):
            got = correlate(buckets, key, horizon)
            if got is None:
                continue
            results[f"{key}|{horizon}"] = got
            star = "  <--" if abs(got["t"]) >= 2.0 else ""
            print(f"  {label:26s} {horizon:>8d} {got['n']:>6d} {got['corr']:>+8.4f} "
                  f"{got['t']:>+7.2f} {got['bps_per_bucket']:>+8.2f} {got['hit']:>6.1f}%{star}")
        print()

    flows = np.array([b["hedge"] for b in buckets])
    gam = np.array([b["dealer_gamma"] for b in buckets])
    print(f"  dealer hedge demand: median {np.median(flows):+.2f} {args.currency}/bucket, "
          f"90th pct {np.percentile(np.abs(flows), 90):.2f}")
    print(f"  dealer gamma:        {(gam > 0).mean() * 100:.0f}% of buckets net LONG gamma "
          f"(dampening), {(gam < 0).mean() * 100:.0f}% short (amplifying)")

    (OUT / f"flow_study_{args.currency}.json").write_text(
        json.dumps({"trades": len(rows), "hours": span_h, "buckets": len(buckets),
                    "results": results}, indent=1, default=float))
    print(f"\n-> {OUT / f'flow_study_{args.currency}.json'}")


if __name__ == "__main__":
    main()
