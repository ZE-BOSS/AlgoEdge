"""
backend/risk/live_risk_config.py

The risk config a live trade runs under — built ONCE, used for the entry
(bot_service -> RiskEngine.evaluate_signal) and for managing the position
afterwards (position_manager -> exit_replay).

It used to be a ~120-line dict literal inside bot_service's scan loop, and the
position manager did not use it at all: break-even and trailing read the global
RiskParams, so a strategy's measured exits (applied to the entry here, and by
both backtest engines to the whole trade) never reached live management.
"""

from __future__ import annotations

from typing import Any


def build_live_risk_config(config: Any, strategy_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(risk_config, measured exit defaults that were applied) for `strategy_id`."""
    from backend.risk.multi_tp import slot_overrides_from_config
    from backend.strategies.strategy_defaults import get_slot_tp1_rr_defaults

    risk = config.risk
    slot_maps = slot_overrides_from_config(getattr(config, "instrument_slots", None))
    risk_config: dict[str, Any] = {
        "risk_per_trade_pct": risk.risk_per_trade_pct,
        "min_rr": risk.min_rr,
        "tp1_rr": risk.tp1_rr,
        "tp2_rr": risk.tp2_rr,
        "tp3_rr": risk.tp3_rr,
        "tp4_rr": risk.tp4_rr,
        "tp5_rr": risk.tp5_rr,
        "tp_count": risk.tp_count,
        "tp_splits": risk.tp_splits,
        # [17.1]/[18.3] Measured per-symbol R:R first, then the slot's own setting.
        "tp1_rr_overrides_by_slot": {**get_slot_tp1_rr_defaults(), **slot_maps["tp1_rr_overrides_by_slot"]},
        **{k: v for k, v in slot_maps.items() if k != "tp1_rr_overrides_by_slot"},
        "multi_position_mode": True,
        "max_daily_drawdown_pct": risk.max_daily_drawdown_pct,
        "max_weekly_drawdown_pct": risk.max_weekly_drawdown_pct,
        "max_daily_trades": risk.max_daily_trades,
        "max_concurrent_positions": risk.max_concurrent_positions,
        "max_positions_per_symbol": risk.max_positions_per_symbol,
        "prop_firm": {
            "account_mode": getattr(config.prop_firm, "account_mode", "personal"),
            "max_lot_sizes": getattr(config.prop_firm, "max_lot_sizes", {}),
            "initial_balance": getattr(config.prop_firm, "initial_balance", 10000.0),
            "default_max_lot": getattr(config.prop_firm, "default_max_lot", None),
            "max_positions_per_symbol": getattr(config.prop_firm, "max_positions_per_symbol", 5),
            "max_total_positions": getattr(config.prop_firm, "max_total_positions", 13),
            "trading_day_rule": getattr(config.prop_firm, "trading_day_rule", "PROFIT_PCT"),
            "trading_day_profit_pct": getattr(config.prop_firm, "trading_day_profit_pct", 0.5),
        },
        "max_risk_hard_cap_pct": getattr(risk, "max_risk_hard_cap_pct", 3.0),
        "target_profit_enabled": risk.target_profit_enabled,
        "max_daily_profit": risk.max_daily_profit,
        "max_weekly_profit": risk.max_weekly_profit,
        "be_trigger_rr": risk.be_trigger_rr,
        "be_buffer_pips": risk.be_buffer_pips,
        "be_buffer_atr_mult": risk.be_buffer_atr_mult,
        "trail_method_tp1": getattr(risk, "trail_method_tp1", "NONE"),
        "trail_method_tp2": risk.trail_method_tp2,
        "trail_method_tp3": risk.trail_method_tp3,
        "trail_method_tp4": risk.trail_method_tp4,
        "trail_method_tp5": risk.trail_method_tp5,
        "atr_trail_multiplier": getattr(risk, "atr_trail_multiplier", 1.5),
        **{f"atr_trail_multiplier_tp{k}": getattr(risk, f"atr_trail_multiplier_tp{k}", 1.5) for k in range(1, 6)},
        "trail_pips": getattr(risk, "trail_pips", 15.0),
        "trail_pct": getattr(risk, "trail_pct", 0.5),
        "trail_activation_rr": getattr(risk, "trail_activation_rr", 1.0),
        "trail_step_pips": getattr(risk, "trail_step_pips", 5.0),
        "trail_structure_bars": getattr(risk, "trail_structure_bars", 3),
        "max_margin_utilisation_pct": getattr(risk, "max_margin_utilisation_pct", None),
        "max_account_leverage": getattr(risk, "max_account_leverage", None),
        "min_deployable_risk_pct": getattr(risk, "min_deployable_risk_pct", 0.0),
        "min_stop_spread_multiple": getattr(risk, "min_stop_spread_multiple", None),
        "min_stop_cost_multiple": getattr(risk, "min_stop_cost_multiple", 0.0),
        "confluence_risk_tiers": getattr(risk, "confluence_risk_tiers", None),
        "reject_below_confluence": getattr(risk, "reject_below_confluence", True),
        "post_split_risk_tolerance_pct": getattr(risk, "post_split_risk_tolerance_pct", 5.0),
        "exit_slippage_pips": getattr(risk, "exit_slippage_pips", None),
        "open_risk_weight": getattr(risk, "open_risk_weight", 0.5),
        "allow_pyramiding": getattr(risk, "allow_pyramiding", False),
        "min_bars_between_entries": getattr(risk, "min_bars_between_entries", 0),
        "min_sl_pips": getattr(risk, "min_sl_pips", 0.0),
        "vol_target_annual_pct": getattr(risk, "vol_target_annual_pct", None),
        "vol_target_lookback_bars": getattr(risk, "vol_target_lookback_bars", 20),
        "vol_target_min_scale": getattr(risk, "vol_target_min_scale", 0.5),
        "vol_target_max_scale": getattr(risk, "vol_target_max_scale", 2.0),
        "sizing_basis": getattr(risk, "sizing_basis", "STATIC"),
        "be_spread_multiple": getattr(risk, "be_spread_multiple", 2.0),
        "trail_require_be_first": getattr(risk, "trail_require_be_first", False),
        "be_mode": getattr(risk, "be_mode", "EITHER"),
        "be_trigger_tp_level": getattr(risk, "be_trigger_tp_level", 1),
        "trail_mode": getattr(risk, "trail_mode", "RR"),
        "trail_trigger_rr": getattr(risk, "trail_trigger_rr", 1.5),
        "trail_trigger_tp_level": getattr(risk, "trail_trigger_tp_level", 1),
        "tp_volume_pcts": getattr(risk, "tp_volume_pcts", None),
        "max_cluster_risk_pct": getattr(risk, "max_cluster_risk_pct", 0.0),
        "max_net_direction_risk_pct": getattr(risk, "max_net_direction_risk_pct", 0.0),
        "symbol_cluster_overrides": getattr(risk, "symbol_cluster_overrides", None),
        "strategy_risk_budget_pct": getattr(risk, "strategy_risk_budget_pct", None),
        # [2.15] Per-strategy TP1 RR override — see DriftJumpAlphaParams.tp1_rr_override.
        "tp1_rr_overrides_by_strategy": (
            {"DriftJumpAlpha": config.drift_jump_alpha.tp1_rr_override}
            if getattr(getattr(config, "drift_jump_alpha", None), "tp1_rr_override", None) is not None
            else {}
        ),
        "strategy_id": strategy_id,
    }

    # Measured per-strategy exits, where the saved setting is still the shipped
    # RiskParams default. A value equal to its default cannot be told apart from
    # "not set", so an explicit "Either" loses to a strategy's measured NONE —
    # `use_strategy_exit_defaults = False` is how a user makes their own exits
    # apply to every strategy (the Backtester's switch of the same name).
    applied: dict[str, Any] = {}
    if not getattr(risk, "use_strategy_exit_defaults", True):
        return risk_config, applied
    try:
        from backend.core.config_schema import RiskParams
        from backend.strategies.strategy_defaults import get_strategy_defaults

        base = RiskParams()
        for key, value in get_strategy_defaults(strategy_id).items():
            if key == "session_filter_enabled":
                continue  # a strategy-params field, applied at engine build
            if hasattr(base, key) and getattr(risk, key, None) == getattr(base, key):
                risk_config[key] = value
                applied[key] = value
    except Exception:
        pass
    return risk_config, applied
