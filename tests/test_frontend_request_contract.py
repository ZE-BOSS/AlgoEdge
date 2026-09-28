"""Every key the frontend sends to /api/backtest must exist on BacktestRequest.

Pydantic IGNORES unknown fields by default. A frontend that sends a key the
model does not declare gets no error, no warning and a 200 — the run simply
proceeds with defaults and reports them as if they were the settings that were
asked for.

That shipped. The Strategy Lab page sent the strategy's parameters under a
GROUP-KEYED block (`{ivw: {...}}`, `{trend_breakout: {...}}`) because that is
how the parameters are addressed everywhere else — in the schema, in
UserConfigV2, in slotSpec.js. `BacktestRequest` has exactly one field for them,
`strategy_params`, so every parameter tuned on that page was dropped in transit
and the preview ran the dataclass defaults. The same block was written to
localStorage on "promote", where the Backtester's NESTED_FORM_KEYS did not read
it either, so a promoted config arrived stripped as well.

Found 2026-09-28 by running the same backtest with and without a parameter
change and getting byte-identical results.
"""

import re
from pathlib import Path

import pytest

from backend.api.routes.backtest import BacktestRequest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend/src"
FIELDS = set(BacktestRequest.model_fields)


def _call_bodies(source: str, pattern: str) -> list[str]:
    """Every object literal matching `pattern`, brace-matched.

    Two shapes are in use: `runBacktest({...})` inline, and `const payload = {...}`
    built first and passed later. Matching only the inline one let the main
    Backtester page skip silently, which is the wrong way for a contract test
    to pass.
    """
    out = []
    for m in re.finditer(pattern, source):
        i = source.index("{", m.start())
        depth, j = 0, i
        while j < len(source):
            if source[j] == "{":
                depth += 1
            elif source[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(source[i:j + 1])
    return out


def _top_level_keys(literal: str) -> set[str]:
    keys, depth = set(), 0
    for line in literal.splitlines():
        stripped = line.strip()
        if depth == 1:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", stripped)
            if m:
                keys.add(m.group(1))
            elif stripped.startswith("["):
                keys.add("<computed>")     # a dynamic key cannot be checked here
        depth += line.count("{") + line.count("[") - line.count("}") - line.count("]")
    return keys


@pytest.mark.parametrize("page,pattern", [
    ("pages/StrategyLab.jsx", r"\brunBacktest\(\s*\{"),
    ("pages/Backtester.jsx", r"\bconst payload\s*=\s*\{"),
])
def test_every_key_sent_to_the_backtest_route_is_a_real_field(page, pattern):
    source = (FRONTEND / page).read_text(encoding="utf-8")
    bodies = _call_bodies(source, pattern)
    assert bodies, f"{page}: no request literal found — the pattern is stale"
    for body in bodies:
        for key in _top_level_keys(body):
            if key == "<computed>":
                pytest.fail(
                    f"{page} sends a COMPUTED key to {fn}. Pydantic drops anything "
                    f"BacktestRequest does not declare, silently — a group-keyed "
                    f"block like {{ivw: ...}} is exactly how the Strategy Lab lost "
                    f"every parameter it was given. Send `strategy_params`.")
            assert key in FIELDS, (
                f"{page} sends '{key}' to {fn}, which BacktestRequest does not "
                f"declare. Pydantic ignores it, so the run proceeds on defaults "
                f"and reports them as your settings.")


def test_strategy_parameters_travel_under_the_one_field_that_exists():
    assert "strategy_params" in FIELDS
    lab = (FRONTEND / "pages/StrategyLab.jsx").read_text(encoding="utf-8")
    assert "strategy_params: params" in lab, "the Lab must send strategy_params"
    assert "[strategy.group]: params" not in lab, "the group-keyed block is the bug"


def test_promoted_configs_land_where_the_backtester_reads_them():
    """"Promote" writes to localStorage for the Backtester to restore. It has to
    use the key the Backtester actually merges (NESTED_FORM_KEYS), or the
    parameters are dropped a second time on the way in."""
    lab = (FRONTEND / "pages/StrategyLab.jsx").read_text(encoding="utf-8")
    bt = (FRONTEND / "pages/Backtester.jsx").read_text(encoding="utf-8")
    nested = re.search(r"NESTED_FORM_KEYS\s*=\s*\[(.*?)\]", bt, re.S).group(1)
    nested_keys = set(re.findall(r"'([^']+)'", nested))
    assert "slot_strategy_params" in nested_keys
    assert "slot_strategy_params:" in lab, "promote must write slot_strategy_params"
    assert "[strategy.group]:" not in lab, "a group-keyed block is not read on either side"
