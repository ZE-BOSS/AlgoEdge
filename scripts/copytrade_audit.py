"""
scripts/copytrade_audit.py

How well the parser reads an exported channel (scripts/copytrade_export.py):
what every message parsed as, signals per month, and short samples of the
messages most likely misread - signal-looking posts that did not parse as a
signal, signals missing a stop or target, and follow-ups by kind.

    python scripts/copytrade_audit.py /tmp/gts_vip.tsv [--samples 12] [--kind NONE] [--grep WORD]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.copytrade.parser import parse_message  # noqa: E402

LOOKS_LIKE_SIGNAL = re.compile(r"\b(BUY|SELL)\b.*\d{3,}", re.I | re.S)
HAS_LEVELS = re.compile(r"\b(SL|STOP|TP|TAKE\s*PROFIT)\b", re.I)


def short(t: str, n: int = 150) -> str:
    t = " | ".join(x.strip() for x in t.split("\n") if x.strip())
    return t[:n] + ("…" if len(t) > n else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv")
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--kind", default=None, help="list messages of this parsed kind")
    ap.add_argument("--grep", default=None, help="list messages containing this (regex, case-insensitive)")
    a = ap.parse_args()

    rows = []
    for line in open(a.tsv, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        kind, mid, t, ref, text = line.rstrip("\n").split("\t", 4)
        text = text.replace("\\n", "\n")
        rows.append((int(mid), t, int(ref.split(":")[0].lstrip("A") or 0) if kind == "F" else 0, text,
                     parse_message(text)))

    kinds = Counter(p.kind for *_, p in rows)
    print("kinds: " + "  ".join(f"{k} {v}" for k, v in kinds.most_common()))
    months = Counter(t[:7] for _, t, _, _, p in rows if p.kind == "OPEN")
    print("signals/month: " + "  ".join(f"{m[2:]} {v}" for m, v in sorted(months.items())))
    syms = Counter((p.symbol or "?") for *_, p in rows if p.kind == "OPEN")
    print("symbols: " + "  ".join(f"{k} {v}" for k, v in syms.most_common(8)))

    if a.kind or a.grep:
        rx = re.compile(a.grep, re.I) if a.grep else None
        hits = [r for r in rows if (not a.kind or r[4].kind == a.kind) and (not rx or rx.search(r[3]))]
        print(f"\n{len(hits)} matching")
        for mid, t, ref, text, p in hits[: a.samples]:
            print(f"{mid} {t[2:16]} {p.kind:9} {short(text, 170)}")
        return 0

    missed = [r for r in rows if r[4].kind != "OPEN" and LOOKS_LIKE_SIGNAL.search(r[3]) and HAS_LEVELS.search(r[3])]
    print(f"\nsignal-looking but not OPEN: {len(missed)}")
    for mid, t, ref, text, p in missed[: a.samples]:
        print(f"{mid} {t[2:16]} {p.kind:9} {short(text)}")
    incomplete = [r for r in rows if r[4].kind == "OPEN" and (not r[4].sl or not r[4].tps)]
    print(f"\nOPEN without SL or TP: {len(incomplete)}")
    for mid, t, ref, text, p in incomplete[: a.samples]:
        print(f"{mid} {t[2:16]} sl={p.sl} tps={len(p.tps)} {short(text, 120)}")
    for k in ("BREAKEVEN", "MOVE_SL", "CLOSE", "CANCEL", "MOVE_TP"):
        xs = [r for r in rows if r[4].kind == k]
        print(f"\n{k} {len(xs)}:")
        for mid, t, ref, text, p in xs[: max(3, a.samples // 3)]:
            print(f"  {mid} {short(text, 110)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
