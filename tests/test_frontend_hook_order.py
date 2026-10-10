"""
tests/test_frontend_hook_order.py

A React hook that reads a `useState` variable declared further down the same
component is a crash, not a warning.

THE BUG THIS EXISTS FOR
-----------------------
`Backtester.jsx` gained this, placed ~150 lines above where `form` is
declared:

    const overlap = useOverlap({
      agentId: agentIdForStrategy(form.strategy_id),
      symbol: form.symbol,
      ...
    });

A `const` is in its temporal dead zone until its own declaration executes, and
hook arguments are evaluated immediately — so every render threw
`Cannot access 'form' before initialization` (minified to `'j'`) and the whole
Backtester page showed its error boundary. Nothing caught it: the Vite build
succeeds, eslint has no `no-use-before-define`, and there is no JS test runner
in this project.

WHY NOT JUST TURN ON `no-use-before-define`
-------------------------------------------
Tried it. 65 hits across `src`, and almost all are module-level constants
referenced inside a component declared above them — which is perfectly safe,
because the module finishes evaluating before any component body runs. A rule
that is wrong 60-odd times out of 65 is a rule someone switches off.

So this checks the narrow case that is always a real crash: a `useState`
variable used, inside the same function, on a line above its own declaration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path("frontend/src")

# `const [form, setForm] = useState(` / `const [x] = useState(`
STATE_DECL = re.compile(r"^\s*const \[(\w+)(?:\s*,\s*(\w+))?\]\s*=\s*useState\b")
# a component or hook body opens with `function Name(` or `const Name = (`
BODY_START = re.compile(
    r"^(?:export\s+)?(?:default\s+)?function\s+(\w+)\s*\(|^\s*const\s+(\w+)\s*=\s*\(?[^=]*\)?\s*=>\s*\{")


def jsx_files() -> list[Path]:
    return sorted(list(SRC.rglob("*.jsx")) + list(SRC.rglob("*.js")))


def bodies(lines: list[str]) -> list[tuple[int, int, str]]:
    """Rough (start, end, name) spans for top-level functions in a module.

    Brace counting rather than a parser: good enough to keep one component's
    declarations from being matched against another's, which is the only
    thing this needs.
    """
    out = []
    i = 0
    while i < len(lines):
        m = BODY_START.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1) or m.group(2)
        depth = lines[i].count("{") - lines[i].count("}")
        j = i + 1
        while j < len(lines) and depth > 0:
            depth += lines[j].count("{") - lines[j].count("}")
            j += 1
        out.append((i, j, name))
        i = j
    return out


# A top-level `const x = useSomething(` inside a component body. Two-space
# indent: deeper than that and it is nested in something, which changes when
# it runs.
HOOK_CALL = re.compile(r"^  const \w+ = (use[A-Z]\w*)\(")


def statement_extent(span: list[str], first: int) -> tuple[int, str]:
    """From a statement's first line to its balanced close. Returns the text."""
    depth = 0
    text = []
    for i in range(first, len(span)):
        line = span[i]
        text.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0 and i > first or (depth <= 0 and line.rstrip().endswith(";")):
            return i, "\n".join(text)
    return len(span) - 1, "\n".join(text)


def offences(path: Path) -> list[str]:
    """References that are evaluated IMMEDIATELY and point at a later `const`.

    The distinction that matters, and the one the first version of this check
    got wrong: a component body may freely mention a state variable inside a
    CALLBACK declared above it, because the callback runs after render. Only
    an argument evaluated on the spot - a hook's arguments - is a crash. The
    crude version flagged 13 safe callback references in `Backtester.jsx`
    alone, which is how a guard gets switched off.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    found = []
    for start, end, fn_name in bodies(lines):
        span = lines[start:end]
        declared: dict[str, int] = {}
        for offset, line in enumerate(span):
            m = STATE_DECL.match(line)
            if m:
                for name in (m.group(1), m.group(2)):
                    if name:
                        declared.setdefault(name, offset)
        if not declared:
            continue

        for offset, line in enumerate(span):
            if not HOOK_CALL.match(line):
                continue
            last, text = statement_extent(span, offset)
            # Anything after a `=>` or `function` inside the call is a
            # callback body: it runs later, so it is not a dead-zone read.
            head = re.split(r"=>|\bfunction\b", text)[0]
            for name, decl_at in declared.items():
                if decl_at <= last:
                    continue        # declared before or within: fine
                if re.search(rf"\b{re.escape(name)}\b", head):
                    found.append(
                        f"{path.as_posix()}:{start + offset + 1} "
                        f"{HOOK_CALL.match(line).group(1)}() reads '{name}' as an "
                        f"argument, but '{name}' is declared at line "
                        f"{start + decl_at + 1}. Hook arguments are evaluated "
                        f"immediately, so this throws "
                        f"\"Cannot access '{name}' before initialization\".")
    return found


@pytest.mark.parametrize("path", jsx_files(), ids=lambda p: p.as_posix())
def test_no_state_variable_is_used_before_it_is_declared(path):
    """The crash this file exists for, checked on every component.

    Only an immediately-evaluated hook ARGUMENT that reads a `useState`
    variable declared further down the same body. A reference inside a
    callback is fine and is not flagged.
    """
    bad = offences(path)
    assert not bad, "\n".join(bad)


def test_the_checker_catches_the_bug_it_was_written_for(tmp_path):
    """A checker that cannot fail is not a checker.

    This is the exact shape that broke the Backtester, reduced to the few
    lines that matter.
    """
    sample = tmp_path / "Broken.jsx"
    sample.write_text(
        "export default function Broken() {\n"
        "  const overlap = useOverlap({\n"
        "    symbol: form.symbol,\n"
        "  });\n"
        "  const [form, setForm] = useState({});\n"
        "  return <div>{overlap}</div>;\n"
        "}\n", encoding="utf-8")
    assert offences(sample), "the checker missed a real temporal dead zone"


def test_the_checker_allows_the_normal_order(tmp_path):
    """And it must not cry wolf on correct code, or it gets switched off."""
    sample = tmp_path / "Fine.jsx"
    sample.write_text(
        "export default function Fine() {\n"
        "  const [form, setForm] = useState({});\n"
        "  const overlap = useOverlap({ symbol: form.symbol });\n"
        "  return <div>{overlap}</div>;\n"
        "}\n", encoding="utf-8")
    assert offences(sample) == []


def test_a_callback_may_mention_state_declared_later(tmp_path):
    """The false positive that made the first version unusable.

    `useCallback`'s body runs after render, so reading a state variable
    declared below it is correct code and extremely common.
    """
    sample = tmp_path / "Callback.jsx"
    sample.write_text(
        "export default function Fine() {\n"
        "  const run = useCallback(() => {\n"
        "    console.log(form.symbol);\n"
        "  }, [form]);\n"
        "  const [form, setForm] = useState({});\n"
        "  return <div onClick={run} />;\n"
        "}\n", encoding="utf-8")
    assert offences(sample) == []
