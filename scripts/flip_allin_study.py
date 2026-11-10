"""How often does the TikTok "flip" survive? 5 years of XAUUSD M5.

The video (supremefx93, 5 Nov) runs the WHOLE balance at full leverage on every
trade: margin used ~= equity, so a small move against the position hits the
broker's stop-out. With leverage L and stop-out at 50 % margin level, a full-size
position is closed after losing half the balance, i.e. after an adverse move of
    0.5 * price / L            (gold 4100, 1:1000 -> $2.05;  1:2000 -> $1.03)
We count, from every M5 close, whether price reaches +target before -that
distance (spread paid; a bar touching both counts as the loss). Entries:
  any      - every bar, both sides
  extreme  - "market exhaustion": buy at a 48-bar (4 h) low, sell at a 4 h high,
             the kind of level the video calls a sniper entry
"""
import numpy as np

d = np.load("data/edge_lab/bars/XAUUSD.npz")
o, h, l, c = (d[k].astype(float) for k in ("open", "high", "low", "close"))
sp = d["spread"].astype(float) * float(d["point"])
n = len(c)
H = 288                                     # give each trade up to one day

def win_prob(mask, side, target, adverse):
    idx = np.nonzero(mask[: n - H])[0]
    if side > 0:
        entry = c[idx] + sp[idx]            # buy at the ask
        tp, so = entry + target, entry - adverse
    else:
        entry = c[idx]                      # sell at the bid, closes at the ask
        tp, so = entry - target, entry + adverse
    res = np.zeros(len(idx), dtype=np.int8)  # 1 win, -1 stop-out, 0 neither in a day
    for k in range(1, H + 1):
        j = idx + k
        open_ = res == 0
        if not open_.any():
            break
        if side > 0:
            lose = l[j] <= so
            win = h[j] >= tp
        else:
            lose = h[j] + sp[j] >= so
            win = l[j] + sp[j] <= tp
        res[open_ & lose] = -1
        res[open_ & win & ~lose] = 1
    done = res != 0
    return (res == 1).sum() / max(done.sum(), 1), len(idx)

lo48 = np.array([l[max(0, i - 48):i].min() if i else l[0] for i in range(n)])
hi48 = np.array([h[max(0, i - 48):i].max() if i else h[0] for i in range(n)])
at_low = l <= lo48                      # this bar made a new 4 h low
at_high = h >= hi48
any_ = np.ones(n, bool)

price = float(np.median(c[-50000:]))
print(f"bars {n}  median recent price {price:.0f}  median spread ${np.median(sp):.2f}")
for lev in (500, 1000, 2000):
    adv = 0.5 * price / lev
    print(f"\n== leverage 1:{lev}: stop-out after ${adv:.2f} against")
    for name, mb, ms in (("any bar", any_, any_), ("4h extreme", at_low, at_high)):
        row = []
        for tgt in (3, 6, 16, 22):
            pb, nb = win_prob(mb, +1, tgt, adv)
            ps, ns = win_prob(ms, -1, tgt, adv)
            p = (pb * nb + ps * ns) / (nb + ns)
            row.append(f"+${tgt}: {p:5.1%}")
        print(f"  {name:11s} " + "   ".join(row))
