"""Analyse deep_runs output: per-pair monthly consistency, best assets per
strategy, Sigma variants, and a low-correlation portfolio backtested together.

    python3 analyze_deep.py /opt/algoedge/research_out/deep > report.md

Portfolio: every trade keeps the P&L it made in its own run (1% of a static
$10,000 per trade, so $100 per R; RQ_TMeanReversion is sized at 1x notional
like its Pine script). Trades are added on one $10,000 account in exit order.
"""
from __future__ import annotations

import glob
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone

D = sys.argv[1] if len(sys.argv) > 1 else "/opt/algoedge/research_out/deep"
BAL = 10000.0
MONTHS = [f"{y}-{m:02d}" for y, m in [(2025, 11), (2025, 12)] + [(2026, k) for k in range(1, 12)]]


def load():
    out = []
    for f in glob.glob(f"{D}/*.json"):
        r = json.load(open(f))
        if r.get("error") or not r.get("rows"):
            continue
        out.append(r)
    return out


def stats(rows):
    rows = sorted(rows, key=lambda x: x["x"])
    bal = peak = BAL
    mdd = 0.0
    for x in rows:
        bal += x["pnl"]
        peak = max(peak, bal)
        mdd = max(mdd, (peak - bal) / peak)
    pnl = [x["pnl"] for x in rows]
    gw, gl = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
    mon = defaultdict(float)
    for x in rows:
        mon[datetime.fromtimestamp(x["x"], tz=timezone.utc).strftime("%Y-%m")] += x["pnl"]
    act = [m for m in MONTHS if m in mon]
    pos = sum(1 for m in act if mon[m] > 0)
    net = sum(pnl)
    return {"n": len(rows), "net": net, "dd": 100 * mdd, "pf": gw / gl if gl > 0 else float("inf"),
            "win": sum(p > 0 for p in pnl) / len(pnl) if pnl else 0, "mon": dict(mon),
            "mpos": pos, "mact": len(act), "worst_m": min((mon[m] for m in act), default=0.0),
            "best_m": max((mon[m] for m in act), default=0.0), "rdd": (net / BAL * 100) / (100 * mdd) if mdd > 0 else float("inf")}


def weekly(rows):
    w = defaultdict(float)
    for x in rows:
        w[int(x["x"] // (7 * 86400))] += x["pnl"]
    return w


def corr(a, b):
    keys = sorted(set(a) | set(b))
    if len(keys) < 8:
        return 0.0
    xa = [a.get(k, 0.0) for k in keys]
    xb = [b.get(k, 0.0) for k in keys]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    va = sum((v - ma) ** 2 for v in xa)
    vb = sum((v - mb) ** 2 for v in xb)
    if va <= 0 or vb <= 0:
        return 0.0
    return sum((p - ma) * (q - mb) for p, q in zip(xa, xb)) / math.sqrt(va * vb)


def money(v):
    return f"{'+' if v >= 0 else '−'}${abs(v):,.0f}"


def name(r):
    s = r["strategy"].replace("_v1", "").replace("_v32", " v32")
    return f"{s} · {r['symbol']}"


def main():
    runs = load()
    base = [r for r in runs if not r["tag"].startswith("SIGMA_VAR")]
    for r in runs:
        r["s"] = stats(r["rows"])
        r["h1n"] = (r.get("h1") or {}).get("net") or 0
        r["h2n"] = (r.get("h2") or {}).get("net") or 0
    print(f"# Deep runs: {len(base)} pairs re-run with every trade kept\n")
    print("A pair is **consistent** if it made money in both halves of the year, in at least 60% of the "
          "months it traded, with profit factor ≥ 1.2 and ≥ 20 trades.\n")

    def consistent(r):
        s = r["s"]
        return (r["h1n"] > 0 and r["h2n"] > 0 and s["n"] >= 20 and s["pf"] >= 1.2
                and s["mact"] >= 6 and s["mpos"] / s["mact"] >= 0.6)

    good = sorted([r for r in base if consistent(r)], key=lambda r: -r["s"]["rdd"])

    def tier_b(r):
        s = r["s"]
        return not consistent(r) and r["h1n"] > 0 and r["h2n"] > 0 and s["n"] >= 20 and s["pf"] >= 1.1

    tb = sorted([r for r in base if tier_b(r)], key=lambda r: -r["s"]["rdd"])
    print(f"**Tier A: {len(good)} consistent pairs** (of {len(base)} that made money over the year). "
          f"**Tier B: {len(tb)} more** that made money in both halves with profit factor 1.1 or better "
          f"but fewer winning months.\n")
    for r in good:
        r["tier"] = "A"
    for r in tb:
        r["tier"] = "B"
    good = sorted(good + tb, key=lambda r: -r["s"]["rdd"])

    # per strategy: best assets
    print("## Where each strategy works (Tier A and B pairs, best first)\n")
    print("| Strategy | Tier A+B pairs | Best pairs (net, max DD, months up) |\n|---|---|---|")
    by = defaultdict(list)
    for r in good:
        by[r["strategy"]].append(r)
    for strat, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        best = "; ".join(f"{r['symbol']} {money(r['s']['net'])}, {r['s']['dd']:.1f}%, {r['s']['mpos']}/{r['s']['mact']}"
                         for r in rs[:4])
        print(f"| {strat.replace('_v1', '')} | {len(rs)} | {best} |")
    print()

    print("## Top 45 pairs (Tier A and B), by return/drawdown\n")
    print("| # | Tier | Pair | Class | Trades | Win | PF | Net | Max DD | Ret/DD | Months up | Worst month | Best month |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(good[:45], 1):
        s = r["s"]
        pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        print(f"| {i} | {r['tier']} | {name(r)} | {r.get('asset_class', '')[:9]} | {s['n']} | {100 * s['win']:.0f}% | {pf} | "
              f"{money(s['net'])} | {s['dd']:.1f}% | {s['rdd']:.1f} | {s['mpos']}/{s['mact']} | "
              f"{money(s['worst_m'])} | {money(s['best_m'])} |")
    print()

    # synthetics specifically
    syn = [r for r in good if (r.get("asset_class") or "").upper().startswith("SYNTH")]
    print(f"## Synthetic indices: {len(syn)} Tier A/B pairs\n")
    print("| Tier | Pair | Trades | Net | Max DD | Months up | Worst month |\n|---|---|---|---|---|---|---|")
    for r in syn[:25]:
        s = r["s"]
        print(f"| {r['tier']} | {name(r)} | {s['n']} | {money(s['net'])} | {s['dd']:.1f}% | {s['mpos']}/{s['mact']} | {money(s['worst_m'])} |")
    print()

    # Sigma variants
    sig = [r for r in runs if r["tag"].startswith("SIGMA_VAR") or (r["strategy"] == "RQ_SigmaBreakout_v1" and r["symbol"] == "US Tech 100" and not r.get("params"))]
    if sig:
        print("## Sigma on US Tech 100: stop and take-profit variants\n")
        print("| Stop | Take-profit | Trades | Win | Net | Max DD | Ret/DD | H1 / H2 | Months up |\n|---|---|---|---|---|---|---|---|---|")

        def key(r):
            p = r.get("params") or {}
            return (p.get("sl_pct", 0), p.get("tp_r", 0))
        for r in sorted(sig, key=key):
            p = r.get("params") or {}
            s = r["s"]
            stop = f"{p['sl_pct']}%" if p.get("sl_pct") else "2% safety (default)"
            tp = f"1:{p['tp_r']:g}" if p.get("tp_r") else "none (band flip / 16:00 ET)"
            print(f"| {stop} | {tp} | {s['n']} | {100 * s['win']:.0f}% | {money(s['net'])} | {s['dd']:.1f}% | "
                  f"{s['rdd']:.1f} | {money(r['h1n'])} / {money(r['h2n'])} | {s['mpos']}/{s['mact']} |")
        print()

    # portfolio
    print("## Portfolio: low-correlation combination, backtested together\n")
    cands = [r for r in good if r["s"]["n"] >= 20]
    for r in cands:
        r["w"] = weekly(r["rows"])
    def select(pool, metric, cap):
        out = []
        for r in pool:
            if len(out) >= cap:
                break
            f = fam(r["symbol"])
            if f is not None and any(c["strategy"] == r["strategy"] and fam(c["symbol"]) == f for c in out):
                continue
            if r["strategy"] == "RQ_TMeanReversion_v1" and any(c["strategy"] == r["strategy"] for c in out):
                continue
            if sum(c["strategy"] == r["strategy"] for c in out) >= 2:
                continue
            if any(c["symbol"] == r["symbol"] for c in out):
                continue
            if any(c["strategy"] == r["strategy"] and corr(c[metric], r[metric]) > 0.3 for c in out):
                continue
            if any(corr(c[metric], r[metric]) > 0.5 for c in out):
                continue
            out.append(r)
        return out

    families = [{"US Tech 100", "Wall Street 30", "US SP 500", "US Small Cap 2000"},
                {"Germany 40", "France 40", "Europe 50", "Netherlands 25", "UK 100", "Swiss 20", "Spain 35"},
                {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"}]

    def fam(sym):
        return next((i for i, f in enumerate(families) if sym in f), None)

    chosen = select(cands, "w", int(sys.argv[2] if len(sys.argv) > 2 else 10))
    rows = sorted([dict(x, who=name(r)) for r in chosen for x in r["rows"]], key=lambda x: x["x"])
    ps = stats(rows)
    # concurrency
    ev = sorted([(x["t"], 1) for x in rows] + [(x["x"], -1) for x in rows])
    cur = mx = 0
    for _, d in ev:
        cur += d
        mx = max(mx, cur)
    print("Chosen greedily by return/drawdown: one strategy per instrument, at most two pairs per strategy "
          "(one for RQ_TMeanReversion, sized at 1x notional), no strategy twice on a co-moving family "
          "(US indices, EU indices, precious metals), no two pairs whose weekly P&L correlates above 0.5 "
          "(0.3 for the same strategy).\n")
    print("| Pair | Trades | Net (alone) | Max DD (alone) |\n|---|---|---|---|")
    for r in chosen:
        print(f"| {name(r)} | {r['s']['n']} | {money(r['s']['net'])} | {r['s']['dd']:.1f}% |")
    print(f"\n**Together on one $10,000 account at 1% risk per trade:** {ps['n']} trades, "
          f"**{money(ps['net'])} ({ps['net'] / BAL * 100:.0f}%)**, max drawdown **{ps['dd']:.1f}%**, "
          f"profit factor {ps['pf']:.2f}, {ps['mpos']}/{ps['mact']} months up, worst month {money(ps['worst_m'])}, "
          f"up to {mx} trades open at once.\n")
    print("| Month | P&L | Account |\n|---|---|---|")
    bal = BAL
    for m in MONTHS:
        if m in ps["mon"]:
            bal += ps["mon"][m]
            print(f"| {m} | {money(ps['mon'][m])} | ${bal:,.0f} |")
    # weekly correlation matrix summary
    if len(chosen) > 1:
        cs = [corr(a["w"], b["w"]) for i, a in enumerate(chosen) for b in chosen[i + 1:]]
        print(f"\nAverage weekly P&L correlation between the chosen pairs: {sum(cs) / len(cs):.2f} "
              f"(highest {max(cs):.2f}).")

    # walk-forward: choose on the first half only, score on the second half only
    mid = datetime(2026, 5, 20, tzinfo=timezone.utc).timestamp()
    pool = []
    for r in base:
        h1 = [x for x in r["rows"] if x["t"] < mid]
        h2 = [x for x in r["rows"] if x["t"] >= mid]
        if len(h1) < 10:
            continue
        s1 = stats(h1)
        if s1["net"] <= 0 or s1["pf"] < 1.2:
            continue
        r2 = dict(r, s1=s1, h2rows=h2, w1=weekly(h1))
        pool.append(r2)
    pool.sort(key=lambda r: -(r["s1"]["rdd"] if r["s1"]["rdd"] != float("inf") else 99))
    picked = select(pool, "w1", int(sys.argv[2] if len(sys.argv) > 2 else 10))
    rows2 = [x for r in picked for x in r["h2rows"]]
    s2 = stats(rows2) if rows2 else None
    print("\n## Walk-forward check: portfolio chosen on Nov–May only, traded Jun–Nov\n")
    print("| Pair (chosen on first half) | First half net | Second half net |\n|---|---|---|")
    for r in picked:
        print(f"| {name(r)} | {money(r['s1']['net'])} | {money(sum(x['pnl'] for x in r['h2rows']))} |")
    if s2:
        print(f"\n**Second half, never seen when choosing:** {s2['n']} trades, **{money(s2['net'])}**, "
              f"max drawdown {s2['dd']:.1f}%, profit factor {s2['pf']:.2f}, {s2['mpos']}/{s2['mact']} months up, "
              f"{sum(1 for r in picked if sum(x['pnl'] for x in r['h2rows']) > 0)}/{len(picked)} pairs profitable.")


if __name__ == "__main__":
    main()
