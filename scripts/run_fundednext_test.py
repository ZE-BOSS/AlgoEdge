#!/usr/bin/env python
"""
scripts/run_fundednext_test.py

The round-number continuation book, run on FUNDEDNEXT's own bars and contract
specs — a different broker, different spreads, different feed — and then put
through the FundedNext evaluation rules to see whether it would pass.

This is the out-of-sample test that matters most: the edge was found on Deriv
data. If it is a property of the market it survives a change of broker. If it
was a property of the feed, it does not.

    python scripts/run_fundednext_test.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FN = ROOT / "data" / "zone_study" / "fundednext"

from scripts.run_zone_robust import sequential_trades  # noqa: E402
from scripts.run_zone_study import CONTROL_OFFSETS  # noqa: E402
from scripts import zone_money_engine as eng  # noqa: E402

# point the money engine at FundedNext's contract specs
eng.SPECS = json.loads((FN / "specs.json").read_text())

BOOK = [("EURUSD", 0.01), ("XAUUSD", 100.0), ("BTCUSD", 1000.0)]
SINCE = "2026-01-01"

# FundedNext Stellar 2-step
TARGET_P1, DAILY_LOSS, MAX_DD, MIN_DAYS, LIMIT_DAYS, CONSISTENCY = 0.10, 0.05, 0.10, 5, 45, 0.40


def load_fn(sym: str) -> dict:
    z = np.load(FN / f"{sym}.npz")
    return {"time": z["time"].astype(np.int64), "open": z["open"].astype(float),
            "high": z["high"].astype(float), "low": z["low"].astype(float),
            "close": z["close"].astype(float), "spread_pts": z["spread"].astype(float),
            "point": float(z["point"])}


def book_trades(offset: float = 0.0, since: str = SINCE) -> list[dict]:
    t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    out = []
    for sym, step in BOOK:
        b = load_fn(sym)
        m = b["time"] >= t0
        b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        for tr in sequential_trades(b, step, offset, approach=12, horizon=48,
                                    stop_frac=0.25, slip_points=1.0):
            out.append({"t": tr["t"], "symbol": sym, "r": tr["r"], "stop_distance": 0.25 * step})
    return out


def challenge(trades: list[dict], balance: float, risk_pct: float) -> dict:
    """Walk the real trade sequence through the evaluation rules."""
    trades = sorted(trades, key=lambda x: x["t"])
    bal = balance
    floor = balance * (1 - MAX_DD)
    day_start = {}
    day_profit: dict[str, float] = {}
    days_traded = set()
    start_day = None
    blocked_day = None

    for tr in trades:
        d = datetime.fromtimestamp(tr["t"], timezone.utc).date()
        if start_day is None:
            start_day = d
        if (d - start_day).days > LIMIT_DAYS:
            return {"result": "timeout", "balance": bal, "days": len(days_traded),
                    "elapsed": (d - start_day).days}
        key = d.isoformat()
        if key not in day_start:
            day_start[key] = bal
            blocked_day = None
        if blocked_day == key:
            continue

        lots, risked = eng.size_lots(tr["symbol"], bal * risk_pct / 100.0, tr["stop_distance"])
        if lots <= 0 or risked > bal * risk_pct / 100.0 * 3:
            continue
        bal += tr["r"] * risked
        days_traded.add(key)
        day_profit[key] = day_profit.get(key, 0.0) + tr["r"] * risked

        if bal <= floor:
            return {"result": "dd_breach", "balance": bal, "days": len(days_traded),
                    "elapsed": (d - start_day).days}
        if bal <= day_start[key] - balance * DAILY_LOSS:
            blocked_day = key
        if bal - balance >= balance * TARGET_P1 and len(days_traded) >= MIN_DAYS:
            total = bal - balance
            best = max(day_profit.values())
            if total > 0 and best / total <= CONSISTENCY:
                return {"result": "pass", "balance": bal, "days": len(days_traded),
                        "elapsed": (d - start_day).days}
    return {"result": "no_target", "balance": bal, "days": len(days_traded),
            "elapsed": (d - start_day).days if start_day else 0}


def main() -> None:
    print("FUNDEDNEXT DATA — round-number continuation book, 2026-01-01 -> today")
    print(f"  markets: {', '.join(f'{s} @ {st:g}' for s, st in BOOK)}")

    real = book_trades(0.0)
    ctrl = book_trades(CONTROL_OFFSETS[0])

    for label, trades in (("FundedNext feed, ROUND levels", real),
                          ("FundedNext feed, CONTROL levels", ctrl)):
        res = eng.run_account(trades, start_balance=10_000.0, risk_pct=0.5)
        rep = eng.report(res, label)
        eng.print_report(rep)
        if label.startswith("FundedNext feed, ROUND"):
            print(eng.month_table(rep))

    print("\nEVALUATION — would this sequence pass? "
          f"(target {TARGET_P1:.0%}, daily {DAILY_LOSS:.0%}, static DD {MAX_DD:.0%}, "
          f"{LIMIT_DAYS} days, consistency {CONSISTENCY:.0%})")
    print(f"  {'risk':>6s} {'result':>12s} {'end balance':>13s} {'days traded':>12s} {'elapsed':>8s}")
    out = {}
    for risk in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
        r = challenge(real, 10_000.0, risk)
        print(f"  {risk:>5.2f}% {r['result']:>12s} {r['balance']:>13,.2f} "
              f"{r['days']:>12d} {r['elapsed']:>8d}")
        out[risk] = r

    (ROOT / "data" / "zone_study" / "fundednext_test.json").write_text(
        json.dumps(out, indent=1, default=float))
    print("\n-> data/zone_study/fundednext_test.json")


if __name__ == "__main__":
    main()
