"""
scripts/copytrade_flip.py

"Flip" study: can a small pot ($100) be grown to a target ($1,000) by
trading a channel's signals aggressively, and how often does it bust first?

Each run walks the channel's signals in time order with one pot. A signal
is taken only when no flip trade is open. When the pot reaches the target it
is banked and a new $100 pot starts; when it can no longer open the minimum
lot within its stop, it is bust and a new $100 pot starts. Gold is traded
in 0.01-lot steps (0.01 lot = $1 per $1 move), the way a broker sizes it.

Ways of trading each signal (all on the signal's own stop and targets):

  copier    the AlgoEdge copier as configured (layered zone legs, his
            follow-ups, TP ladder trail), risking a fraction of the pot.
  pyramid   one entry at the zone; every time price reaches the next target
            another position is stacked on, sized from the floating profit
            so that if price falls back to the trailed stop (one target
            back) the whole stack closes at >= 0 (or at most `give` of the
            open profit); everything closes at target `close_at`.
  runner    one entry, stop trailed one target behind (TP1 -> breakeven,
            TP2 -> TP1 ...), no fixed target: rides until trailed out,
            capped at `rr` x the initial risk.

    python scripts/copytrade_flip.py CHANNEL.tsv [MORE.tsv ...] --bars /tmp/ct_XAUUSD_M1_0.npz
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import copytrade_backtest as bt  # noqa: E402

from backend.copytrade.rules import merge_rules  # noqa: E402

LOT_STEP = 0.01
PER_LOT = 100.0          # $ per $1 move per 1.00 lot of gold


def lots(x: float) -> float:
    return max(0.0, np.floor(x / LOT_STEP + 1e-9) * LOT_STEP)


def first_fill(sig, bars, expiry_h=24.0, max_wait_h=24.0):
    """Index and price of the first entry: the zone's near edge (or market if
    price is already in/through the zone when the message lands)."""
    T = bars["time"]
    p = sig["p"]
    s = 1 if p["side"] == "BUY" else -1
    i0 = int(np.searchsorted(T, sig["t"] + 1))
    if i0 >= len(T) or T[i0] - sig["t"] > 6 * 3600:
        return None
    hi, lo = p.get("entry_high"), p.get("entry_low")
    near = (hi if s == 1 else lo) if hi and lo else p.get("entry")
    sl = p.get("sl")
    tps = [float(t) for t in (p.get("tps") or [])]
    if sl is None or not tps:
        return None
    sp = float(bars["spread"][i0])
    mkt = float(bars["open"][i0]) + (sp if s == 1 else 0.0)
    if near is None or (mkt - near) * s <= 0:
        if (mkt - sl) * s <= 0 or (tps[0] - mkt) * s <= 0:
            return None
        return i0, mkt
    end = int(np.searchsorted(T, sig["t"] + max_wait_h * 3600))
    for i in range(i0, min(end, len(T))):
        h, l_, sp = float(bars["high"][i]), float(bars["low"][i]), float(bars["spread"][i])
        if (s == 1 and l_ + sp <= near) or (s == -1 and h >= near):
            return i, float(near)
        if (s == 1 and h >= tps[0]) or (s == -1 and l_ + sp <= tps[0]):
            return None          # ran to TP1 without us: the copier cancels after TP1
    return None


def run_path(sig, bars, i_fill, entry, vol0, mode, close_at=4, give=0.0, rr=10.0, max_hold_h=48.0,
             add_mult=1.0):
    """P/L in $ of one signal traded as `mode` ("pyramid" or "runner")."""
    T = bars["time"]
    p = sig["p"]
    s = 1 if p["side"] == "BUY" else -1
    tps = [float(t) for t in p["tps"]]
    sl0 = float(p["sl"])
    risk_px = abs(entry - sl0)
    pos = [(entry, vol0)]
    stop = sl0
    k = 0                                    # targets reached
    end = int(np.searchsorted(T, T[i_fill] + max_hold_h * 3600))
    cap = entry + s * rr * risk_px
    i = i_fill
    for i in range(i_fill, min(end, len(T))):
        o, h, l_, c, sp = (float(bars[x][i]) for x in ("open", "high", "low", "close", "spread"))
        # exits on the bid for a buy / ask for a sell
        worst = l_ if s == 1 else h + sp
        best = h if s == 1 else l_ + sp
        if (worst - stop) * s <= 0 and i > i_fill:
            return sum(v * (stop - e) * s for e, v in pos) * PER_LOT, T[i]
        while i > i_fill and k < len(tps) and (best - tps[k]) * s >= 0:   # not in the fill bar: order unknown
            lvl = tps[k]
            k += 1
            if mode == "pyramid" and k >= close_at:
                return sum(v * (lvl - e) * s for e, v in pos) * PER_LOT, T[i]
            new_stop = entry if k == 1 else tps[k - 2]
            if (new_stop - stop) * s > 0:
                stop = new_stop
            if mode == "pyramid":
                # stack: P/L at the stop must stay >= -give x the open profit at this level
                at_stop = sum(v * (stop - e) * s for e, v in pos)
                open_pl = sum(v * (lvl - e) * s for e, v in pos)
                room = at_stop + give * max(open_pl, 0.0)
                dist = abs(lvl - stop)
                if dist > 0 and room > 0:
                    add = lots(min(room / dist, add_mult * sum(v for _, v in pos)))
                    if add >= LOT_STEP:
                        pos.append((lvl + s * 0.2, add))     # a little slippage on the stacked fill
        if mode == "runner" and i > i_fill and (best - cap) * s >= 0:
            return sum(v * (cap - e) * s for e, v in pos) * PER_LOT, T[i]
    last = float(bars["close"][min(i, len(T) - 1)])
    return sum(v * (last - e) * s for e, v in pos) * PER_LOT, T[min(i, len(T) - 1)]


def trade(sig, fups, bars, pot, frac, how):
    """$ result of one signal for a pot of `pot`, risking `frac` of it, traded `how`."""
    name, kw = how
    if name == "copier":
        rules = merge_rules({"risk_pct": frac * 100, "allow_min_lot": True, "min_lot_max_risk_pct": 100.0,
                             "trail_ladder": kw.get("ladder", True), "on_first_fill": "keep",
                             "cancel_pending_after_tp": 1})
        r = bt.simulate(sig, fups, bars, rules, follow=True, balance=pot, ladder=kw.get("ladder", True),
                        first_fill=kw.get("first_fill", "keep"))
        if r["status"] != "FILLED":
            return None, None
        return r["pnl"], r["end_t"]
    f = first_fill(sig, bars)
    if f is None:
        return None, None
    i, entry = f
    risk_px = abs(entry - float(sig["p"]["sl"]))
    vol = lots(pot * frac / (risk_px * PER_LOT))
    if vol < LOT_STEP:
        if risk_px * PER_LOT * LOT_STEP <= pot:      # the min lot still fits inside the whole pot
            vol = LOT_STEP
        else:
            return "BUST", None
    return run_path(sig, bars, i, entry, vol, name, **kw)


def flip(sigs, by_sig, bars, frac, how, start=100.0, target=1000.0):
    pot, t_start, i_start = start, None, 0
    done, busts, hold_days, trades_to_win = [], 0, [], []
    n_tr = 0
    last_end = 0
    peak = start
    for sg in sigs:
        if sg["t"] < last_end:
            continue                         # one flip trade at a time
        pnl, t_end = trade(sg, by_sig.get(sg["id"], []), bars, pot, frac, how)
        if pnl is None:
            continue
        if t_start is None:
            t_start = sg["t"]
        if pnl == "BUST":
            busts += 1
            pot, t_start, n_tr = start, None, 0
            continue
        n_tr += 1
        pot += pnl
        peak = max(peak, pot)
        last_end = int(t_end)
        if pot >= target:
            done.append(pot)
            hold_days.append((sg["t"] - t_start) / 86400)
            trades_to_win.append(n_tr)
            pot, t_start, n_tr = start, None, 0
        elif pot < start * 0.2:              # $20 left: 0.01 lot on a $8 stop is 40 % of it - call it bust
            busts += 1
            pot, t_start, n_tr = start, None, 0
    return {"wins": len(done), "busts": busts, "open_pot": pot, "peak": peak,
            "days": float(np.median(hold_days)) if hold_days else None,
            "trades": float(np.median(trades_to_win)) if trades_to_win else None,
            "net": sum(done) + pot - start * (len(done) + busts + 1)}


HOWS = [
    ("copier", {"ladder": True}),
    ("copier", {"ladder": True, "first_fill": "market_rest"}),
    ("copier", {"ladder": True, "first_fill": "market_add"}),
    ("pyramid", {"close_at": 3}),
    ("pyramid", {"close_at": 4}),
    ("pyramid", {"close_at": 5}),
    ("pyramid", {"close_at": 6, "give": 0.3}),
    ("pyramid", {"close_at": 4, "give": 1.0, "add_mult": 2.0}),
    ("runner", {"rr": 5}),
    ("runner", {"rr": 10}),
    ("runner", {"rr": 20}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("channels", nargs="+")
    ap.add_argument("--bars", required=True)
    ap.add_argument("--fracs", default="0.1,0.25,0.5")
    ap.add_argument("--start", type=float, default=100.0)
    ap.add_argument("--target", type=float, default=1000.0)
    a = ap.parse_args()
    bars = bt.load_bars_npz(a.bars)
    lo, hi = int(bars["time"][0]) + 3600, int(bars["time"][-1]) - 3600
    for path in a.channels:
        sigs, by_sig = bt.load_channel(path)
        sigs = [x for x in sigs if lo <= x["t"] <= hi and (x["p"].get("symbol") or "XAUUSD") == "XAUUSD"]
        if not sigs:
            continue
        d0 = datetime.fromtimestamp(sigs[0]["t"], timezone.utc).strftime("%Y-%m-%d")
        d1 = datetime.fromtimestamp(sigs[-1]["t"], timezone.utc).strftime("%Y-%m-%d")
        print(f"\n### {Path(path).stem}: {len(sigs)} gold signals {d0} .. {d1}, ${a.start:g} -> ${a.target:g}")
        print(f"{'how':42} {'risk':>5} {'wins':>5} {'busts':>5} {'days/win':>8} {'trades/win':>10} {'peak $':>7} {'net $':>7}")
        for how in HOWS:
            label = how[0] + " " + ",".join(f"{k}={v}" for k, v in how[1].items())
            for fr in [float(x) for x in a.fracs.split(",")]:
                r = flip(sigs, by_sig, bars, fr, how, a.start, a.target)
                dd = f"{r['days']:.1f}" if r["days"] is not None else "-"
                tt = f"{r['trades']:.0f}" if r["trades"] is not None else "-"
                print(f"{label:42} {fr*100:4.0f}% {r['wins']:5d} {r['busts']:5d} {dd:>8} {tt:>10} {r['peak']:7.0f} {r['net']:7.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
