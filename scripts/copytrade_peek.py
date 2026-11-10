"""Quick look at an exported channel TSV: how many signals, which symbols, how
they write entries and follow-ups. python scripts/copytrade_peek.py x.tsv [n]"""
import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.copytrade.parser import parse_message  # noqa: E402

path, n = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5
rows = [l.rstrip("\n").split("\t", 4) for l in open(path, encoding="utf-8") if not l.startswith("#")]
S = [r for r in rows if r[0] == "S"]
F = [r for r in rows if r[0] == "F"]
kinds = collections.Counter(parse_message(r[4].replace("\\n", "\n")).kind for r in F)
syms = collections.Counter()
for r in S:
    p = parse_message(r[4].replace("\\n", "\n"))
    syms[(p.symbol or "?", p.side)] += 1
print(f"{path}: {len(rows)} msgs, {len(S)} signals {S[0][2][:10] if S else ''}..{S[-1][2][:10] if S else ''}")
print("  symbols:", ", ".join(f"{s}/{d}:{c}" for (s, d), c in syms.most_common(10)))
print("  follow-ups parsed:", dict(kinds))
step = max(1, len(S) // n)
for r in S[::step][:n]:
    print("  S", r[2][5:16], r[4][:230])
kw = re.compile(r"(?i)\b(be|break ?even|close|closed|hit|sl|stop|secure|partial|cancel)\b")
fs = [r for r in F if kw.search(r[4])]
for r in fs[:: max(1, len(fs) // n)][:n]:
    print("  F", r[2][5:16], r[4][:160])
