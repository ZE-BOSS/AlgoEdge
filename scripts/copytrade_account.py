"""
scripts/copytrade_account.py

A "flip account" traded like a normal account: start with $1,000, risk a fixed
% of the current balance on every signal, never reset. Shows how fast each way
of trading the same signals grows it, and how deep it falls on the way.

Ways of trading a signal (all on the provider's stop and posted targets, one
entry at the zone's near edge, or at market if price is already there):

  copier       the AlgoEdge copier: zone legs to TP1/TP2/TP3, his follow-ups,
               TP ladder trail (what the live copier does with the ladder on)
  copier+add   the same, plus on the first fill the other legs are also taken at
               market (limits stay until TP1) - more size on fast moves
  ladder       ONE position, no fixed target: TP1 -> breakeven, TP2 -> TP1,
               TP3 -> TP2 ... until price comes back to the trailed stop
               ("TP open" continues the ladder at the last spacing)
  ladder+tp1   ladder, but half closes at TP1 (1:x locked) and half runs
  ladder+stack ladder, plus a second position of the same size added at TP1
               (its stop at the first entry: risks only TP1's distance)

    python scripts/copytrade_account.py A.tsv [B.tsv ...] --bars /tmp/ct_XAUUSD_M1_0.npz --risk 1,2,5,10
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
from copytrade_flip import first_fill  # noqa: E402

from backend.copytrade.rules import merge_rules  # noqa: E402

PER_LOT = 100.0


def ladder_targets(p) -> list[float]:
    tps = [float(t) for t in (p.get("tps") or [])]
    if p.get("tp_open") and len(tps) >= 2:
        step = tps[-1] - tps[-2]
        tps += [tps[-1] + step * (j + 1) for j in range(30)]
    return tps


def path_trade(sig, bars, i_fill, entry, mode, max_hold_h=72.0):
    """(R, exit time) of one signal traded `mode`; R is P/L over the first position's risk."""
    T = bars["time"]
    p = sig["p"]
    s = 1 if p["side"] == "BUY" else -1
    sl0 = float(p["sl"])
    tps = ladder_targets(p)
    risk = abs(entry - sl0)
    if risk <= 0 or not tps:
        return None, None
    # positions: [entry, volume, fixed target or None]
    pos = [[entry, 1.0, None]]
    if mode == "ladder+tp1":
        pos = [[entry, 0.5, tps[0]], [entry, 0.5, None]]
    stop = sl0
    k = 0
    realized = 0.0
    end = int(np.searchsorted(T, T[i_fill] + max_hold_h * 3600))
    i = i_fill
    for i in range(i_fill, min(end, len(T))):
        h, l_, sp = float(bars["high"][i]), float(bars["low"][i]), float(bars["spread"][i])
        worst = l_ if s == 1 else h + sp
        best = h if s == 1 else l_ + sp
        if i > i_fill and (worst - stop) * s <= 0:
            realized += sum(v * (stop - e) * s for e, v, _ in pos)
            return realized / risk, T[i]
        if i == i_fill:
            continue
        for q in [q for q in pos if q[2] is not None and (best - q[2]) * s >= 0]:
            realized += q[1] * (q[2] - q[0]) * s
            pos.remove(q)
        while k < len(tps) and (best - tps[k]) * s >= 0:
            k += 1
            new = entry if k == 1 else tps[k - 2]
            if (new - stop) * s > 0:
                stop = new
            if mode == "ladder+stack" and k == 1:
                pos.append([tps[0] + s * 0.2, 1.0, None])
        if not pos:
            return realized / risk, T[i]
    last = float(bars["close"][min(i, len(T) - 1)])
    realized += sum(v * (last - e) * s for e, v, _ in pos)
    return realized / risk, T[min(i, len(T) - 1)]


def trades_for(path, bars, mode):
    """[(entry time, exit time, R, stop distance $)] for a channel's gold signals."""
    sigs, by_sig = bt.load_channel(path)
    lo, hi = int(bars["time"][0]) + 3600, int(bars["time"][-1]) - 3600
    sigs = [x for x in sigs if lo <= x["t"] <= hi and (x["p"].get("symbol") or "XAUUSD") == "XAUUSD"]
    out = []
    for sg in sigs:
        if mode.startswith("copier"):
            rules = merge_rules({"risk_pct": 1.0, "trail_ladder": True, "cancel_pending_after_tp": 1})
            r = bt.simulate(sg, by_sig.get(sg["id"], []), bars, rules, follow=True, ladder=True,
                            first_fill="market_add" if mode == "copier+add" else "keep")
            if r["status"] != "FILLED":
                continue
            out.append((sg["t"], r["end_t"], r["R"], None))
        else:
            f = first_fill(sg, bars, by_sig.get(sg["id"], []))
            if f is None:
                continue
            i, entry = f
            R, t_end = path_trade(sg, bars, i, entry, mode)
            if R is None:
                continue
            out.append((int(bars["time"][i]), int(t_end), R, abs(entry - float(sg["p"]["sl"]))))
    return out


def account(trades, risk_pct, start=1000.0):
    """Balance path when every trade risks risk_pct of the balance at its entry
    (0.01-lot steps for gold: the risk is rounded down to what the lot step allows)."""
    events = sorted(trades, key=lambda x: x[0])
    bal, peak, maxdd = start, start, 0.0
    open_: list[tuple[int, float]] = []       # (exit time, P/L $)
    hit = {2: None, 5: None, 10: None}
    t0 = events[0][0] if events else 0
    for t_in, t_out, R, dist in events:
        for x in sorted([x for x in open_ if x[0] <= t_in]):
            bal += x[1]
            open_.remove(x)
            peak = max(peak, bal)
            maxdd = max(maxdd, (peak - bal) / peak)
            for m in hit:
                if hit[m] is None and bal >= start * m:
                    hit[m] = (x[0] - t0) / 86400
        if bal <= start * 0.05:
            break
        want = bal * risk_pct / 100.0
        if dist:                                        # whole 0.01 lots
            lots = np.floor(want / (dist * PER_LOT) / 0.01 + 1e-9) * 0.01
            lots = max(lots, 0.01)
            want = lots * dist * PER_LOT
        open_.append((t_out, R * want))
    for x in sorted(open_):
        bal += x[1]
        peak = max(peak, bal)
        maxdd = max(maxdd, (peak - bal) / peak)
        for m in hit:
            if hit[m] is None and bal >= start * m:
                hit[m] = (x[0] - t0) / 86400
    return bal, maxdd, hit


MODES = ["copier", "copier+add", "ladder", "ladder+tp1", "ladder+stack"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("channels", nargs="+")
    ap.add_argument("--bars", required=True)
    ap.add_argument("--risk", default="1,2,5,10")
    ap.add_argument("--start", type=float, default=1000.0)
    ap.add_argument("--combine", action="store_true", help="also all channels together in one account")
    a = ap.parse_args()
    bars = bt.load_bars_npz(a.bars)
    risks = [float(x) for x in a.risk.split(",")]
    sets = [[c] for c in a.channels] + ([a.channels] if a.combine and len(a.channels) > 1 else [])
    cache: dict = {}
    for chs in sets:
        name = " + ".join(Path(c).stem for c in chs)
        print(f"\n### {name}  (${a.start:,.0f} start)")
        print(f"{'how':13} {'trades':>6} {'sum R':>7} {'win%':>5} {'avg win R':>9} | " +
              " | ".join(f"{r:g}% risk: end $ / maxDD / days to 2x,5x,10x" for r in risks[:1]))
        for mode in MODES:
            tr = []
            for c in chs:
                if (c, mode) not in cache:
                    cache[(c, mode)] = trades_for(c, bars, mode)
                tr += cache[(c, mode)]
            if not tr:
                continue
            Rs = np.array([x[2] for x in tr])
            wins = Rs[Rs > 0.05]
            line = f"{mode:13} {len(tr):6d} {Rs.sum():7.1f} {100 * len(wins) / len(Rs):4.0f}% {wins.mean() if len(wins) else 0:9.2f}"
            for r in risks:
                bal, dd, hit = account(tr, r, a.start)
                hs = ",".join("-" if hit[m] is None else f"{hit[m]:.0f}" for m in (2, 5, 10))
                line += f" | {r:g}%: ${max(bal, 0):,.0f} dd{min(dd, 1) * 100:.0f}% d{hs}"
            print(line)
        t0 = min(min(x[0] for x in cache[(c, 'ladder')]) for c in chs if cache.get((c, 'ladder')))
        t1 = max(max(x[1] for x in cache[(c, 'ladder')]) for c in chs if cache.get((c, 'ladder')))
        print(f"   period {datetime.fromtimestamp(t0, timezone.utc):%Y-%m-%d} .. "
              f"{datetime.fromtimestamp(t1, timezone.utc):%Y-%m-%d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
