"""Compare two copies of backend/copytrade/parser.py on every cached channel message.

    python scripts/parser_symbol_diff.py OLD_REPO NEW_REPO CACHE_DIR [CACHE_DIR ...]

Prints every message whose parsed kind/symbol/side changes, so a parser change
can be checked against the real channels before it goes live."""
import importlib.util
import json
import sys
from pathlib import Path


def load(root, name):
    spec = importlib.util.spec_from_file_location(name, Path(root) / "backend/copytrade/parser.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m          # dataclasses look their module up here
    spec.loader.exec_module(m)
    return m


old, new = load(sys.argv[1], "p_old"), load(sys.argv[2], "p_new")
n = changed = 0
def sources(d):
    if d.endswith(".tsv"):                     # copytrade_export.py output: every message, text last
        msgs = []
        for line in open(d, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t", 4)
            if len(parts) == 5:
                msgs.append({"id": parts[1], "text": parts[4].replace("\\n", "\n")})
        yield Path(d).stem, msgs
        return
    if d.endswith(".db"):                      # every message the copier logged
        import sqlite3
        con = sqlite3.connect(d)
        names = dict(con.execute("select id, title from copytrade_channels"))
        rows = con.execute("select id, channel_id, text from copytrade_signals").fetchall()
        by = {}
        for i, ch, t in rows:
            by.setdefault(names.get(ch, ch), []).append({"id": i, "text": t})
        for title, msgs in by.items():
            yield title, msgs
        return
    for f in sorted(Path(d).glob("*.json")):
        c = json.loads(f.read_text())
        yield c.get("title") or f.stem, c.get("messages", [])


for d in sys.argv[3:]:
    for title, msgs in sources(d):
        for m in msgs:
            t = m.get("text") or ""
            if not t.strip():
                continue
            n += 1
            a, b = old.parse_message(t), new.parse_message(t)
            if (a.kind, a.symbol, a.side) != (b.kind, b.symbol, b.side):
                changed += 1
                print(f"{d.split('/')[3]:5s} {str(title)[:22]:22s} #{m.get('id')}: {a.kind}/{a.symbol}/{a.side} -> {b.kind}/{b.symbol}/{b.side} | {' '.join(t.split())[:90]}")
print(f"messages {n}, changed {changed}")
