"""
tests/test_engineering_rules.py

[Rule-2/Rule-4/Part 12] Engineering-rules enforcement tests from the master
plan. No CI pipeline exists in this repo yet (no `.github/workflows/`,
`pytest.ini`, or `conftest.py` found) — AND no test framework is installed in
either venv here (`import pytest` resolves to an empty stray namespace
package, not the real library). Written as plain-assert functions with a
manual runner at the bottom so they run TODAY via
`python tests/test_engineering_rules.py`, with zero new dependencies. If
`pip install pytest` is ever run, these are already pytest-collectible
(plain `test_*` functions/classes, no decorators needed) — the
`@pytest.mark.parametrize` niceties were deliberately left out for that
reason, not forgotten.

Run with: python tests/test_engineering_rules.py
"""

import dataclasses
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _literal_dict_keys_in_range(file_path: Path, start_marker: str, max_lines: int = 80) -> set[str]:
    """
    [Rule-2] Pragmatic key extractor: find the first `dict_name = {` matching
    `start_marker`, then collect every quoted string that appears in
    `"key":` position within the next `max_lines` lines — covers both plain
    key:value pairs and the `**{...}.items() if v is not None}` merge
    pattern this codebase uses (backtest.py's merged_risk_config,
    bot_service.py's risk_config), since both still write literal `"key":`
    tokens. This is intentionally NOT a full expression evaluator — it reads
    the SHAPE of the dict literal, not its runtime values, which is exactly
    what a key-set drift check needs and nothing more.
    """
    text = file_path.read_text(encoding="utf-8")
    idx = text.find(start_marker)
    if idx == -1:
        return set()
    lines = text[idx:].splitlines()[:max_lines]
    keys = set()
    for line in lines:
        for m in re.finditer(r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:', line):
            keys.add(m.group(1))
    return keys


def _riskparams_field_names() -> set[str]:
    from backend.core.config_schema import RiskParams
    return {f.name for f in dataclasses.fields(RiskParams)}


def test_backtest_risk_config_key_drift_report(marker="merged_risk_config = {", label="backtest.py::merged_risk_config"):
    """
    [Rule-2/3.15] Diagnostic, not a hard gate (3.14 — replacing these dict
    literals with `dataclasses.asdict(RiskParams())` — was deliberately not
    done; see TASKS.md 3.14 for why). Reports the actual key-set diff between
    the backtest route's risk_config dict literal and RiskParams' real
    fields, so the drift this rule exists to catch is visible and
    measurable instead of assumed-fixed.
    """
    bt_path = REPO_ROOT / "backend" / "api" / "routes" / "backtest.py"
    dict_keys = _literal_dict_keys_in_range(bt_path, marker, max_lines=120)
    rp_fields = _riskparams_field_names()

    missing_from_dict = rp_fields - dict_keys
    extra_in_dict = dict_keys - rp_fields

    print(f"\n[{label}] RiskParams fields not present in dict literal ({len(missing_from_dict)}): "
          f"{sorted(missing_from_dict)}")
    print(f"[{label}] Dict literal keys not on RiskParams ({len(extra_in_dict)}): "
          f"{sorted(extra_in_dict)}")

    # Not a hard assertion — see docstring. A future pass that completes 3.14
    # (asdict-based construction) should tighten this to a real assert.


def test_riskparams_fields_are_stable_and_nonempty():
    """Sanity check the extractor itself has something real to compare against."""
    fields = _riskparams_field_names()
    assert len(fields) > 50, "RiskParams should have a substantial number of fields by now"
    assert "risk_per_trade_pct" in fields
    assert "max_cluster_risk_pct" in fields  # [9.5] — proves this test file is checking current code, not stale


class TestBindingConstraintCoverage:
    """
    [Rule-4] "Every code path that reduces position size sets
    binding_constraint; every rejection path writes a named reason to the
    funnel." Verifies the concrete cases added this session actually produce
    a named (not generic) reason — a full static-analysis sweep of every
    rejection path in risk/engine.py is a larger undertaking than this test
    attempts; this locks in the specific paths exercised elsewhere in this
    session's own verification so a future regression is caught.
    """

    def _base_cfg(self, **overrides):
        cfg = {
            "risk_per_trade_pct": 1.0, "min_rr": 0.5, "tp_count": 1, "tp1_rr": 5.0,
            "tp_splits": [100], "multi_position_mode": True, "is_backtest": True,
        }
        cfg.update(overrides)
        return cfg

    def test_margin_binding_constraint_is_named(self):
        from backend.risk.engine import RiskEngine
        eng = RiskEngine(self._base_cfg(max_margin_utilisation_pct=0.01))
        sig = {"symbol": "EURUSD", "direction": "BUY", "entry_price": 1.10,
               "stop_loss": 1.0999, "confluence_score": 100}
        approved, reason, _ = eng.evaluate_signal(sig, 25000, initial_balance=25000)
        # Either rejected outright (below min lot after margin clamp) or
        # approved with binding_constraint == "margin" — never silent.
        if approved:
            diag = sig.get("metadata", {}).get("sizing_diagnostics", {})
            assert diag.get("binding_constraint") in ("margin", "lot_min"), diag
        else:
            assert reason and reason != "Lot size calculation returned 0", reason

    def test_cluster_exposure_rejection_has_named_reason(self):
        from datetime import datetime, timezone
        from backend.risk.engine import RiskEngine
        eng = RiskEngine(self._base_cfg(max_cluster_risk_pct=0.5))
        now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        eng.circuit._check_daily_reset(now)
        eng.circuit._check_weekly_reset(now)
        eng.circuit.position_opened("g1", 1, symbol="XAUUSD", initial_risk_dollars=200.0, direction="BUY")
        sig = {"symbol": "XAGUSD", "direction": "BUY", "entry_price": 24.0,
               "stop_loss": 23.5, "confluence_score": 100}
        approved, reason, _ = eng.evaluate_signal(sig, 25000, current_time=now, initial_balance=25000)
        assert not approved
        assert "Cluster exposure cap breached" in reason  # named, not generic

    def test_strategy_daily_budget_rejection_has_named_reason(self):
        from datetime import datetime, timezone
        from backend.risk.engine import RiskEngine
        eng = RiskEngine(self._base_cfg(
            max_daily_drawdown_pct=3.0, strategy_risk_budget_pct={"VWAP_v1": 20.0}
        ))
        now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        eng.circuit._check_daily_reset(now)
        eng.circuit._check_weekly_reset(now)
        eng.circuit.strategy_daily_pnl["VWAP_v1"] = -150.0  # exactly exhausts 20% of 3% * 25000
        sig = {"symbol": "EURUSD", "direction": "BUY", "entry_price": 1.10, "stop_loss": 1.095,
               "confluence_score": 100, "metadata": {"strategy_id": "VWAP_v1"}}
        approved, reason, _ = eng.evaluate_signal(sig, 25000, current_time=now, initial_balance=25000)
        assert not approved
        assert "daily risk budget" in reason


def _run_all():
    """Manual runner — no pytest installed in this repo's venvs. See module docstring."""
    failures = []
    tests = [
        ("test_backtest_risk_config_key_drift_report (diagnostic, always passes)", test_backtest_risk_config_key_drift_report),
        ("test_riskparams_fields_are_stable_and_nonempty", test_riskparams_fields_are_stable_and_nonempty),
    ]
    cls = TestBindingConstraintCoverage()
    tests += [
        ("TestBindingConstraintCoverage.test_margin_binding_constraint_is_named", cls.test_margin_binding_constraint_is_named),
        ("TestBindingConstraintCoverage.test_cluster_exposure_rejection_has_named_reason", cls.test_cluster_exposure_rejection_has_named_reason),
        ("TestBindingConstraintCoverage.test_strategy_daily_budget_rejection_has_named_reason", cls.test_strategy_daily_budget_rejection_has_named_reason),
    ]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
            failures.append(name)
        except Exception as e:
            print(f"ERROR {name}: {type(e).__name__}: {e}")
            failures.append(name)

    print()
    if failures:
        print(f"{len(failures)}/{len(tests)} failed: {failures}")
        return 1
    print(f"All {len(tests)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(_run_all())
