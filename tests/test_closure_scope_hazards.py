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
    """Names this function imports in its own body -> the EARLIEST line that binds them.

    Earliest, not first-walked: `ast.walk` is breadth-first, so it can reach a
    second `import asyncio` further down the function before the one near the
    top. Reporting the later line turned a function that imports a name twice
    (once early in a `try`, once later) into a false positive.
    """
    out: dict[str, int] = {}
    for node in ast.walk(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node is not fn:
            for alias in node.names:
                key = (alias.asname or alias.name).split(".")[0]
                out[key] = min(out.get(key, node.lineno), node.lineno)
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


# ── the sibling hazard: the SAME function, not a closure ────────────────────
#
# Hit again on 2026-09-27 in portfolio_engine.run():
#
#     from backend.backtester.engine import _apply_exit_slippage   # module level
#     def run(self):
#         ...
#         pos["exit_price"] = _apply_exit_slippage(...)            # <- reads HERE
#         ...
#         from backend.backtester.engine import _apply_exit_slippage  # binds HERE
#
# The local import makes the name function-local for the WHOLE body, so the
# earlier read raises UnboundLocalError even though a module-level import of the
# same name exists three lines from the top of the file. The scanner above only
# looks at closures, so it passed. This one looks at the enclosing function
# itself, which is the shape that actually shipped twice.


def _own_body_nodes(fn: ast.AST):
    """Nodes in this function's OWN body, not descending into nested functions.

    A read inside a nested function is the closure case, and its timing depends
    on when that function is CALLED rather than where it is written — which is
    exactly what the scanner above checks. Walking into them here reported four
    false positives on `asyncio` in backtest.py, where the read sits in a nested
    `_save_state()` that is only ever called after the import has run.
    """
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _self_shadow_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_level = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module_level.add((alias.asname or alias.name).split(".")[0])

    nested_fns = {inner for fn in ast.walk(tree)
                  if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                  for inner in _nested(fn)}

    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn in nested_fns:
            continue
        late = _imports_inside(fn)
        # Only names that ALSO exist at module level are a trap: without the
        # module-level binding the early read would be an obvious NameError that
        # any run of the code finds immediately.
        late = {n: line for n, line in late.items() if n in module_level}
        if not late:
            continue
        for read in _own_body_nodes(fn):
            if not (isinstance(read, ast.Name) and isinstance(read.ctx, ast.Load)):
                continue
            line = late.get(read.id)
            if line is not None and read.lineno < line:
                found.append(
                    f"{path}:{read.lineno}: {fn.name}() reads '{read.id}', which the module "
                    f"imports at the top — but {fn.name}() re-imports it at line {line}, "
                    f"making it function-local for the whole body (UnboundLocalError)"
                )
    return found


def test_no_function_reads_a_module_import_it_later_shadows():
    problems: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        problems += _self_shadow_in(path)

    assert not problems, "\n".join(sorted(set(problems)))


# ── the scanners must actually catch their own bug shapes ──────────────────
SELF_SHADOW_SAMPLE = '''
from helpers import do_thing

def run(self):
    x = do_thing(1)          # reads it here
    from helpers import do_thing   # binds it here, for the WHOLE body
    return x, do_thing(2)
'''

CLOSURE_SAMPLE = '''
def outer():
    def inner():
        return _time.time()
    inner()
    import time as _time
'''

SAFE_SAMPLE = '''
from helpers import do_thing

def run(self):
    try:
        import asyncio
        return asyncio.sleep(0)
    except asyncio.CancelledError:
        pass

def other():
    def later():
        return _time.time()
    import time as _time
    return later()
'''


def _write(tmp_path, name, src) -> Path:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return path


def test_the_self_shadow_scanner_catches_its_own_shape(tmp_path):
    found = _self_shadow_in(_write(tmp_path, "bad.py", SELF_SHADOW_SAMPLE))
    assert found and "do_thing" in found[0], found


def test_the_closure_scanner_catches_its_own_shape(tmp_path):
    found = _hazards_in(_write(tmp_path, "bad2.py", CLOSURE_SAMPLE))
    assert found and "_time" in found[0], found


def test_neither_scanner_fires_on_safe_code(tmp_path):
    """An import EARLIER in the same body, and a closure called after its import,
    are both fine — these were real false positives before the scanners were
    taught about walk order and nested-function boundaries."""
    path = _write(tmp_path, "ok.py", SAFE_SAMPLE)
    assert _self_shadow_in(path) == []
    assert _hazards_in(path) == []
