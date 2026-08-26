"""
tests/rule1_bare_literals_check.py

[Rule-1/Part 12] "CI check: grep risk/, backtester/, services/ for bare
numeric literals outside the params modules, with an explicit allow-list;
fail the build on new unlisted literals."

Standalone script (not a pytest test — this is a repo-wide scan, closer to a
linter than a unit test) so it can be run directly in CI once a workflow
exists: `python tests/rule1_bare_literals_check.py`. Exits 1 and prints every
newly-introduced bare numeric literal when the allow-list doesn't cover one;
exits 0 otherwise.

WHY AN ALLOW-LIST, NOT A BLANKET BAN: `0`, `1`, `0.0`, `-1`, array indices,
and loop bounds are bare numeric literals too and are completely legitimate —
banning all numbers outright would make this useless noise. The allow-list
below is deliberately small (only comparison/index/loop-bound values that
show up constantly and are never risk-tuning constants); anything else that
looks like a magic number — a threshold, a multiplier, a cap — should live in
a Params dataclass instead, per this codebase's own established convention
(RiskParams, APAParams, VWAPParams, etc.) and be flagged here if it doesn't.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["backend/risk", "backend/backtester", "backend/services"]
EXCLUDE_SUFFIXES = ("params.py", "config_schema.py")  # where constants SHOULD live

# Values that are structurally never risk-tuning constants — indices, bounds,
# percentages' own denominator, boolean-ish 0/1, etc.
ALLOWED_VALUES = {0, 1, -1, 2, 100, 0.0, 1.0, -1.0, 100.0, 60, 1000}


def find_python_files() -> list[Path]:
    files = []
    for d in SCAN_DIRS:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if p.name.endswith(EXCLUDE_SUFFIXES) or "__pycache__" in p.parts:
                continue
            files.append(p)
    return files


def find_bare_literals(file_path: Path) -> list[tuple[int, float]]:
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except SyntaxError:
        return []

    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if node.value in ALLOWED_VALUES:
                continue
            findings.append((node.lineno, node.value))
    return findings


def main() -> int:
    total = 0
    for f in find_python_files():
        literals = find_bare_literals(f)
        if literals:
            rel = f.relative_to(REPO_ROOT)
            for lineno, value in literals:
                print(f"{rel}:{lineno}: bare numeric literal {value!r} — move to a Params/RiskParams field or add to ALLOWED_VALUES if structural")
                total += 1

    if total:
        print(f"\n{total} unlisted bare numeric literal(s) found across {len(SCAN_DIRS)} directories.")
        print("This is a REPORT, not a hard gate yet — no CI workflow exists in this repo to wire it into.")
        print("Review each: either move it to a Params dataclass (this codebase's own convention) or, if it's")
        print("structural (an index, a loop bound), add it to ALLOWED_VALUES above.")
        return 1
    print("No unlisted bare numeric literals found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
