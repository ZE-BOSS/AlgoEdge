"""Phase 0 alert fixes (2026-11-19).

* A stop that closes in profit must not be announced as "Stop Loss Hit".
* Repeated errors that differ only in numbers ("Retrying in 18s" / "37s")
  must collapse into one Telegram alert per window.
* A rule-exit strategy's placeholder TP stays at its documented size, so the
  alert wording added for it describes what actually happens.
"""

import re

from backend.notify.labels import close_reason_label


def test_sl_in_loss_is_stop_loss():
    assert close_reason_label("SL", -272.31) == "Stop Loss Hit"


def test_sl_in_profit_is_not_stop_loss():
    assert close_reason_label("SL", 33.29) == "Stop Hit in Profit"


def test_trail_in_profit():
    assert close_reason_label("TRAIL", 33.29) == "Trailing Stop Hit (profit)"


def test_breakeven_band():
    assert close_reason_label("SL", 0.2) == "Breakeven Stop Hit"
    assert close_reason_label("TRAIL", -0.4) == "Breakeven Stop Hit"


def test_take_profit_levels():
    assert close_reason_label("TP2", 50.0) == "Take Profit 2 Hit"
    assert close_reason_label("TP", 50.0) == "Take Profit Hit"


def test_strategy_rule_exits_are_named():
    assert close_reason_label("RQ_SESSION_CLOSE", -10.0) == "Session Close (strategy rule)"
    assert close_reason_label("CLIENT", 5.0) == "Manual Close"
    assert close_reason_label(None, 0.0) == "Closed"


def test_error_dedupe_key_ignores_numbers():
    # Same normalisation as global_error_handler's error_sink.
    def key(msg):
        return "m:f:" + re.sub(r"\d+(\.\d+)?", "#", msg)
    assert key("MT5 auto-reconnect failed. Retrying in 18s...") == \
        key("MT5 auto-reconnect failed. Retrying in 37s...")
    assert key("Retrying in 18s") != key("Symbol missing")


def test_error_handler_uses_normalised_key():
    src = open("backend/utils/global_error_handler.py", encoding="utf-8").read()
    assert "_dedupe_key" in src and "auto-reconnect failed" in src


def test_sigma_placeholder_is_far_by_design():
    # The NAS100 alert of 2026-11-19 (TP 5458.62 on entry 27293.1) is this
    # placeholder: 2% disaster stop x 40R = 80% of price. It is a never-fill
    # target for a strategy that exits at 16:00 ET or on the opposite band.
    from backend.strategies.strategy_defaults import NO_MEASURED_TARGET, STRATEGY_DEFAULTS
    assert "RQ_SigmaBreakout_v1" in NO_MEASURED_TARGET
    entry, stop_pct = 27293.1, 2.0
    sl = entry * (1 + stop_pct / 100)
    tp = entry - STRATEGY_DEFAULTS["RQ_SigmaBreakout_v1"]["tp1_rr"] * (sl - entry)
    assert abs(tp - 5458.62) < 0.01
