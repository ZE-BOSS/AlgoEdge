"""Backtest every symbol a channel trades often enough, compactly.

    python scripts/copytrade_batch.py /tmp/a.tsv /tmp/b.tsv --min 20 --tf M1

Runs scripts/copytrade_backtest.py once per (channel, symbol) and prints, per
variant: filled trades, total R, profit factor, max drawdown."""
import argparse
import collections
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.copytrade.parser import parse_message  # noqa: E402

SHORT = {
    "AlgoEdge copier": "copier (follows him)",
    "Signals only, no follow-ups": "signals only",
    "Signals only, auto-BE": "signals + BE@TP1",
    "Follows his messages + auto-BE": "copier + BE@TP1",
    "Follows his messages + closes": "copier + close on opposite",
    "Follows + TP ladder trail": "copier + ladder",
    "Follows + first fill: rest": "copier + rest at market",
    "Follows + ladder + first fill: rest": "copier + ladder + rest at mkt",
    "Follows + ladder + first fill: ADD": "copier + ladder + market add",
}


def short(name):
    for k in sorted(SHORT, key=len, reverse=True):
        if name.startswith(k):
            return SHORT[k]
    return name[:30]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv", nargs="+")
    ap.add_argument("--min", type=int, default=20)
    ap.add_argument("--tf", default="M1")
    ap.add_argument("--full", action="store_true", help="every variant, not one line per symbol")
    a = ap.parse_args()
    for path in a.tsv:
        cnt = collections.Counter()
        for line in open(path, encoding="utf-8"):
            if line.startswith("S\t"):
                cnt[parse_message(line.split("\t", 4)[4].replace("\\n", "\n")).symbol or "?"] += 1
        print(f"\n######## {Path(path).stem}: " + ", ".join(f"{s} {n}" for s, n in cnt.most_common(8)), flush=True)
        for sym, n in cnt.most_common():
            if n < a.min or sym == "?":
                continue
            r = subprocess.run([sys.executable, str(ROOT / "scripts/copytrade_backtest.py"), path,
                                "--symbol", sym, "--tf", a.tf], capture_output=True, text=True, timeout=1800)
            out = r.stdout
            rng = re.search(r"# (\d+)/(\d+) signals inside the bars \(([^)]*)\)", out)
            if r.returncode or not rng:
                err = (r.stderr.strip().splitlines() or ["?"])[-1]
                print(f"  {sym}: {n} signals - no result ({err[:120]})", flush=True)
                continue
            res = {}
            for block in out.split("\n== ")[1:]:
                t = re.search(r"total ([+-][\d.]+)R.*?PF ([\d.inf]+).*?maxDD ([\d.]+)R", block)
                f = re.search(r"filled (\d+)", block)
                if t:
                    res[short(block.splitlines()[0])] = (int(f.group(1)), float(t.group(1)), t.group(2), float(t.group(3)))
            if not a.full:
                best = max(res.items(), key=lambda kv: kv[1][1]) if res else None
                cp = res.get("copier (follows him)")
                sb = res.get("signals + BE@TP1")
                fmt = lambda v: f"{v[1]:+6.1f}R PF {v[2]:>4} DD {v[3]:4.1f}R" if v else "-"
                print(f"  {sym[:26]:26s} {rng.group(1):>4}/{rng.group(2):<4} filled {cp[0] if cp else 0:>3} | copier {fmt(cp)} | "
                      f"sig+BE {fmt(sb)} | best {best[0] if best else '-'} {fmt(best[1]) if best else ''}", flush=True)
                continue
            print(f"  {sym}: {rng.group(1)}/{rng.group(2)} signals in bars {rng.group(3)}", flush=True)
            for block in out.split("\n== ")[1:]:
                name = block.splitlines()[0]
                t = re.search(r"total ([+-][\d.]+)R.*?PF ([\d.inf]+).*?maxDD ([\d.]+)R", block)
                f = re.search(r"filled (\d+)", block)
                if t:
                    print(f"    {short(name):32s} filled {f.group(1):>4}  {t.group(1):>7}R  PF {t.group(2):>5}  DD {t.group(3)}R",
                          flush=True)
            m = re.search(r"by month \(R\): (.*)", out)
            if m:
                print(f"    by month (copier): {m.group(1)}", flush=True)


if __name__ == "__main__":
    main()
