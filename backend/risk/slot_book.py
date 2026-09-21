"""
backend/risk/slot_book.py

One RiskEngine and one CircuitBreaker per SLOT (symbol x strategy), instead of
one per account.

WHY (implementation/PER-SLOT-RISK-DESIGN-2026-09-19.md)
------------------------------------------------------
The portfolio backtest and the live bot ran every slot through a single
RiskEngine, so a slot's trades depended on what the other slots had been doing:
one slot's losses latched the shared daily/weekly drawdown pause for all of
them, its open risk shrank the others' position sizes, and its entries used up a
shared daily-trade and open-position budget. A single-symbol backtest had that
engine to itself. That is why a slot that looked good alone behaved differently
in a basket and differently again live.

Here each slot owns its limits. Nothing in a slot's book reads another slot's
state, so "run alone" and "run in a basket" are the same run.

What stays at the account level, because it is not a policy:
  * the balance / equity every slot sizes from,
  * the broker's margin pool and lot steps (position_sizer clamps each trade to
    max_margin_utilisation_pct of equity — a per-trade ceiling, so per-slot
    engines do not change it),
  * prop-firm limits, when the account IS a prop account: the firm enforces
    those on the whole account whatever we would prefer. One validator instance
    is shared by every slot's engine.
"""

from __future__ import annotations

from typing import Any

from backend.risk.engine import RiskEngine
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Keys a slot may not set: they describe the account or the simulation, not the
# slot's trading policy. Everything else in a risk config may be overridden per
# slot (risk %, trade caps, drawdown limits, targets, break-even, trailing, the
# drawdown brake).
ACCOUNT_ONLY_KEYS: frozenset[str] = frozenset({
    "prop_firm", "is_backtest", "mt5_account", "max_account_leverage",
    # position_sizer clamps each trade to this share of ACCOUNT equity, so it
    # describes the broker's margin pool, not a slot's trading policy.
    "max_margin_utilisation_pct",
    "mt5_order_deviation_points", "simulate_wicks", "stop_fill_model",
    "stop_fill_lambda", "stop_fill_seed", "simulate_backtest_only_exits",
})


def slot_key(symbol: str, strategy_id: str) -> str:
    """The key a slot is addressed by when it has no slot_id of its own."""
    return f"{str(symbol).upper()}|{strategy_id}"


def slot_overrides_from(slot: Any) -> dict[str, Any]:
    """An InstrumentSlot's own risk settings, as risk-config keys.

    `slot.risk` is the full per-slot block; the named InstrumentSlot fields
    (risk_per_trade_pct, max_trades_per_day, ...) are folded in on top, so a slot
    saved before the `risk` block existed keeps meaning what it meant.
    """
    out = dict(getattr(slot, "risk", None) or {})
    named = (
        ("risk_per_trade_pct", getattr(slot, "risk_per_trade_pct", None)),
        ("max_daily_trades", getattr(slot, "max_trades_per_day", None)),
        ("max_positions_per_symbol", getattr(slot, "max_positions_per_symbol", None)),
        ("tp1_rr", getattr(slot, "tp1_rr", None)),
        ("tp_count", getattr(slot, "tp_count", None)),
    )
    for key, value in named:
        if value is not None:
            out[key] = value
    return out


def resolve_slot_risk_config(
    base: dict[str, Any],
    strategy_id: str,
    *,
    overrides: dict[str, Any] | None = None,
    use_strategy_exit_defaults: bool = True,
    state_file: str | None = None,
) -> dict[str, Any]:
    """The complete risk config ONE slot trades under.

    Layered, lowest first:
      1. `base` — the account's defaults (a new slot starts from these),
      2. the strategy's measured exits (strategy_defaults.OVERRIDABLE), unless
         the caller turned that off or set the field itself,
      3. `overrides` — this slot's own settings,
      4. the strategy's measured `min_rr`, which may only LOWER the gate.

    Every path (single backtest, portfolio leg, live) resolves a slot through
    this one function, so the same slot means the same thing on all three.
    """
    from backend.strategies.strategy_defaults import (
        OVERRIDABLE,
        get_strategy_defaults,
        measured_min_rr_by_strategy,
    )

    cfg: dict[str, Any] = dict(base or {})
    over = {k: v for k, v in (overrides or {}).items()
            if v is not None and k not in ACCOUNT_ONLY_KEYS}

    if use_strategy_exit_defaults:
        for key, value in get_strategy_defaults(strategy_id).items():
            if key in OVERRIDABLE and key not in over:
                cfg[key] = value

    cfg.update(over)
    cfg["strategy_id"] = strategy_id
    cfg["min_rr_by_strategy"] = measured_min_rr_by_strategy([strategy_id])
    if state_file:
        cfg["cb_state_file"] = state_file
    return cfg


class SlotBook:
    """The slots' engines, addressed by slot key.

    `base_config` is the account's config; `slot_configs` holds each slot's
    resolved config (from `resolve_slot_risk_config`). A key with no config of
    its own falls back to the base — so a caller that knows nothing about slots
    still gets one working engine per slot rather than a shared one.
    """

    # Live counters that must survive a settings change: they describe trades
    # this slot has actually taken, not what it is allowed to take.
    _CARRIED_STATE = ("daily_trades_count", "daily_pnl", "weekly_pnl", "open_positions_by_symbol",
                      "open_positions_by_slot", "losses_today_by_slot", "active_groups", "is_paused",
                      "pause_reason", "last_reset_day", "last_reset_week", "_day_start_balance",
                      "_week_start_balance", "_last_known_balance", "_cumulative_pnl",
                      "_cumulative_pnl_at_last_balance", "last_trade_closed_time", "cum_r", "peak_r")

    def __init__(self, base_config: dict[str, Any], slot_configs: dict[str, dict[str, Any]] | None = None,
                 *, prop_firm_validator: Any = None, is_backtest: bool | None = None):
        self.base_config = dict(base_config or {})
        if is_backtest is not None:
            self.base_config["is_backtest"] = bool(is_backtest)
        self.slot_configs: dict[str, dict[str, Any]] = dict(slot_configs or {})
        self.prop_firm_validator = prop_firm_validator
        self._engines: dict[str, RiskEngine] = {}
        # slot key -> the symbol it trades, for routing a close back to its slot
        self.slot_symbols: dict[str, str] = {}

    # ── configs ───────────────────────────────────────────────────────────
    def config_for(self, key: str) -> dict[str, Any]:
        return self.slot_configs.get(key) or self.base_config

    def set_slot_config(self, key: str, config: dict[str, Any], *, symbol: str | None = None,
                        preserve_state: bool = True) -> None:
        """Register a slot's config.

        An engine already built for this key is rebuilt on next use. When
        `preserve_state` (live), the new breaker inherits the old one's counters,
        so editing a limit never forgets today's trades or the open positions.
        """
        if symbol:
            self.slot_symbols[key] = symbol
        if self.slot_configs.get(key) == config:
            return
        self.slot_configs[key] = config
        old = self._engines.pop(key, None)
        if old is not None and preserve_state:
            new = self.engine(key)
            for field in self._CARRIED_STATE:
                if hasattr(old.circuit, field):
                    setattr(new.circuit, field, getattr(old.circuit, field))
            logger.info(f"[SLOT] {key}: risk settings changed — breaker rebuilt, live counters kept.")

    # ── engines ───────────────────────────────────────────────────────────
    def engine(self, key: str) -> RiskEngine:
        """This slot's engine, built on first use and kept for the run."""
        eng = self._engines.get(key)
        if eng is None:
            cfg = dict(self.config_for(key))
            if self.base_config.get("is_backtest"):
                cfg["is_backtest"] = True
            eng = RiskEngine(cfg)
            if self.base_config.get("is_backtest"):
                eng.is_backtesting = True
            if self.prop_firm_validator is not None:
                eng.prop_firm_validator = self.prop_firm_validator
            self._engines[key] = eng
        return eng

    def built(self) -> dict[str, RiskEngine]:
        """The engines built so far — slots that never saw a signal are absent."""
        return dict(self._engines)

    # ── live: account-wide events fanned out to every slot ────────────────
    def circuits_by_key(self) -> dict[str, Any]:
        """Every slot's breaker, keyed by slot, building any that is missing."""
        for key in list(self.slot_configs):
            self.engine(key)
        return {key: eng.circuit for key, eng in self._engines.items()}

    def circuits(self) -> list[Any]:
        """Every slot's breaker, building one per configured slot if needed."""
        return list(self.circuits_by_key().values())

    def note_account_balance(self, balance: float, account_id: int | None = None) -> bool:
        """Tell every slot what the account is worth now.

        The balance is the account's, so a deposit, withdrawal or account switch
        has to re-baseline every slot's drawdown denominators, not just one.
        """
        reset = False
        for circuit in self.circuits():
            try:
                reset = bool(circuit.note_account_balance(balance, account_id)) or reset
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"[SLOT] note_account_balance failed: {e}")
        return reset

    def reset_for_new_account(self, account_id: int | None) -> None:
        for circuit in self.circuits():
            circuit.reset_for_new_account(account_id)

    def reconcile_from_mt5(self, open_symbols: list[str]) -> None:
        """Re-point every slot's open-position counts at what MT5 actually holds.

        Keyed, never zipped: the engines are built lazily, so their order is not
        the order of `slot_configs`, and pairing them by position would hand one
        slot another slot's open positions.
        """
        for key, circuit in self.circuits_by_key().items():
            symbol = self.slot_symbols.get(key)
            circuit.reconcile_from_mt5([s for s in open_symbols if symbol is None or s == symbol])

    def route_close(self, symbol: str, pnl: float, close_time: Any = None) -> bool:
        """Book a close on the slot that opened it.

        The group it belongs to is the first place to look; failing that (a
        restart wiped the in-memory groups), the slot configured for that symbol
        takes it, and only when exactly one is — guessing between two slots on
        one symbol would put a loss on the wrong slot's record.
        """
        for engine in self._engines.values():
            groups = getattr(engine.circuit, "active_groups", {}) or {}
            if any(g.get("symbol") == symbol for g in groups.values()):
                engine.circuit.record_external_close(symbol, pnl, close_time)
                return True
        candidates = [k for k, sym in self.slot_symbols.items() if sym == symbol]
        if len(candidates) == 1:
            self.engine(candidates[0]).circuit.record_external_close(symbol, pnl, close_time)
            return True
        logger.warning(f"[SLOT] close on {symbol} matched {len(candidates)} slots — not booked to any")
        return False

    # ── reporting ─────────────────────────────────────────────────────────
    def circuit_summary(self) -> dict[str, Any]:
        """Paused checks across the book, plus the reason each slot last gave.

        The account has no breaker of its own any more, so "was it paused?" is
        answered per slot and summed for the run's headline number.
        """
        per_slot: dict[str, Any] = {}
        total_paused = 0
        last_reason = ""
        for key, eng in self._engines.items():
            circuit = eng.circuit
            total_paused += int(getattr(circuit, "paused_bars", 0) or 0)
            reason = getattr(circuit, "last_pause_reason", "") or ""
            if reason:
                last_reason = f"{key}: {reason}"
            per_slot[key] = {
                "paused_checks": int(getattr(circuit, "paused_bars", 0) or 0),
                "last_pause_reason": reason,
                "daily_trades": int(getattr(circuit, "daily_trades_count", 0) or 0),
                "realised_r": round(float(getattr(circuit, "cum_r", 0.0) or 0.0), 3),
                "drawdown_r": round(float(getattr(circuit, "drawdown_r", lambda: 0.0)()), 3),
            }
        return {"paused_checks": total_paused, "last_pause_reason": last_reason, "by_slot": per_slot}
