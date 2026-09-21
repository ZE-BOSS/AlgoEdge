"""
tests/test_closure_scope_hazards.py

A whole class of runtime NameError that neither the import, the linter, nor a
unit test of the helper itself can see.

Shipped 2026-09-21 and broke every backtest in production:

    async def _run_backtest_task():
        async def _save_state(state):
            state["heartbeat"] = _time.time()    # closure read
        await _save_state(initial_state)         # <- called HERE
        try:
            import time as _time                 # <- bound only HERE

`import time as _time` inside a function makes `_time` local to that function
for its ENTIRE body, so the closure read a cell that was not bound yet:
"cannot access free variable '_time' where it is not associated with a value in
enclosing scope". The task died between "Backtest queued" and "Backtest
started" — no progress, no error state, and because no state was ever written,
every later run queued and died the same way.

This scans for the shape: an inner function that reads a name, called before
the enclosing function imports that name.
"""

import ast
from pathlib import Path

BACKEND = Path("backend")


def _imports_inside(fn: ast.AST) -> dict[str, int]:
    """Names this function imports in its own body -> the line that binds them."""
    out: dict[str, int] = {}
    for node in ast.walk(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node is not fn:
            for alias in node.names:
                out.setdefault((alias.asname or alias.name).split(".")[0], node.lineno)
    return out


def _nested(fn: ast.AST):
    for node in ast.walk(fn):
        if node is not fn and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _hazards_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        late = _imports_inside(fn)
        if not late:
            continue
        inner = {n.name: n for n in _nested(fn)}
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "id", None)
            callee = inner.get(name)
            if callee is None:
                continue
            # A name the inner function imports ITSELF is its own local; the
            # enclosing function's later import cannot reach it.
            own = {
                (alias.asname or alias.name).split(".")[0]
                for sub in ast.walk(callee)
                if isinstance(sub, (ast.Import, ast.ImportFrom))
                for alias in sub.names
            }
            for read in ast.walk(callee):
                if not (isinstance(read, ast.Name) and isinstance(read.ctx, ast.Load)):
                    continue
                if read.id in own or read.id not in late:
                    continue
                if call.lineno < late[read.id]:
                    found.append(
                        f"{path}:{call.lineno}: {fn.name}() calls {name}(), which reads "
                        f"'{read.id}' — but {fn.name}() only imports it at line "
                        f"{late[read.id]}, so the closure cell is unbound here"
                    )
    return found


def test_no_closure_reads_a_name_its_caller_imports_later():
    problems: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        problems += _hazards_in(path)

    assert not problems, "\n".join(sorted(set(problems)))
