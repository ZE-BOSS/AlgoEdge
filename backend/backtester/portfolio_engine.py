"""
backend/backtester/portfolio_engine.py

Unified Portfolio Backtesting Engine.
Simulates multiple symbols chronologically on a single global timeline.
"""
import uuid
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.analytics.reports import generate_risk_report
from backend.risk.engine import RiskEngine
from backend.risk.multi_tp import TPLevel, _is_buy
from backend.risk.position_sizer import get_pip_size, get_symbol_info
from backend.risk.prop_firm_validator import PropFirmValidator
from backend.utils.logger import get_logger
from backend.utils.trade_grouper import group_trades
from backend.utils.timeutils import detect_session
import pytz
from backend.backtester.engine import (
    _epoch_to_iso,
    _calc_duration_minutes,
    _validate_position,
    _to_epoch_seconds,
    _resolve_sl_tp_hit,
    _gap_adjusted_fill_price,
    CostModelMixin,
    validate_at_fill_price,
    _breakeven_stop,
)
from backend.backtester.report import apply_bar_level_drawdown, apply_leg_level_hit_rates

logger = get_logger(__name__)

class PortfolioBacktestEngine(CostModelMixin):
    def __init__(self, risk_config: dict[str, Any]):
        # Bug 5 fix: mark as backtest so CircuitBreaker skips cb_state.json
        # load/save on every position close (was causing file I/O on every bar,
        # a major performance bottleneck with many symbols).
        risk_config = risk_config.copy()
        risk_config["is_backtest"] = True
        self.risk_engine = RiskEngine(risk_config)
        self.risk_config = risk_config
        prop_firm_config = risk_config.get("prop_firm", {})
        if isinstance(prop_firm_config, dict):
            prop_firm_config = prop_firm_config.copy()
            prop_firm_config["is_backtesting"] = True
        else:
            # Bug 14 fix: Deep copy the dataclass object so we don't leak backtest
            # state (is_backtesting=True) into the live bot's config.
            import copy
            prop_firm_config = copy.deepcopy(prop_firm_config)
            setattr(prop_firm_config, "is_backtesting", True)
            
        self.prop_firm_validator = PropFirmValidator(prop_firm_config)
        self.risk_engine.prop_firm_validator = self.prop_firm_validator
        
        self.trades = []
        self.open_positions = []
        self.equity_curve = []
        self.invalid_signals = 0
        self.rejection_funnel = {
            "total_evaluated": 0,
            "strategy_rejections": {},
            "risk_rejections": {},
            # Task 1 parity with engine.py: signals that cleared every pre-trade
            # gate but failed re-validation against the ACTUAL FILL price.
            "fill_rejections": {},
            "errors": 0,
            "approved": 0
        }
        # [I4] Mirrors engine.py's blocked_signals — see that file for rationale.
        self.blocked_signals: list[dict[str, Any]] = []
        self._blocked_signals_cap = 500
        self.run_logs = []
        # ── Simulation costs (Task 2) ──
        # Resolved lazily PER SYMBOL via CostModelMixin._costs_for(): explicit user
        # values win, anything unset ("auto"/absent) is sourced from live MT5 or
        # asset-class broker defaults instead of the old silent 0.0. Per-symbol
        # resolution matters far more here than in the single-symbol engine, since
        # a portfolio can mix FX, indices and synthetics with wildly different
        # spread/commission/swap profiles.
        self._init_cost_model()
        # Pin instrument data for the whole run — see the matching call in
        # engine.py for the sizing/PnL divergence and non-determinism this prevents.
        from backend.risk.position_sizer import freeze_symbol_info
        freeze_symbol_info()
        # Wick simulation flag
        self._simulate_wicks = bool(risk_config.get("simulate_wicks", True))

    def _calc_pnl(
        self,
        direction: str,
        entry: float,
        exit_price: float,
        volume: float,
        symbol: str,
        entry_time: Any = None,
        exit_time: Any = None,
    ) -> float:
        """
        Calculate P&L with simulation costs applied.
        Matches engine.py _calc_pnl exactly (slippage, spread, commission and
        overnight swap deducted, each resolved per-symbol — see
        backend.backtester.engine.resolve_effective_costs).

        Swap is only charged when BOTH timestamps are supplied, i.e. on real
        closes; floating-equity marks pass neither.
        """
        costs = self._costs_for(symbol)
        info = get_symbol_info(symbol)
        tick_value = info.get("tick_value", 1.0)
        tick_size  = info.get("tick_size",  0.00001)
        source     = info.get("source", "UNKNOWN")
        pip_size   = get_pip_size(symbol)

        if source == "DEFAULT":
            logger.warning(f"[_calc_pnl] {symbol}: PnL computed with DEFAULT fallback — may be incorrect!")

        if tick_size == 0 or tick_value == 0:
            logger.warning(f"[_calc_pnl] {symbol}: tick_size or tick_value is zero — returning 0 PnL.")
            return 0.0

        value_per_unit_move = tick_value / tick_size

        # Apply slippage: shift effective entry against the trade direction
        slippage_pips = costs["slippage_pips"]
        if slippage_pips > 0 and pip_size > 0:
            slippage_price = slippage_pips * pip_size
            if _is_buy(direction):
                entry = entry + slippage_price  # BUY fills higher (worse)
            else:
                entry = entry - slippage_price  # SELL fills lower (worse)

        price_diff = exit_price - entry
        raw_pnl = price_diff * value_per_unit_move * volume
        if not _is_buy(direction):
            raw_pnl = -raw_pnl

        # Deduct spread cost (pip cost of crossing bid/ask at entry)
        spread_pips = costs["spread_pips"]
        if spread_pips > 0 and pip_size > 0:
            spread_cost = spread_pips * pip_size * value_per_unit_move * volume
            raw_pnl -= spread_cost

        # Deduct round-turn commission
        commission_per_lot = costs["commission_per_lot"]
        if commission_per_lot > 0:
            raw_pnl -= commission_per_lot * volume

        # Task 3: overnight financing (signed, MT5 convention: negative = charge)
        if entry_time is not None and exit_time is not None:
            raw_pnl += self._swap_cost(direction, volume, symbol, entry_time, exit_time)

        return raw_pnl

    def _record_blocked(self, sig: dict[str, Any], current_time: Any, gate: str, reason: str = "") -> None:
        """[I4] Mirrors engine.py::_record_blocked — see that file for rationale."""
        if len(self.blocked_signals) >= self._blocked_signals_cap:
            return
        try:
            time_iso = _epoch_to_iso(current_time)
        except Exception:
            time_iso = str(current_time)
        self.blocked_signals.append({
            "time": time_iso,
            "symbol": sig.get("symbol", ""),
            "direction": sig.get("direction", ""),
            "entry_price": sig.get("entry_price"),
            "stop_loss": sig.get("stop_loss"),
            "gate": gate,
            "reason": reason,
        })

    def run(
        self,
        portfolio_data: dict[str, pd.DataFrame],
        portfolio_signals: dict[str, list[dict[str, Any]]],
        initial_balance: float = 10000.0,
        portfolio_data_m15: dict[str, pd.DataFrame] = None,
        portfolio_data_m5: dict[str, pd.DataFrame] = None,
        symbol_map: dict[str, str] | None = None,
        progress_cb: Any = None,
    ) -> dict[str, Any]:
        """
        [12.8/Part14] `portfolio_data`/`portfolio_signals` are keyed by a
        DEDUP/CACHE KEY, not necessarily the real tradeable symbol — this is
        what lets two InstrumentSlots trading the SAME symbol under different
        strategies coexist in one portfolio run (each gets its own candle
        series, e.g. different primary timeframes) without their entries
        overwriting each other in these dicts. `symbol_map` (cache_key ->
        real_symbol) resolves the real symbol wherever one is genuinely
        needed (cost/pip-size lookups, MT5 calls, trade records) — every
        POSITION/SIGNAL dict's own "symbol" field is ALWAYS the real symbol
        regardless of what its cache key looks like (see `_cache_key`
        threaded onto both below). `symbol_map=None` (every caller before
        12.8) makes cache_key == real_symbol everywhere, reproducing today's
        behaviour exactly — this parameter is purely additive.
        """
        symbol_map = symbol_map or {}

        def _real_symbol(cache_key: str) -> str:
            return symbol_map.get(cache_key, cache_key)

        balance = initial_balance
        self.trades = []
        self.open_positions = []
        self.equity_curve = [balance]
        
        self.prop_firm_validator.is_breached = False
        self.prop_firm_validator.breach_reason = ""
        self.prop_firm_validator._breach_logged = False
        self.prop_firm_validator._alerts_sent = set()
        
        logger.info("[PORTFOLIO] ═══ Starting global portfolio engine ═══")

        # Task 2: resolve and log transaction costs per symbol up-front, so the
        # run log states which costs were applied and their provenance
        # (USER / MT5 / ASSET_CLASS_DEFAULT) before any trade is simulated.
        for _sym in portfolio_data.keys():
            self._costs_for(_real_symbol(_sym))

        all_timestamps = set()
        for sym, df in portfolio_data.items():
            if 'time' in df.columns:
                all_timestamps.update(df['time'].values)
            else:
                all_timestamps.update(df.index.values)
                
        global_timeline = sorted(list(all_timestamps))
        
        symbol_cache = {}
        for sym, df in portfolio_data.items():
            if 'time' not in df.columns:
                df['time'] = df.index
            
            bar_dict = df.set_index('time').to_dict('index')
            
            highs_arr = df["high"].values.astype(float)
            lows_arr = df["low"].values.astype(float)
            closes_arr = df["close"].values.astype(float)
            atr_period = 14

            # Bug 8 fix: Vectorized ATR via pandas rolling mean — replaces the
            # O(N) Python loop that used np.mean on a slice every bar (~10x slower).
            highs_s  = pd.Series(highs_arr)
            lows_s   = pd.Series(lows_arr)
            closes_s = pd.Series(closes_arr)
            prev_c   = closes_s.shift(1).fillna(closes_s.iloc[0])
            tr_s = pd.concat(
                [highs_s - lows_s, (highs_s - prev_c).abs(), (lows_s - prev_c).abs()],
                axis=1,
            ).max(axis=1)
            atr_series = tr_s.rolling(atr_period, min_periods=1).mean()
            atr_array  = atr_series.values

            time_vals = df['time'].values
            atr_dict = dict(zip(time_vals, atr_array))

            # Bug 7 fix: Swing-point cache — algorithm is identical to engine.py's
            # single-symbol cache (O(N × lookback × sw_len)), keyed by timestamp.
            # The previous portfolio code computed the same loop PLUS an extra outer
            # loop over (swing_lookback, len(df)) that re-scanned already-processed
            # bars, causing roughly O(N²) behaviour for large datasets.
            sw_len = self.risk_config.get("trail_structure_bars", self.risk_config.get("swing_length", 5))
            swing_lookback = 20
            swing_dict: dict = {}
            for i in range(swing_lookback, len(df)):
                points = []
                for j in range(max(sw_len, i - swing_lookback), i - sw_len):
                    if j - sw_len < 0:
                        continue
                    window_h = highs_arr[j - sw_len:j + sw_len + 1]
                    window_l = lows_arr[j - sw_len:j + sw_len + 1]
                    if highs_arr[j] == window_h.max():
                        points.append({"type": "HIGH", "price": float(highs_arr[j])})
                    if lows_arr[j] == window_l.min():
                        points.append({"type": "LOW", "price": float(lows_arr[j])})
                if points:
                    swing_dict[time_vals[i]] = points

            symbol_cache[sym] = {
                "bars": bar_dict,
                "atr": atr_dict,
                "swings": swing_dict,
            }
            
        all_signals = []
        for sym, sigs in portfolio_signals.items():
            for sig in sigs:
                # [12.8] `sym` here is the dedup/cache key, which is only
                # guaranteed to equal the real symbol when no two slots share
                # one. Preserve whatever real symbol the signal already
                # carries (set by the caller at signal-creation time) instead
                # of clobbering it — `_cache_key` carries the grouping key
                # for this engine's own bar/ATR/swing cache lookups.
                sig.setdefault("symbol", _real_symbol(sym))
                sig["_cache_key"] = sym
                all_signals.append(sig)
        
        all_signals.sort(key=lambda x: float(x.get("time", float("inf"))))
        signal_idx = 0
        
        breach_days = set()
        
        # [17.2] Portfolio runs previously reported NOTHING during the global
        # simulation — the UI sat at 85% for the whole thing, the same symptom
        # B4/B5 fixed on the single-symbol path. Fire ~200 times regardless of
        # timeline length; the caller rate-limits, so over-supply is free.
        _n_steps = len(global_timeline)
        _prog_stride = max(1, _n_steps // 200)

        for _step_i, current_time in enumerate(global_timeline):
            if progress_cb is not None and (_step_i % _prog_stride) == 0:
                try:
                    progress_cb(_step_i, _n_steps)
                except Exception:
                    pass
            current_timestamp = float(current_time)
            
            # 1. Update floating equity and check limits
            open_pnl = 0.0
            for pos in self.open_positions:
                sym = pos.get("symbol")  # real symbol — cost/pnl functions
                _ckey = pos.get("_cache_key", sym)  # [12.8] bar-cache lookup key
                if _ckey in symbol_cache and current_time in symbol_cache[_ckey]["bars"]:
                    pos["_last_known_close"] = symbol_cache[_ckey]["bars"][current_time]["close"]
                last_close = pos.get("_last_known_close", pos["entry_price"])
                open_pnl += self._calc_pnl(pos["direction"], pos["entry_price"], last_close, pos["volume"], sym)
            
            current_time_dt = datetime.fromtimestamp(current_timestamp, timezone.utc)
            self.prop_firm_validator.update_equity_balance(balance + open_pnl, balance, current_time_dt)
            if self.prop_firm_validator.is_breached:
                breach_date = current_time_dt.date().isoformat()
                if breach_date not in breach_days:
                    logger.warning(f"[PROP FIRM MONITOR] Breach detected on {breach_date}: {self.prop_firm_validator.breach_reason} — continuing")
                    breach_days.add(breach_date)
                
            # 2. Process open positions
            closed_this_bar = []
            tp1_hit_groups = set()

            # Pre-pass: determine which groups will have TP1 close THIS bar so
            # that siblings (TP2/TP3) can defer their own SL/TP check to the
            # next bar. Without this, a TP2 position can be evaluated and closed
            # at its original SL/TP price on the SAME bar TP1 closes — BEFORE
            # the tp1_hit_groups BE block (lines below) has moved the SL to
            # entry+buffer. The result was TP2/TP3 showing full-TP profit (or
            # full-SL loss) instead of the near-zero BE_SL exit expected.
            _tp1_closing_this_bar: set = set()
            for _p in self.open_positions:
                if _p.get("tp_level") != 1:
                    continue
                _ckey = _p.get("_cache_key", _p.get("symbol"))  # [12.8]
                if _ckey not in symbol_cache or current_time not in symbol_cache[_ckey]["bars"]:
                    continue
                _bar = symbol_cache[_ckey]["bars"][current_time]
                _open_p_pp = _bar.get("open", _bar["close"])
                # Item 3.7: reuse the same ambiguity-resolved tp_hit determination
                # as the real close logic below (shared helper — also matches
                # engine.py's single-symbol pre-pass).
                _sl_hit_pp, _tp_hit_pp = _resolve_sl_tp_hit(
                    _p["direction"], _open_p_pp, _bar["high"], _bar["low"],
                    _p["stop_loss"], _p["take_profit"], self._simulate_wicks,
                )
                if _tp_hit_pp:
                    _tp1_closing_this_bar.add(_p.get("group_id"))

            for pos in self.open_positions[:]:
                sym = pos.get("symbol")  # real symbol
                _ckey = pos.get("_cache_key", sym)  # [12.8]
                if _ckey not in symbol_cache or current_time not in symbol_cache[_ckey]["bars"]:
                    continue # No tick for this symbol at this time

                bar = symbol_cache[_ckey]["bars"][current_time]
                current_price = bar["close"]
                high = bar["high"]
                low = bar["low"]
                open_p = bar.get("open", current_price)

                if pos["direction"] == "BUY":
                    pos["highest_price"] = max(pos.get("highest_price", pos["entry_price"]), high)
                else:
                    pos["lowest_price"] = min(pos.get("lowest_price", pos["entry_price"]), low)

                pos["bars_held"] = pos.get("bars_held", 0) + 1
                symbol_upper = sym.upper()
                is_crashboom = "CRASH" in symbol_upper or "BOOM" in symbol_upper
                if is_crashboom and pos["bars_held"] >= 400:
                    pos["exit_price"] = current_price
                    pos["exit_reason"] = "TIME_LIMIT"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], sym, pos.get("entry_time"), current_time)
                    closed_this_bar.append(pos)
                    continue

                # Task 4: hard_close_time now actually reaches the position dict
                # (see the new_pos construction below) — before, it was never copied
                # out of sig["metadata"], so this lookup always returned None and the
                # whole session-end rule was dead code in this engine too.
                hard_close_time = pos.get("hard_close_time")
                if hard_close_time:
                    import pytz  # noqa: F811 — kept for compatibility with older references
                    _et_tz = pytz.timezone("America/New_York")
                    et = current_time_dt.astimezone(_et_tz)
                    time_str = et.strftime("%H:%M")
                    # Also close once the ET calendar date has advanced past entry's:
                    # a bare "HH:MM >=" compare wraps at ET midnight and would let a
                    # position run a further ~24h. Mirrors engine.py.
                    _entry_et_date = pos.get("_entry_et_date")
                    if _entry_et_date is None:
                        _entry_secs = _to_epoch_seconds(pos.get("entry_time"))
                        _entry_et_date = (
                            datetime.fromtimestamp(_entry_secs, tz=timezone.utc).astimezone(_et_tz).date()
                            if _entry_secs is not None else et.date()
                        )
                        pos["_entry_et_date"] = _entry_et_date
                    if time_str >= hard_close_time or et.date() > _entry_et_date:
                        pos["exit_price"] = current_price
                        pos["exit_reason"] = "SESSION_END"
                        pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], sym, pos.get("entry_time"), current_time)
                        closed_this_bar.append(pos)
                        continue

                # Skip SL/TP evaluation for TP2/TP3 siblings whose TP1 leg
                # closes on THIS SAME BAR. Their BE stop has not been applied
                # yet (that happens in the tp1_hit_groups block after this loop).
                # Evaluating them now would close them at the wrong price.
                # They will be re-evaluated on the next bar with BE stop in place.
                if pos.get("tp_level", 1) != 1 and pos.get("group_id") in _tp1_closing_this_bar:
                    # Still update MAE/MFE and trailing-stop state for this bar.
                    pip_size = get_pip_size(sym)
                    if pos["direction"] == "BUY":
                        adverse = pos["entry_price"] - low
                        favorable = high - pos["entry_price"]
                    else:
                        adverse = high - pos["entry_price"]
                        favorable = pos["entry_price"] - low
                    pos["mae_pips"] = max(pos.get("mae_pips", 0), adverse / pip_size if pip_size else 0)
                    pos["mfe_pips"] = max(pos.get("mfe_pips", 0), favorable / pip_size if pip_size else 0)
                    continue  # Defer SL/TP to next bar

                # Resolve SL/TP hits and same-bar ambiguity via the shared helper
                # (items 3.2/3.3/3.7 — identical logic used by engine.py and the
                # TP1 pre-pass above, so both backtest engines behave identically
                # on ambiguous bars and default to the conservative SL-favoring
                # resolution).
                _raw_sl_hit = (low <= pos["stop_loss"]) if pos["direction"] == "BUY" else (high >= pos["stop_loss"])
                _raw_tp_hit = (high >= pos["take_profit"]) if pos["direction"] == "BUY" else (low <= pos["take_profit"])
                if _raw_sl_hit and _raw_tp_hit:
                    pos["same_bar_ambiguous"] = True  # Tag for reporting
                sl_hit, tp_hit = _resolve_sl_tp_hit(
                    pos["direction"], open_p, high, low,
                    pos["stop_loss"], pos["take_profit"], self._simulate_wicks,
                )

                # Update MAE/MFE using this bar's high/low BEFORE the exit checks below,
                # so the excursion on the closing bar itself is captured — see engine.py
                # for the single-symbol version of this same fix.
                if pos["direction"] == "BUY":
                    adverse = pos["entry_price"] - low
                    favorable = high - pos["entry_price"]
                else:
                    adverse = high - pos["entry_price"]
                    favorable = pos["entry_price"] - low

                pip_size = get_pip_size(sym)
                pos["mae_pips"] = max(pos.get("mae_pips", 0), adverse / pip_size if pip_size else 0)
                pos["mfe_pips"] = max(pos.get("mfe_pips", 0), favorable / pip_size if pip_size else 0)

                if sl_hit:
                    # Item 3.4: fill at the gapped open (± slippage) instead of a
                    # perfect SL fill when the bar's open already gapped past SL.
                    _sl_gapped = (open_p <= pos["stop_loss"]) if pos["direction"] == "BUY" else (open_p >= pos["stop_loss"])
                    if _sl_gapped:
                        pos["exit_price"] = _gap_adjusted_fill_price(pos["direction"], open_p, pos["stop_loss"], sym, self._costs_for(sym)["slippage_pips"])
                        pos["gap_fill"] = True
                    else:
                        pos["exit_price"] = pos["stop_loss"]
                    pos["exit_reason"] = "TRAIL_SL" if pos.get("trail_applied") else ("BE_SL" if pos.get("be_applied") else "SL")
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], sym, pos.get("entry_time"), current_time)
                    closed_this_bar.append(pos)
                    continue
                elif tp_hit:
                    _tp_gapped = (open_p >= pos["take_profit"]) if pos["direction"] == "BUY" else (open_p <= pos["take_profit"])
                    if _tp_gapped:
                        pos["exit_price"] = _gap_adjusted_fill_price(pos["direction"], open_p, pos["take_profit"], sym, self._costs_for(sym)["slippage_pips"])
                        pos["gap_fill"] = True
                    else:
                        pos["exit_price"] = pos["take_profit"]
                    pos["exit_reason"] = f"TP{pos.get('tp_level', 1)}"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], sym, pos.get("entry_time"), current_time)
                    closed_this_bar.append(pos)
                    if pos.get("tp_level") == 1:
                        tp1_hit_groups.add(pos.get("group_id"))
                    continue

                current_atr = symbol_cache[_ckey]["atr"].get(current_time, 0.0)  # [12.8]
                swing_points = symbol_cache[_ckey]["swings"].get(current_time, [])

                actions = self.risk_engine.manage_open_position(
                    pos, current_price,
                    atr_value=current_atr,
                    swing_points=swing_points,
                )
                for action in actions:
                    if action["action"] == "MODIFY_SL":
                        old_sl = pos["stop_loss"]
                        pos["stop_loss"] = action["new_sl"]
                        if action.get("reason") == "BREAKEVEN":
                            pos["be_applied"] = True
                        elif action.get("reason") == "TRAIL":
                            pos["trail_applied"] = True

            # [4.7/D8/F5] Was unconditional — now gated on be_mode, matching engine.py.
            _be_mode_cascade = self.risk_config.get("be_mode", "EITHER")
            if tp1_hit_groups and _be_mode_cascade in ("TP_HIT", "EITHER"):
                for pos in self.open_positions:
                    if pos.get("group_id") in tp1_hit_groups and pos not in closed_this_bar:
                        _sym = pos.get("symbol", "")
                        pip_size = get_pip_size(_sym)
                        # Market reference for this position's own symbol — the outer
                        # `current_price` belongs to whichever symbol the bar loop is on,
                        # which is not necessarily this position's.
                        _mkt = pos.get("_last_known_close", pos["entry_price"])
                        _pos_ckey = pos.get("_cache_key", _sym)  # [12.8]
                        _atr = symbol_cache.get(_pos_ckey, {}).get("atr", {}).get(current_time, 0.0)
                        # Clamped BE — see _breakeven_stop() in engine.py for why the clamp
                        # exists (it prevented a stop being placed beyond the market and
                        # force-filled as a phantom gap, fabricating ~+1R on every BE exit).
                        new_sl = _breakeven_stop(
                            direction=pos["direction"],
                            entry_price=pos["entry_price"],
                            current_price=_mkt,
                            pip_size=pip_size,
                            atr=_atr,
                            risk_config=self.risk_config,
                            spread_pips=self._costs_for(_sym)["spread_pips"],
                        )
                        if pos["direction"] == "BUY":
                            if new_sl > pos["stop_loss"]:
                                pos["stop_loss"] = new_sl
                                pos["be_applied"] = True
                        else:
                            if new_sl < pos["stop_loss"]:
                                pos["stop_loss"] = new_sl
                                pos["be_applied"] = True

            positions_to_remove = []
            for pos in closed_this_bar:
                pos["exit_time"] = current_time
                # Task 6: leg records kept status="OPEN" even after exit_price/
                # exit_reason/pnl/exit_time were all populated.
                pos["status"] = "CLOSED"
                try:
                    # current_time is drawn from global_timeline, which is
                    # built from df['time'].values (a numpy array) — so each
                    # element is a numpy.int64/float64, not a plain Python
                    # int/float. detect_session()'s isinstance(x,(int,float))
                    # check is False for numpy.int64, so it was silently
                    # returning "UNKNOWN" for every single exit (no exception
                    # thrown). sig_time at entry is explicitly float()-cast
                    # already, which is why entry_session was unaffected.
                    pos["session"] = detect_session(_to_epoch_seconds(current_time))
                except Exception:
                    pos["session"] = "UNKNOWN"
                pos["duration_minutes"] = _calc_duration_minutes(pos.get("entry_time"), pos.get("exit_time"))
                pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
                pos["exit_time_iso"] = _epoch_to_iso(pos.get("exit_time"))

                pos["exit_confirmations"] = [
                    f"Exit Reason: {pos.get('exit_reason', 'UNKNOWN')}",
                    f"Exit Price: {pos.get('exit_price', 0):.5f}",
                    f"PnL: ${pos.get('pnl', 0):.2f}",
                    f"Duration: {pos.get('duration_minutes', 0):.1f} min",
                    f"MAE: {pos.get('mae_pips', 0):.1f} pips",
                    f"MFE: {pos.get('mfe_pips', 0):.1f} pips",
                    f"BE Applied: {'Yes' if pos.get('be_applied') else 'No'}",
                    f"Trail Method: {pos.get('trail_method') or 'NONE'}",
                    f"Session: {pos.get('session', 'UNKNOWN')}",
                ]

                balance += pos.get("pnl", 0)
                pos["balance_after"] = balance

                # ── Group-level PnL accumulation (mirrors single-symbol engine.py pattern) ──
                # Only call on_backtest_position_closed when the last TP leg for a group closes.
                # (e.g., when a multi-TP position is still active). This avoids false
                # CB trips and ghost open-position counts.
                group_id_closed = pos.get("group_id", "unknown")
                remaining_legs = sum(
                    1 for p in self.open_positions
                    if p.get("group_id") == group_id_closed
                    and p not in positions_to_remove
                    and p != pos
                )
                if remaining_legs == 0:
                    group_pnl = sum(
                        p.get("pnl", 0) for p in self.trades
                        if p.get("group_id") == group_id_closed
                    ) + pos.get("pnl", 0)
                    # Item 3.6: pass symbol/lots too (matches engine.py's 5-arg call)
                    # so open_positions_by_symbol/open_lots_by_symbol actually
                    # decrement for the real symbol in portfolio backtests.
                    group_lots = sum(
                        p.get("volume", 0.0) for p in self.trades
                        if p.get("group_id") == group_id_closed
                    ) + pos.get("volume", 0.0)
                    if hasattr(self.risk_engine, "on_backtest_position_closed"):
                        self.risk_engine.on_backtest_position_closed(
                            group_id_closed, group_pnl, current_time,
                            pos.get("symbol", ""), group_lots,
                        )

                self.trades.append(pos)
                positions_to_remove.append(pos)

                self.run_logs.append({
                    "time": _epoch_to_iso(current_time),
                    "level": "INFO",
                    "category": "BACKTEST_LOG",
                    "message": f"Closed {pos['direction']} {pos.get('symbol', sym)} {pos.get('exit_reason')} | PnL: ${pos.get('pnl', 0):.2f}"
                })

                
            for p in positions_to_remove:
                if p in self.open_positions:
                    self.open_positions.remove(p)

            # 3. Process new signals
            while signal_idx < len(all_signals):
                sig = all_signals[signal_idx]
                sig_time = float(sig.get("time", float("inf")))
                if sig_time >= current_timestamp:
                    break
                signal_idx += 1

                symbol = sig.get("symbol")
                cache_key = sig.get("_cache_key", symbol)  # [12.8]
                sig_is_buy = _is_buy(sig.get("direction", "BUY"))

                # [12.5/12.8] Scoped to THIS SLOT (cache_key), not the bare
                # symbol — two different strategy slots on the same real
                # symbol must not block each other's entries.
                already_open = False
                for p in self.open_positions:
                    p_is_buy = _is_buy(p.get("direction", "BUY"))
                    if p.get("_cache_key", p.get("symbol")) == cache_key and p_is_buy == sig_is_buy:
                        already_open = True
                        break
                        
                if already_open:
                    self._record_blocked(sig, current_time_dt, "same_direction_already_open")
                    continue

                group_id = str(uuid.uuid4())[:8]
                sig["group_id"] = group_id

                self.rejection_funnel["total_evaluated"] += 1

                passed_gates = sig.get("metadata", {}).get("passed_gates", True)
                if not passed_gates:
                    reasons = sig.get("metadata", {}).get("rejection_reasons", [])
                    for r in reasons:
                        gate = r.split(":")[0] if ":" in r else "Unknown Strategy Rule"
                        self.rejection_funnel["strategy_rejections"][gate] = self.rejection_funnel["strategy_rejections"].get(gate, 0) + 1
                    self._record_blocked(sig, current_time_dt, f"strategy:{reasons[0].split(':')[0] if reasons else 'unknown'}", "; ".join(reasons))
                    continue

                # Prop Firm hard block — skip new signals when drawdown limit is breached (except in backtesting where we only flag)
                if self.prop_firm_validator.enabled and self.prop_firm_validator.is_breached and not getattr(self.prop_firm_validator, 'is_backtesting', False):
                    self.rejection_funnel["risk_rejections"]["prop_firm_drawdown_block"] = self.rejection_funnel["risk_rejections"].get("prop_firm_drawdown_block", 0) + 1
                    self._record_blocked(sig, current_time_dt, "prop_firm_drawdown_block")
                    continue

                approved, reason, tp_levels = False, "Error", []
                try:
                    # [4.2/D1] Same resolver as the single-symbol engine / live —
                    # see backtester/engine.py's evaluate_signal call for the full rationale.
                    from backend.risk.position_sizer import resolve_sizing_base_balance
                    _sizing_base_balance = resolve_sizing_base_balance(
                        self.risk_config.get("sizing_basis", "STATIC"),
                        static_balance=initial_balance,
                        live_balance=balance,
                        live_equity=balance,
                    )
                    approved, reason, tp_levels = self.risk_engine.evaluate_signal(
                        signal_data=sig,
                        account_balance=balance,
                        current_time=current_time_dt,
                        initial_balance=_sizing_base_balance
                    )
                except Exception as e:
                    self.invalid_signals += 1
                    self.rejection_funnel["errors"] += 1
                    self._record_blocked(sig, current_time_dt, "engine_error", str(e))
                    continue

                if not approved:
                    self.invalid_signals += 1
                    self.rejection_funnel["risk_rejections"][reason] = self.rejection_funnel["risk_rejections"].get(reason, 0) + 1
                    self._record_blocked(sig, current_time_dt, "risk_engine", reason)
                    continue

                is_valid, val_reason = _validate_position(
                    sig["direction"], sig["entry_price"], sig["stop_loss"], tp_levels[-1].tp_price
                )
                if not is_valid:
                    self.invalid_signals += 1
                    self.rejection_funnel["risk_rejections"][val_reason] = self.rejection_funnel["risk_rejections"].get(val_reason, 0) + 1
                    self._record_blocked(sig, current_time_dt, "invalid_position_geometry", val_reason)
                    continue

                self.rejection_funnel["approved"] += 1

                # Item 3.1: the stored/fill entry_price must always be the
                # realistic next-bar-open price for this symbol's bar at
                # current_time, never the strategy's theoretical signal
                # entry_price unconditionally — sig["entry_price"] is kept only
                # as a reference/logging value below (confirmations, risk-dollar
                # sizing distance, run_logs), not as the fill price.
                _bar_for_fill = symbol_cache.get(cache_key, {}).get("bars", {}).get(current_time)  # [12.8]
                bar_open_price = (
                    _bar_for_fill["open"] if _bar_for_fill and "open" in _bar_for_fill else sig["entry_price"]
                )

                # ── Task 1: re-validate SL/TP against the ACTUAL FILL price ──
                # Same defect and same fix as engine.py: the risk engine validated
                # SL-vs-entry using the strategy's theoretical signal entry, but
                # legs fill at the next bar's open. When that open gaps across the
                # stop, the position opens with SL on the wrong side, the risk
                # distance collapses to a fraction of a pip and sizing explodes.
                # Live trading rejects exactly these (mt5/order_manager.py's
                # stale-signal guard), so booking them here is a backtest-vs-live
                # divergence, not conservatism.
                #
                # Validated for EVERY leg BEFORE any state is mutated (run log,
                # circuit-breaker notification, position creation) so a rejected
                # group is skipped atomically and can never be left half-created.
                _fill_costs = self._costs_for(symbol)
                _fill_reject_key = ""
                _fill_reject_detail = ""
                for _lvl in tp_levels:
                    _ok, _key, _detail = validate_at_fill_price(
                        sig["direction"],
                        bar_open_price,
                        sig["stop_loss"],
                        _lvl.tp_price,
                        symbol,
                        stops_level_pips=_fill_costs["stops_level_pips"],
                        spread_pips=_fill_costs["spread_pips"],
                    )
                    if not _ok:
                        _fill_reject_key, _fill_reject_detail = _key, _detail
                        break

                if _fill_reject_key:
                    logger.warning(
                        f"[PORTFOLIO] ❌ Fill-time rejection ({_fill_reject_key}) {symbol} "
                        f"signal_entry={sig['entry_price']:.5f} fill={bar_open_price:.5f} "
                        f"sl={sig['stop_loss']:.5f}: {_fill_reject_detail}"
                    )
                    # Undo the optimistic "approved" increment so the funnel balances.
                    self.rejection_funnel["approved"] -= 1
                    self.invalid_signals += 1
                    _fr = self.rejection_funnel.setdefault("fill_rejections", {})
                    _fr[_fill_reject_key] = _fr.get(_fill_reject_key, 0) + 1
                    # Also surface in risk_rejections so the existing rejection-funnel
                    # UI shows it rather than the signal vanishing silently.
                    self.rejection_funnel["risk_rejections"][_fill_reject_key] = (
                        self.rejection_funnel["risk_rejections"].get(_fill_reject_key, 0) + 1
                    )
                    self._record_blocked(sig, current_time_dt, f"fill:{_fill_reject_key}", _fill_reject_detail)
                    self.run_logs.append({
                        "time": _epoch_to_iso(sig_time),
                        "level": "WARNING",
                        "category": "BACKTEST_LOG",
                        "message": f"Rejected at fill ({_fill_reject_key}) {sig['direction']} {symbol}: {_fill_reject_detail}",
                    })
                    continue

                self.run_logs.append({
                    "time": _epoch_to_iso(sig_time),
                    "level": "INFO",
                    "category": "BACKTEST_LOG",
                    "message": f"Opened {sig['direction']} {symbol} @ {sig['entry_price']:.5f} | {len(tp_levels)} TPs"
                })

                # [2.7/A5 parity] Re-anchor SL/TP to the actual fill, same fix
                # as backtester/engine.py::_create_position — this path never
                # had it: every leg's stop/target stayed pinned to the
                # strategy's theoretical signal price, so realised risk/RR
                # drifted with fill slippage exactly as described for the
                # single-symbol engine, just never fixed here. sl_distance is
                # invariant under this parallel shift, so it re-validates
                # trivially against the check already run above.
                _fill_delta = bar_open_price - sig["entry_price"]

                # Bug 3 fix: notify circuit breaker of the new signal group so
                # daily_trades_count, max_concurrent_positions, and
                # open_positions_by_symbol are properly accumulated.
                # Previously this call was missing entirely from portfolio engine,
                # making all CB trade-count limits ineffective in portfolio backtests.
                strategy_id = sig.get("strategy_name", sig.get("strategy_id", "UNKNOWN"))

                if hasattr(self.risk_engine, "circuit") and hasattr(self.risk_engine.circuit, "position_opened"):
                    from backend.risk.position_sizer import calculate_risk_dollars
                    actual_risk = sum(
                        calculate_risk_dollars(lvl.volume, sig["entry_price"], sig["stop_loss"], symbol)
                        for lvl in tp_levels
                    )
                    self.risk_engine.circuit.position_opened(
                        group_id,
                        len(tp_levels),
                        symbol=symbol,
                        initial_risk_dollars=actual_risk,
                        strategy_id=strategy_id,
                        direction=sig.get("direction", ""),  # [9.6]
                        slot_id=sig.get("metadata", {}).get("slot_id", ""),  # [12.5/12.6]
                    )

                # Detect entry session (used for session win-rate breakdown and
                # displayed directly in the trade-expand panel)
                try:
                    entry_session = detect_session(sig_time)
                except Exception:
                    entry_session = "UNKNOWN"

                # Mirror engine.py's confirmations list so the frontend's
                # "Entry Confirmations" panel has something to render. Built
                # per-TP-level (same as engine.py's _create_position) since
                # each leg has its own TP price / RR / volume.
                #
                # FIX: this used to be built once, ABOVE this loop, but
                # referenced the loop variable `lvl` before the `for lvl in
                # tp_levels:` loop that defines it had even started —
                # `UnboundLocalError: cannot access local variable 'lvl'`,
                # crashing every portfolio backtest on its first approved
                # signal. Moved inside the loop so `lvl` is always bound.
                signal_confirmations = sig.get("confirmations", [])

                for lvl in tp_levels:
                    _leg_sl = sig["stop_loss"] + _fill_delta
                    _leg_tp = lvl.tp_price + _fill_delta
                    entry_confirmations = [
                        f"Direction: {sig.get('direction', 'UNKNOWN')}",
                        f"Symbol: {symbol}",
                        f"Strategy: {strategy_id}",
                        f"Entry Price: {bar_open_price:.5f}",
                        f"Signal Entry Price (reference): {sig['entry_price']:.5f}",
                        f"Stop Loss (re-anchored to fill): {_leg_sl:.5f}",
                        f"Take Profit (TP{lvl.level}, re-anchored to fill): {_leg_tp:.5f}",
                        f"RR Multiplier: 1:{lvl.rr_multiplier:.1f}",
                        f"Volume: {lvl.volume:.2f} lots ({lvl.volume_pct * 100:.0f}%)",
                        f"Entry Session: {entry_session}",
                        "Entry Mode: ALL AT ENTRY",
                    ]
                    if signal_confirmations:
                        entry_confirmations.append("── SMC Analysis ──")
                        entry_confirmations.extend(signal_confirmations)

                    new_pos = {
                        "id": str(uuid.uuid4()),
                        "group_id": group_id,
                        "symbol": symbol,
                        "_cache_key": cache_key,  # [12.8]
                        "strategy_id": strategy_id,
                        "strategy": strategy_id,  # kept as an alias for backward compatibility
                        "direction": sig["direction"],
                        "entry_time": sig_time,
                        "entry_time_iso": _epoch_to_iso(sig_time),
                        "entry_session": entry_session,
                        "entry_price": bar_open_price,
                        "stop_loss": _leg_sl,
                        # Immutable copy of the entry-time stop — `stop_loss` is mutated
                        # by BE/trailing, so R must be measured against this instead.
                        "initial_stop_loss": _leg_sl,
                        "original_sl": _leg_sl,  # risk/engine.py::manage_open_position reads this key [4.1/F1]
                        # [2.7/A5 parity] Theoretical (pre-fill) signal levels, for audit.
                        "signal_entry_price": sig["entry_price"],
                        "signal_stop_loss": sig["stop_loss"],
                        "take_profit": _leg_tp,
                        "volume": lvl.volume,
                        "tp_level": lvl.level,
                        "be_applied": False,
                        "trail_applied": False,
                        # [Phase 5 parity] was reading a global "trailing_method" key
                        # that nothing ever set (always fell to the "NONE" default,
                        # unconditionally) instead of this TP level's own resolved
                        # trail method — trailing was silently dead for every
                        # portfolio-mode position regardless of trail_method_tpN config.
                        "trail_method": lvl.trail_method,
                        "metadata": sig.get("metadata", {}),
                        "mae_pips": 0.0,
                        "mfe_pips": 0.0,
                        # ── Previously missing fields — see fix notes ──
                        "confluence_score": sig.get("confluence_score", 0),
                        "balance_before": balance,
                        "status": "OPEN",
                        # Task 4: hard_close_time (e.g. VWAP's 15:55 ET flat rule)
                        # was set by the strategy but never lifted out of
                        # sig["metadata"] onto the position, so the SESSION_END
                        # force-close branch above always read None and was dead
                        # code — positions ran for days past their mandated flat.
                        # (Storing the whole `metadata` dict is not enough: the
                        # close check reads pos["hard_close_time"] directly.)
                        "hard_close_time": sig.get("metadata", {}).get("hard_close_time"),
                        "entry_confirmations": entry_confirmations,
                        "entry_snapshot_b64": sig.get("metadata", {}).get("entry_snapshot_b64", ""),
                        # [I3] see engine.py's matching field for rationale.
                        "sizing_diagnostics": sig.get("metadata", {}).get("sizing_diagnostics"),
                        "original_signal": sig,
                        "_last_known_close": bar_open_price
                    }
                    self.open_positions.append(new_pos)

            # Item 3.5: record floating equity once per timestamp, INSIDE the
            # `for current_time in global_timeline:` loop. Previously this
            # append call sat at the same indentation as the loop header
            # itself (a sibling, not a child), so it executed exactly once
            # after the entire loop finished — equity_curve stayed at length
            # ~2 for the whole run regardless of trade count, and the
            # frontend's Equity Curve chart (which only renders when
            # equity_curve.length > 1) was effectively hidden for every
            # portfolio backtest. Recompute floating PnL from the
            # post-close/post-open open_positions state for this bar,
            # mirroring engine.py's post_close_pnl pattern.
            post_close_pnl = 0.0
            for _pos in self.open_positions:
                _last_close = _pos.get("_last_known_close", _pos["entry_price"])
                post_close_pnl += self._calc_pnl(
                    _pos["direction"], _pos["entry_price"], _last_close, _pos["volume"], _pos.get("symbol")
                )
            self.equity_curve.append(balance + post_close_pnl)

        # 4. Force close remaining positions at end of backtest
        if self.open_positions:
            for pos in self.open_positions:
                sym = pos.get("symbol")
                _ckey = pos.get("_cache_key", sym)  # [12.8]
                if _ckey in symbol_cache:
                    last_time = max(symbol_cache[_ckey]["bars"].keys())
                    bar = symbol_cache[_ckey]["bars"][last_time]
                    pos["exit_price"] = bar["close"]
                    pos["exit_time"] = last_time
                else:
                    pos["exit_price"] = pos["entry_price"]
                    pos["exit_time"] = global_timeline[-1]

                pos["exit_reason"] = "END_OF_BACKTEST"
                pos["status"] = "CLOSED"  # Task 6
                pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""), pos.get("entry_time"), pos.get("exit_time"))
                pos["duration_minutes"] = _calc_duration_minutes(pos.get("entry_time"), pos.get("exit_time"))
                pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
                pos["exit_time_iso"] = _epoch_to_iso(pos.get("exit_time"))
                
                balance += pos.get("pnl", 0)
                pos["balance_after"] = balance
                self.trades.append(pos)
                
            self.open_positions.clear()

        self.equity_curve.append(balance)

        # Generate metrics
        from backend.analytics.reports import generate_risk_report
        import uuid as _uuid
        
        # [12.8] trade_grouper.py resolves chart candles by the trade's REAL
        # symbol (`candles.get(trade["symbol"])`), but portfolio_data/
        # portfolio_data_m15/portfolio_data_m5 are keyed by cache_key, which
        # only equals the real symbol when no two slots share one. Re-key by
        # real symbol here (first cache_key seen for a given real symbol
        # wins — if two slots share a symbol with different candle sets,
        # this is a "which chart to show" tie, not a correctness issue for
        # PnL, which never uses this re-keyed dict).
        def _by_real_symbol(data: dict | None) -> dict:
            if not data:
                return {}
            out = {}
            for ckey, df in data.items():
                rs = _real_symbol(ckey)
                out.setdefault(rs, df)
            return out

        # Group trades using the shared utility. Pass the per-symbol candle
        # dicts through so trade_grouper can extract chart_data / SMC zones
        # per group (previously this was called with no candle data at all,
        # so chart_data/chart_data_m15/chart_data_m5 were always empty for
        # every portfolio backtest — see trade_grouper.py's symbol-aware
        # candle resolution for how the dict form is handled).
        grouped_trades = group_trades(
            self.trades,
            _by_real_symbol(portfolio_data),
            _by_real_symbol(portfolio_data_m15),
            _by_real_symbol(portfolio_data_m5),
        )
        
        # Pass the true initial_balance explicitly — do NOT rely on inference
        # from trades[0].balance_before (fragile, and was the root cause of
        # every report metric — Sharpe/Sortino/drawdown/equity curve — being
        # computed off a $0 baseline before balance_before was fixed above).
        report = generate_risk_report(grouped_trades, initial_balance=initial_balance)
        # Attach rejection funnel directly to the RiskReport object (same pattern as engine.py)
        report.rejection_funnel = self.rejection_funnel

        # ── Task 5: TP/SL/BE/TRAIL hit rates from LEG-level exits ──
        # generate_risk_report only sees grouped trades, whose exit_reason is the
        # group's terminal reason, so legs that genuinely banked TP1 before the
        # group closed on BE_SL/SL were never counted.
        hit_rates = apply_leg_level_hit_rates(report, self.trades, grouped_trades)

        # ── Task 6: drawdown from the BAR-level equity curve ──
        dd = apply_bar_level_drawdown(report, self.equity_curve, initial_balance)

        total_pnl = balance - initial_balance

        results = {
            "backtest_id": str(_uuid.uuid4()),
            "initial_balance": initial_balance,
            "final_balance": balance,
            "total_pnl": total_pnl,
            "total_trades": len(self.trades),
            "invalid_signals": self.invalid_signals,
            "trades": self.trades,
            "grouped_trades": grouped_trades,
            "equity_curve": self.equity_curve,
            "report": report,
            "rejection_funnel": self.rejection_funnel,
            "blocked_signals": self.blocked_signals,
            "run_logs": self.run_logs,
            "prop_firm_breach_days": sorted(list(breach_days)),
            # Task 2: per-symbol record of the transaction costs this run assumed
            # and where each value came from (USER vs MT5 vs asset-class default).
            "cost_model": self.cost_model,
            # [2.24] Distinguishes a drawdown-latched stretch from "no setups".
            "circuit_breaker_summary": {
                "paused_checks": self.risk_engine.circuit.paused_bars,
                "last_pause_reason": self.risk_engine.circuit.last_pause_reason,
            },
        }

        logger.info(f"[PORTFOLIO] ═══ Global backtest complete ═══")
        logger.info(f"[PORTFOLIO] Final Balance: ${balance:.2f} | Trades: {len(self.trades)}")
        logger.info(
            f"[PORTFOLIO] Leg-level hit rates: "
            f"TP1={hit_rates['tp1_hit_rate'] * 100:.1f}% TP2={hit_rates['tp2_hit_rate'] * 100:.1f}% "
            f"TP3={hit_rates['tp3_hit_rate'] * 100:.1f}% SL={hit_rates['sl_hit_rate'] * 100:.1f}% "
            f"BE={hit_rates['be_hit_rate'] * 100:.1f}% TRAIL={hit_rates['trail_hit_rate'] * 100:.1f}% "
            f"| max_dd={dd['max_drawdown_pct'] * 100:.2f}% (bar-level)"
        )
        if self.rejection_funnel.get("fill_rejections"):
            logger.info(f"[PORTFOLIO] Fill-time rejections: {self.rejection_funnel['fill_rejections']}")
        logger.info(f"[PORTFOLIO] Cost model applied: {self.cost_model}")
        
        return results