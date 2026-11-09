"""
scripts/copytrade_backtest.py

Backtests a Telegram signal channel the way AlgoEdge's copier would trade it:
each signal is planned with the SAME code the live copier uses
(copytrade.parser + copytrade.rules.build_open_plan, including layered zone
entries), then replayed on 1-minute (or 5-minute) bars together with the
provider's own follow-ups (cancel pending, breakeven, move SL, close), each
applied from the first bar after the message was posted.

    python scripts/copytrade_backtest.py data/copytrade/goldtradersunny_public.tsv \
        --bars mt5 --tf M1            # on the server, bars from the connected MT5
    python scripts/copytrade_backtest.py ... --bars data/edge_lab/bars/XAUUSD.npz

Fill rules (bid bars + the bar's spread): a buy limit fills when the ask low
reaches it; a buy exits at SL when the bid low reaches it, at TP when the bid
high does; sells mirror on the ask. A bar that touches both SL and TP counts as
the SL; a leg filled in a bar cannot also take profit in that bar. Both
pessimistic - with M1 bars they rarely matter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.copytrade.parser import parse_message  # noqa: E402
from backend.copytrade.rules import breakeven_price, build_open_plan, merge_rules  # noqa: E402

GOLD = {"point": 0.01, "tick_size": 0.01, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01,
        "volume_max": 100, "digits": 2, "contract": 100.0}


def ts(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def load_channel(path: str):
    rows = [l.rstrip("\n").split("\t") for l in open(path, encoding="utf-8") if l.strip() and not l.startswith("#")]
    sigs, fups = [], []
    for kind, mid, t, ref, text in rows:
        text = text.replace("\\n", "\n")
        if kind == "S":
            rep = ref.split(":")[0].lstrip("A") if ":" in ref else ref
            sigs.append({"id": int(mid), "t": ts(t), "reported_tp": int(rep or 0), "reported": ref, "text": text,
                         "p": parse_message(text).to_dict()})
        else:
            fups.append({"id": int(mid), "t": ts(t), "reply": int(ref), "text": text, "p": parse_message(text).to_dict()})
    # a follow-up belongs to the signal it replies to, else the latest signal before it
    by_sig = defaultdict(list)
    order = sorted(sigs, key=lambda x: x["t"])
    for f in fups:
        if f["p"]["kind"] not in ("CANCEL", "BREAKEVEN", "MOVE_SL", "MOVE_TP", "CLOSE"):
            continue
        target = f["reply"] if f["reply"] in {s["id"] for s in sigs} else None
        if target is None:
            prev = [s for s in order if s["t"] <= f["t"]]
            if prev:
                target = prev[-1]["id"]
        if target is not None:
            by_sig[target].append(f)
    return order, by_sig


def load_bars_npz(path: str):
    d = np.load(path)
    pt = float(np.atleast_1d(d["point"])[0]) if "point" in d else 0.01
    spr = d["spread"].astype(float) * pt if "spread" in d else np.full(len(d["time"]), 0.3)
    return {"time": d["time"].astype(np.int64), "open": d["open"], "high": d["high"], "low": d["low"],
            "close": d["close"], "spread": spr}


def load_bars_mt5(symbol: str, tf: str, start: int, end: int, cache: Path):
    import backend  # noqa: F401 - installs the MT5 bridge on Linux
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    mt5.symbol_select(symbol, True)
    code = getattr(mt5, f"TIMEFRAME_{tf}")
    parts, a = [], start
    while a < end:
        b = min(a + 20 * 86400, end)
        r = mt5.copy_rates_range(symbol, code, a, b)
        if r is not None and len(r):
            parts.append(r)
        a = b
    if not parts:
        raise SystemExit(f"no {symbol} {tf} bars from MT5: {mt5.last_error()}")
    r = np.concatenate(parts)
    _, idx = np.unique(r["time"], return_index=True)
    r = r[idx]
    info = mt5.symbol_info(symbol)
    pt = info.point if info else 0.01
    out = {"time": r["time"].astype(np.int64), "open": r["open"], "high": r["high"], "low": r["low"],
           "close": r["close"], "spread": r["spread"].astype(float) * pt}
    np.savez_compressed(cache, **out, point=pt)
    print(f"# {len(r)} {symbol} {tf} bars {datetime.fromtimestamp(int(r['time'][0]), timezone.utc)} .. "
          f"{datetime.fromtimestamp(int(r['time'][-1]), timezone.utc)} cached to {cache}", file=sys.stderr)
    return out


def simulate(sig, fups, bars, rules, follow=True, auto_be_tp=0, expiry_h=24.0, max_hold_h=48.0,
             balance=10_000.0, offset=0, opposite_t=None, ladder=False, first_fill="keep", slip=0.3):
    """ladder: trail the stop of every open leg as the signal's targets are reached -
    TP1 -> breakeven, TP2 -> TP1, TP3 -> TP2 ... (only ever tightens).
    first_fill: what happens to the other zone legs when the first one fills -
    "keep" (wait at their levels), "market_rest" (cancel them and take the same
    volume at market right away), "market_add" (keep them AND add the same volume
    at market: more size than the risk budget)."""
    T = bars["time"]
    t0 = sig["t"] + offset
    i0 = int(np.searchsorted(T, t0 + 1))           # first bar that opens after the message
    if i0 >= len(T) or T[i0] - t0 > 6 * 3600:
        return {"status": "NO_DATA"}
    s = 1 if sig["p"]["side"] == "BUY" else -1
    spr0 = bars["spread"][i0]
    bid0 = float(bars["open"][i0])
    plan = build_open_plan(sig["p"], {"bid": bid0, "ask": bid0 + spr0}, GOLD, balance, rules, "XAUUSD")
    if not plan.ok:
        return {"status": "SKIPPED", "reason": plan.reason}
    legs = []
    for L in plan.legs:
        legs.append({"leg": L["leg"], "vol": L["volume"], "entry": L["entry"], "tp": L["tp"], "sl": plan.sl,
                     "state": "OPEN" if L["kind"] == "MARKET" else "PENDING", "fill_i": i0 if L["kind"] == "MARKET" else None,
                     "exit": None, "why": None})
    evs = [(f["t"] + offset, f) for f in (fups if follow else [])]
    if opposite_t is not None:
        evs.append((opposite_t + offset, {"p": {"kind": "CLOSE", "close_fraction": 1.0}}))
    events = sorted(evs, key=lambda x: x[0])
    ei = 0
    end_i = int(np.searchsorted(T, t0 + max_hold_h * 3600))
    budget = balance * float(rules.get("risk_pct") or 1.0) / 100.0
    tp_filled = set()
    sig_tps = [float(t) for t in (sig["p"].get("tps") or [])]
    reached = 0                 # highest signal TP price has reached since the first fill
    first_done = False
    for i in range(i0, min(end_i, len(T))):
        o, h, l, c, sp = (float(bars[k][i]) for k in ("open", "high", "low", "close", "spread"))
        # follow-ups posted before this bar opened
        while ei < len(events) and events[ei][0] < T[i]:
            p = events[ei][1]["p"]
            k = p["kind"]
            for g in legs:
                if k == "CANCEL" and g["state"] == "PENDING":
                    g["state"], g["why"] = "CANCELLED", "cancel msg"
                elif k == "BREAKEVEN" and g["state"] == "OPEN":
                    g["sl"] = breakeven_price(g["entry"], sig["p"]["side"], rules)
                elif k == "BREAKEVEN" and g["state"] == "PENDING" and rules.get("cancel_pending_on_be", True):
                    g["state"], g["why"] = "CANCELLED", "BE msg"
                elif k == "MOVE_SL" and p.get("new_price") and g["state"] in ("OPEN", "PENDING"):
                    new = float(p["new_price"])
                    ref = sig["p"].get("entry_high") or new
                    wrong_side = g["state"] == "OPEN" and ((s == 1 and new >= o) or (s == -1 and new <= o + sp))
                    if abs(new - ref) / ref * 100 <= float(rules.get("max_sl_move_pct") or 3.0) and not wrong_side:
                        g["sl"] = new
                elif k == "CLOSE" and g["state"] == "PENDING" and (p.get("close_fraction") or 1) >= 0.999:
                    g["state"], g["why"] = "CANCELLED", "close msg"
            if k == "CLOSE":
                frac = float(p.get("close_fraction") or 1.0)
                openl = sorted([g for g in legs if g["state"] == "OPEN"], key=lambda g: g["leg"])
                want = sum(g["vol"] for g in openl) * frac
                px = o if s == 1 else o + sp
                for g in openl:
                    if want <= 1e-9:
                        break
                    take = min(g["vol"], want)
                    if take >= g["vol"] - 1e-9:
                        g["state"], g["exit"], g["why"] = "CLOSED", px, "close msg"
                    else:   # split the leg: the closed part is booked now, the rest keeps running
                        legs.append({**g, "vol": take, "state": "CLOSED", "exit": px, "why": "close msg", "leg": g["leg"]})
                        g["vol"] = round(g["vol"] - take, 8)
                    want -= take
                if p.get("also_breakeven"):
                    for g in legs:
                        if g["state"] == "OPEN":
                            g["sl"] = breakeven_price(g["entry"], sig["p"]["side"], rules)
                        elif g["state"] == "PENDING" and rules.get("cancel_pending_on_be", True):
                            g["state"], g["why"] = "CANCELLED", "BE msg"
            ei += 1
        if expiry_h and T[i] - t0 > expiry_h * 3600:
            for g in legs:
                if g["state"] == "PENDING":
                    g["state"], g["why"] = "CANCELLED", "expired"
        ask_l, ask_h = l + sp, h + sp
        for g in legs:
            if g["state"] == "PENDING":
                if (s == 1 and ask_l <= g["entry"]) or (s == -1 and h >= g["entry"]):
                    g["state"], g["fill_i"] = "OPEN", i
                    if s == 1 and o + sp < g["entry"]:
                        g["entry"] = o + sp          # gapped through: filled at the open, better
                    if s == -1 and o > g["entry"]:
                        g["entry"] = o
            if g["state"] != "OPEN":
                continue
            if first_fill != "keep" and not first_done:
                first_done = True
                px = g["entry"] + s * slip
                for o2 in [x for x in legs if x["state"] == "PENDING"]:
                    if first_fill == "market_rest":
                        o2["state"], o2["fill_i"], o2["entry"] = "OPEN", i, px
                    else:
                        legs.append({**o2, "state": "OPEN", "fill_i": i, "entry": px, "leg": o2["leg"]})
            hit_sl = (l <= g["sl"]) if s == 1 else (ask_h >= g["sl"])
            hit_tp = g["tp"] is not None and g["fill_i"] != i and ((h >= g["tp"]) if s == 1 else (ask_l <= g["tp"]))
            if hit_sl:
                g["state"], g["exit"] = "CLOSED", g["sl"]
                g["why"] = "BE" if abs(g["sl"] - g["entry"]) < 0.05 or (g["sl"] - g["entry"]) * s > 0 else "SL"
            elif hit_tp:
                g["state"], g["exit"], g["why"] = "CLOSED", g["tp"], "TP"
                tp_filled.add(g["leg"])
        ca = int(rules.get("cancel_pending_after_tp") or 0)
        if ca and any(lg >= ca for lg in tp_filled):
            for g in legs:
                if g["state"] == "PENDING":
                    g["state"], g["why"] = "CANCELLED", "TP filled"
        if ladder and any(g["fill_i"] is not None for g in legs):
            best = h if s == 1 else ask_l
            while reached < len(sig_tps) and (best - sig_tps[reached]) * s >= 0:
                reached += 1
            k = max([reached] + list(tp_filled))
            if k >= 1:
                for g in legs:
                    if g["state"] != "OPEN":
                        continue
                    tgt = g["entry"] if k == 1 or k - 2 >= len(sig_tps) else sig_tps[k - 2]
                    if k >= 2 and (tgt - g["entry"]) * s < 0:
                        tgt = g["entry"]            # a leg filled after TP1 (rare): never below its entry
                    if (tgt - g["sl"]) * s > 0:
                        g["sl"] = tgt
        if auto_be_tp and any(lg >= auto_be_tp for lg in tp_filled):
            for g in legs:
                if g["state"] == "OPEN" and g["leg"] > max(tp_filled):
                    g["sl"] = g["entry"] if (g["entry"] - g["sl"]) * s > 0 else g["sl"]
        if all(g["state"] in ("CLOSED", "CANCELLED") for g in legs):
            break
    last = float(bars["close"][min(i, len(T) - 1)])
    for g in legs:
        if g["state"] == "OPEN":
            g["state"], g["exit"], g["why"] = "CLOSED", last, "timeout"
        if g["state"] == "PENDING":
            g["state"], g["why"] = "CANCELLED", "timeout"
    pnl = 0.0
    for g in legs:
        if g["exit"] is not None:
            g["pnl"] = (g["exit"] - g["entry"]) * s * g["vol"] * GOLD["contract"]
            pnl += g["pnl"]
    filled = [g for g in legs if g["exit"] is not None]
    return {"status": "FILLED" if filled else "NO_FILL", "pnl": pnl, "R": pnl / budget, "legs": legs,
            "tps": sorted(g["leg"] for g in legs if g["why"] == "TP"), "risk": plan.risk_dollars,
            "order_type": plan.order_type}


def report(name, sigs, results):
    res = [r for r in results if r["status"] not in ("NO_DATA",)]
    filled = [r for r in res if r["status"] == "FILLED"]
    R = [r["R"] for r in filled]
    eq = np.cumsum(R) if R else np.array([0.0])
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    wins = sum(1 for x in R if x > 0.02)
    losses = sum(1 for x in R if x < -0.02)
    legs = Counter(g["why"] for r in filled for g in r["legs"] if g["exit"] is not None)
    tpmax = Counter(max(r["tps"]) if r["tps"] else 0 for r in filled)
    pf = (sum(x for x in R if x > 0) / -sum(x for x in R if x < 0)) if any(x < 0 for x in R) else float("inf")
    print(f"\n== {name}")
    print(f"signals {len(res)}  filled {len(filled)}  no-fill {sum(r['status']=='NO_FILL' for r in res)}  "
          f"skipped {sum(r['status']=='SKIPPED' for r in res)}  no-data {len(results)-len(res)}")
    if filled:
        print(f"total {sum(R):+.2f}R  (${sum(r['pnl'] for r in filled):+,.0f} at 1% of $10k per signal)  "
              f"avg {np.mean(R):+.2f}R  PF {pf:.2f}  win {wins} / loss {losses} / flat {len(R)-wins-losses}  maxDD {dd:.2f}R")
        print(f"legs closed by: {dict(legs)}")
        print(f"furthest TP filled per signal: {dict(sorted(tpmax.items()))}")
        months = defaultdict(float)
        for sg, r in zip(sigs, results):
            if r["status"] == "FILLED":
                months[datetime.fromtimestamp(sg['t'], timezone.utc).strftime('%Y-%m')] += r["R"]
        print("by month (R): " + "  ".join(f"{m} {v:+.1f}" for m, v in sorted(months.items())))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("channel")
    ap.add_argument("--bars", default="mt5", help="mt5, or a .npz file")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--tf", default="M1")
    ap.add_argument("--offset", type=int, default=0, help="seconds added to message times to match bar times")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD: only signals from this date")
    a = ap.parse_args()

    sigs, by_sig = load_channel(a.channel)
    if a.bars == "mt5":
        bars = load_bars_mt5(a.symbol, a.tf, sigs[0]["t"] - 3600, sigs[-1]["t"] + 3 * 86400,
                             Path("/tmp") / f"ct_{a.symbol}_{a.tf}_{os.getuid()}.npz")
    else:
        bars = load_bars_npz(a.bars)
    # only signals the bars cover (MT5 serves a limited history, e.g. ~100k M1 bars)
    lo = int(bars["time"][0]) + 3600
    hi = int(bars["time"][-1]) - 3600
    if a.since:
        lo = max(lo, ts(a.since + " 00:00:00"))
    n0 = len(sigs)
    sigs = [s for s in sigs if lo <= s["t"] <= hi]
    print(f"# {len(sigs)}/{n0} signals inside the bars ({datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d} .. "
          f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d})")
    if not sigs:
        raise SystemExit("no signals inside the bar range")

    # alignment check: for signals he reported a TP on, the zone should be touched soon after
    for off in sorted({a.offset, 0, 3600, -3600, 7200, -7200, 10800}):
        ok = n = 0
        for sg in sigs:
            if sg["reported_tp"] < 1:
                continue
            p = sg["p"]
            near = p["entry_high"] if p["side"] == "BUY" else p["entry_low"]
            i0 = np.searchsorted(bars["time"], sg["t"] + off)
            i1 = np.searchsorted(bars["time"], sg["t"] + off + 3600)
            if i1 <= i0:
                continue
            n += 1
            seg_l, seg_h = bars["low"][i0:i1].min(), bars["high"][i0:i1].max()
            ok += (seg_l <= near + 0.5) if p["side"] == "BUY" else (seg_h >= near - 0.5)
        print(f"# offset {off:+6d}s: zone reached within 1h for {ok}/{n} signals he reported a TP on", file=sys.stderr)

    # for "buy/sell now <price>" signals: how far the market was from the quoted price when he posted
    for off in (-900, -600, -300, -120, 0, 60, 120, 300):
        d, sd = [], []
        for sg in sigs:
            p = sg["p"]
            first = p["entry_high"] if p["side"] == "BUY" else p["entry_low"]
            i = np.searchsorted(bars["time"], sg["t"] + off)
            if 0 < i < len(bars["time"]) and abs(bars["time"][i] - sg["t"] - off) < 600:
                d.append(abs(float(bars["open"][i]) - float(first)))
                sd.append((float(bars["open"][i]) - float(first)) * (1 if p["side"] == "BUY" else -1))
        if d:
            print(f"# offset {off:+6d}s: market vs quoted entry: median {np.median(d):.2f}, within $1 for "
                  f"{sum(x <= 1 for x in d)}/{len(d)}; already moved toward TP by {np.median(sd):+.2f} "
                  f"(>= TP1 distance for {sum(1 for sg, x in zip(sigs, sd) if x >= 2)}/{len(sd)})", file=sys.stderr)

    base = merge_rules({"risk_pct": 1.0})
    opp = {}
    for i, sg in enumerate(sigs):
        nxt = next((x for x in sigs[i + 1:] if x["p"]["side"] != sg["p"]["side"]), None)
        opp[sg["id"]] = nxt["t"] if nxt else None
    runs = [
        ("AlgoEdge copier, follows his messages (cancel / BE / move SL), default rules", dict(follow=True)),
        ("Signals only, no follow-ups, pending expire after 1h", dict(follow=False, expiry_h=1)),
        ("Signals only, auto-BE after TP1, pending expire after 1h", dict(follow=False, auto_be_tp=1, expiry_h=1)),
        ("Follows his messages + auto-BE after TP1", dict(follow=True, auto_be_tp=1)),
        ("Follows his messages + closes on an opposite signal", dict(follow=True, use_opp=True)),
        ("Follows + TP ladder trail (TP1->BE, TP2->TP1 ...)", dict(follow=True, ladder=True)),
        ("Follows + first fill: rest of the zone at market", dict(follow=True, first_fill="market_rest")),
        ("Follows + ladder + first fill: rest at market", dict(follow=True, ladder=True, first_fill="market_rest")),
        ("Follows + ladder + first fill: ADD market legs (extra size)", dict(follow=True, ladder=True, first_fill="market_add")),
    ]
    out = {}
    for name, kw in runs:
        use_opp = kw.pop("use_opp", False)
        results = [simulate(sg, by_sig.get(sg["id"], []), bars, base, offset=a.offset,
                            opposite_t=opp[sg["id"]] if use_opp else None, **kw) for sg in sigs]
        kw["use_opp"] = use_opp
        report(name, sigs, results)
        out[name] = results
        if a.detail and kw.get("follow") and not kw.get("auto_be_tp"):
            print("\n  id      date              side  plan    status   R      TPs      he reported")
            for sg, r in zip(sigs, results):
                d = datetime.fromtimestamp(sg["t"], timezone.utc).strftime("%m-%d %H:%M")
                print(f"  {sg['id']}  {d}  {sg['p']['side']:4}  {r.get('order_type',''):6}  {r['status']:8} "
                      f"{r.get('R', 0):+5.2f}  {','.join(map(str, sorted(set(r.get('tps', []))))) or '-':8} {sg.get('reported', 'TP' + str(sg['reported_tp']))}"
                      f"  {r.get('reason','')}")
    if a.json:
        Path(a.json).write_text(json.dumps({k: [{kk: vv for kk, vv in r.items() if kk != 'legs'} for r in v]
                                            for k, v in out.items()}, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
