"""Per-confluence lift, measured against the market.

For each atomic condition, compare the stop-aware forward excursion of bars
where it is TRUE against the baseline of all bars in the same direction. The
question is not "how often does this fire" (block-rate is not value, per the
programme's own rule) but "does price go further when it fires".

  lift_hit  = P(reach TP | atom) - P(reach TP)
  lift_exp  = E[R | atom] - E[R]

An atom with lift_exp <= 0 is costing money to evaluate. An atom firing on
almost every bar cannot select anything even if its lift looks positive.
"""
from __future__ import annotations
import sys
import numpy as np
from confluence_atoms import load, atr, build_atoms, htf_bias
from optimise_exits import excursion, SYMBOLS

TF = "M15"
HORIZON = 96
STOP_MULT = 1.0
TP = 2.0


def analyse(sym, stop_mult=STOP_MULT, tp=TP):
    b = load(sym, TF)
    if b is None:
        return None
    h, l, c = b["high"], b["low"], b["close"]
    A, a = build_atoms(b)
    risk = stop_mult * a
    cost = float(b["spread"]) * float(b["point"]) / np.nanmedian(risk[risk > 0])

    h4 = load(sym, "H4")
    bias = htf_bias(b, h4) if h4 is not None else np.zeros(len(c))
    A["htf_aligned_up"] = bias > 0
    A["htf_aligned_dn"] = bias < 0

    hours = ((b["time"] % 86400) // 3600).astype(int)
    A["session_london_up"] = (hours >= 7) & (hours < 12)
    A["session_london_dn"] = A["session_london_up"]
    A["session_ny_up"] = (hours >= 12) & (hours < 17)
    A["session_ny_dn"] = A["session_ny_up"]
    A["session_asia_up"] = (hours >= 23) | (hours < 7)
    A["session_asia_dn"] = A["session_asia_up"]

    out = {}
    for direction, suffix in (("BUY", "_up"), ("SELL", "_dn")):
        mr, valid = excursion(h, l, c, risk, direction, HORIZON)
        win = mr >= tp
        base_hit = win[valid].mean()
        base_exp = np.where(win, tp - cost, -1.0 - cost)[valid].mean()
        for name, mask in A.items():
            if not name.endswith(suffix):
                continue
            m = mask & valid & np.isfinite(risk)
            n = int(m.sum())
            if n < 200:
                continue
            hit = win[m].mean()
            exp = np.where(win, tp - cost, -1.0 - cost)[m].mean()
            out[(direction, name[:-3])] = {
                "n": n, "fire": n / int(valid.sum()),
                "hit": hit, "lift_hit": hit - base_hit,
                "exp": exp, "lift_exp": exp - base_exp,
            }
        out[(direction, "_BASELINE")] = {
            "n": int(valid.sum()), "fire": 1.0, "hit": base_hit,
            "lift_hit": 0.0, "exp": base_exp, "lift_exp": 0.0}
    return out


if __name__ == "__main__":
    syms = sys.argv[1:] or SYMBOLS
    print(f"{TF}, stop {STOP_MULT}xATR, TP {TP}R, horizon {HORIZON} bars\n")
    agg = {}
    for sym in syms:
        r = analyse(sym)
        if not r:
            print(f"{sym}: no cache"); continue
        print(f"=== {sym}")
        print(f"  {'atom':22s}{'dir':>5s}{'fires':>8s}{'n':>8s}"
              f"{'P(2R)':>8s}{'lift':>8s}{'expR':>9s}{'liftExp':>9s}")
        for (d, name), v in sorted(r.items(), key=lambda kv: -kv[1]["lift_exp"]):
            print(f"  {name:22s}{d:>5s}{v['fire']:8.1%}{v['n']:8d}"
                  f"{v['hit']:8.1%}{v['lift_hit']:+8.1%}{v['exp']:+9.3f}{v['lift_exp']:+9.3f}")
            if name != "_BASELINE":
                agg.setdefault(name, []).append(v["lift_exp"])
        print()
    print("\n============ ATOM RANKING, averaged across all symbols ============")
    print(f"{'atom':24s}{'symbols':>9s}{'mean liftExp':>14s}{'positive on':>13s}")
    for name, vals in sorted(agg.items(), key=lambda kv: -np.mean(kv[1])):
        pos = sum(1 for x in vals if x > 0)
        print(f"{name:24s}{len(vals):9d}{np.mean(vals):+14.4f}{pos:>8d}/{len(vals)}")
