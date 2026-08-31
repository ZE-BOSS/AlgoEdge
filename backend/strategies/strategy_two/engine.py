"""
backend/strategies/strategy_two/engine.py

DriftJumpAlpha Strategy Orchestrator
Source: DriftJumpAlpha_Strategy_Spec_v2.md

Implements Continuous Drift (Setup A) + Discrete Jump (Setup B) logic for Crash indices only.
Uses simplified empirical gap counting.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.risk.position_sizer import get_pip_size
from backend.services.bot_service import bot_service
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.market_structure import MarketStructureDetector
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Spec default values for DriftJumpAlpha v2
SPEC_DEFAULTS = {
    "min_ema_separation_atr_multiple": 0.2,
    "pullback_max_distance_atr_multiple": 3.0,
    "confirmation_candles_required": 1,
    "atr_period": 14,
    "trailing_atr_multiple_low_vol": 1.5,
    "trailing_atr_multiple_high_vol": 2.5,
    "flatten_all_at_percentile": 99,
    "gap_percentile_hard_reduce": 90,
    "size_reduction_pct_at_hard_threshold": 50,
    "min_bars_before_trusting_fit": 100,
    "min_adx_to_trade": 20,
    "jump_entry_percentile_threshold": 95.0,
}

def _true_range(h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> np.ndarray:
    """
    True range as a plain float array.

    The first bar has no previous close, so two of the three TR components are
    NaN there. The pandas form this replaces used
    `pd.concat([...], axis=1).max(axis=1)`, and DataFrame.max **skips NaN** — so
    TR[0] came out as plain `high - low`, not NaN. `np.maximum` propagates NaN
    instead, which shifted the first valid rolling value by one bar (caught by
    an equivalence test: ATR index 13 was a number before and NaN after).
    Seeding slot 0 with `high - low` reproduces the original exactly.
    """
    prev_c = np.empty_like(c)
    prev_c[0] = np.nan
    prev_c[1:] = c[:-1]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
    if tr.size:
        tr[0] = h[0] - lo[0]
    return tr


def _rolling_mean(a: np.ndarray, period: int) -> np.ndarray:
    """
    Trailing rolling mean, NaN-padded for the first `period-1` slots — the same
    shape and alignment `Series.rolling(period).mean()` produces.

    NaN-aware via cumulative sums over a zero-filled copy, so a leading NaN in
    the true range propagates exactly as pandas does.
    """
    n = a.size
    out = np.full(n, np.nan, dtype=float)
    if n < period:
        return out
    valid = ~np.isnan(a)
    filled = np.where(valid, a, 0.0)
    csum = np.concatenate(([0.0], np.cumsum(filled)))
    ccnt = np.concatenate(([0], np.cumsum(valid)))
    win_sum = csum[period:] - csum[:-period]
    win_cnt = ccnt[period:] - ccnt[:-period]
    # A window containing any NaN is NaN in pandas' default (min_periods=period).
    res = np.where(win_cnt == period, win_sum / period, np.nan)
    out[period - 1:] = res
    return out


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    [L1-opt] Vectorised. The previous form built three intermediate Series, then
    `pd.concat(..., axis=1).max(axis=1)` — a full DataFrame construction and a
    row-wise max — on EVERY bar of EVERY backtest, over the whole 500-bar
    window, for one trailing value. Same arithmetic, no pandas object churn.
    """
    h = df['high'].to_numpy(dtype=float, copy=False)
    lo = df['low'].to_numpy(dtype=float, copy=False)
    c = df['close'].to_numpy(dtype=float, copy=False)
    return pd.Series(_rolling_mean(_true_range(h, lo, c), period), index=df.index)

def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ADX (Average Directional Index).

    [L1-opt] Vectorised. The pandas form constructed ~8 intermediate Series and
    ran four separate `.rolling()` passes per call, and this is called once per
    bar over the full 500-bar window for a single trailing value. Identical
    arithmetic on numpy arrays; the only Series built is the returned one.
    """
    h = df['high'].to_numpy(dtype=float, copy=False)
    lo = df['low'].to_numpy(dtype=float, copy=False)
    c = df['close'].to_numpy(dtype=float, copy=False)

    up_move = np.empty_like(h)
    up_move[0] = np.nan
    up_move[1:] = h[1:] - h[:-1]

    down_move = np.empty_like(lo)
    down_move[0] = np.nan
    down_move[1:] = lo[:-1] - lo[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # `calculate_atr(df, 1)` is the raw true range (a 1-period mean of itself),
    # then smoothed over `period` — preserved exactly.
    atr = _rolling_mean(_rolling_mean(_true_range(h, lo, c), 1), period)

    with np.errstate(divide='ignore', invalid='ignore'):
        plus_di = 100.0 * (_rolling_mean(plus_dm, period) / atr)
        minus_di = 100.0 * (_rolling_mean(minus_dm, period) / atr)
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)

    return pd.Series(_rolling_mean(dx, period), index=df.index)

@register_strategy("DriftJumpAlpha_v1")
class DriftJumpAlphaEngine(BaseStrategy):
    """
    Core engine for trading Crash Synthetic Indices.
    Setup A: Drift Continuation (Buy)
    Setup B: Jump Entry (Sell)
    """

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, 'drift_jump_alpha', getattr(config, 'crashboom', None))
        self.context: dict[str, Any] = {}
        
        # State tracking
        self.last_jump_idx: int | None = None
        self.jump_distances: list[int] = []
        self.ms_detector = MarketStructureDetector(swing_length=5, min_bos_count=1)
        self.post_jump_regime_reset = False
        self.history_scanned = False

        # ── Spec §1 risk guardrails (per-instance, mirroring strategy_vwap's local
        # trades_today/losses_today pattern — this engine already keeps state as
        # un-namespaced instance attributes since one instance is created per symbol) ──
        self.trades_today = 0
        self.daily_risk_used_pct = 0.0
        self.last_reset_date = None
        self.consecutive_losses = 0
        self.cooldown_until: datetime | None = None

    def log_event(self, message: str, level: str = "INFO", category: str = "DJA"):
        """
        Delegate to BaseStrategy.log_event, keeping only the DJA category default.

        [L1-opt] This used to be a full copy of the base implementation, which
        meant it bypassed the base class's backtest fast path and kept fanning
        every INFO line out to bot_service. Profiling showed the override alone
        costing 5.4 s of 26.7 s (20%) on a 2,200-bar run — the base class had
        already been fixed, and this shadowed the fix.
        """
        super().log_event(message, level=level, category=category)

    async def initialize(self):
        logger.info("DriftJumpAlphaEngine initialized")

    def get_required_timeframes(self) -> list[str]:
        return ["M5"]

    def detect_jump(self, bar: pd.Series, symbol: str, atr_val: float) -> bool:
        """Detect if the bar is a massive jump against the drift (downwards for Crash)."""
        symbol_upper = symbol.upper()
        
        if pd.isna(atr_val) or atr_val <= 0:
            return False
            
        pip_size = get_pip_size(symbol)
        threshold_price = 4.0 * atr_val
        if self.params and getattr(self.params, 'spike_threshold_pips', 0) > 0:
            threshold_price = self.params.spike_threshold_pips * pip_size
            
        if abs(bar['open'] - bar['close']) < threshold_price:
            return False
            
        # Hard Crash-only jump logic (downwards)
        if "CRASH" in symbol_upper and bar['close'] < bar['open']:
            return True 
            
        return False

    def compute_gap_percentile(self, bars_since: int) -> float:
        if not self.jump_distances or len(self.jump_distances) < 5:
            return 0.0 # Untrusted
        
        sorted_dists = sorted(self.jump_distances)
        count_below = sum(1 for d in sorted_dists if d <= bars_since)
        return (count_below / len(sorted_dists)) * 100.0

    def calculate_adaptive_atr_multiple(self, current_atr: float, avg_atr: float) -> float:
        """Adaptive trailing ATR multiple based on recent volatility vs avg volatility."""
        if current_atr > (avg_atr * 1.2):
            return SPEC_DEFAULTS['trailing_atr_multiple_high_vol']
        return SPEC_DEFAULTS['trailing_atr_multiple_low_vol']

    def notify_outcome(self, symbol: str, group_id: str, is_win: bool, pnl: float) -> None:
        """
        Called by the backtester/live engine after a full trade group closes.
        Tracks consecutive losses so spec §1's max_consecutive_losses /
        cooldown_after_max_losses_hours guardrails can be enforced in on_bar.
        """
        if is_win:
            self.consecutive_losses = 0
            return

        self.consecutive_losses = getattr(self, 'consecutive_losses', 0) + 1
        max_losses = getattr(self.params, 'max_consecutive_losses', 4) if self.params else 4
        if self.consecutive_losses >= max_losses:
            cooldown_hours = getattr(self.params, 'cooldown_after_max_losses_hours', 12) if self.params else 12
            self.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
            self.log_event(
                f"[{symbol}] Max consecutive losses ({self.consecutive_losses}) reached. "
                f"Cooldown until {self.cooldown_until.isoformat()}",
                "WARN",
            )

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        """
        DriftJumpAlpha Setup A (Drift) + Setup B (Jump Entry) evaluation.
        """
        # [T1.3] Open a confluence-telemetry record for this bar.
        # No-op unless self.gates.enabled (live default is off).
        # Placed AFTER the docstring — inserting before it would demote the
        # docstring to a plain no-op string expression and lose __doc__.
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        if timeframe != "M5":
            return None
            
        if not self.gate("crash_symbol_only", "CRASH" in symbol.upper()):
            return None # HARD CRASH ONLY FILTER
            
        if not self.is_backtesting:
            self.run_logs = []
            
        if len(candles) < 50:
            return None

        current_bar = candles.iloc[-1]
        # [L1-opt] This fired on EVERY bar — 50,000 formatted log records for a
        # full run, each one hitting loguru and (previously) bot_service. It says
        # nothing a reader needs. Removed rather than downgraded: the gate
        # telemetry now records what actually happened on each bar.

        # ── Spec §1 risk guardrails ─────────────────────────────────────────
        # Daily reset (trades_today / daily_risk_used_pct)
        bar_date = candles.index[-1].date()
        if self.last_reset_date != bar_date:
            self.trades_today = 0
            self.daily_risk_used_pct = 0.0
            self.last_reset_date = bar_date

        # Cooldown after max_consecutive_losses (set by notify_outcome)
        if self.cooldown_until is not None:
            now_utc = datetime.now(timezone.utc)
            if now_utc < self.cooldown_until:
                self.log_event(f"[{symbol}] In post-loss cooldown until {self.cooldown_until.isoformat()}.")
                return None
            self.cooldown_until = None

        max_trades_per_day = getattr(self.params, 'max_trades_per_day', 6) if self.params else 6
        if not self.gate("daily_trade_cap", self.trades_today < max_trades_per_day):
            self.log_event(f"[{symbol}] max_trades_per_day ({max_trades_per_day}) reached. Blocking entries.")
            return None

        max_daily_risk_pct = getattr(self.params, 'max_daily_risk_pct', 4.0) if self.params else 4.0
        risk_per_trade_pct = getattr(getattr(self.config, 'risk', None), 'risk_per_trade_pct', 1.0)
        if not self.gate("daily_risk_cap",
                         self.daily_risk_used_pct + risk_per_trade_pct <= max_daily_risk_pct):
            self.log_event(f"[{symbol}] max_daily_risk_pct ({max_daily_risk_pct}%) would be exceeded. Blocking entries.")
            return None

        # Load UI Params
        fast_period = self.params.drift_ema_fast if getattr(self, 'params', None) else 20
        slow_period = self.params.drift_ema_slow if self.params else 50
        min_adx = getattr(self.params, 'min_adx_to_trade', SPEC_DEFAULTS['min_adx_to_trade'])
        jump_threshold = getattr(self.params, 'jump_entry_percentile_threshold', SPEC_DEFAULTS['jump_entry_percentile_threshold'])
        trade_jumps = getattr(self.params, 'trade_jumps_enabled', False)
        control_test_passed = getattr(self.params, 'control_test_passed', False)
        
        pip_size = get_pip_size(symbol)
        
        # Precompute indicators
        df = candles.copy()
        df['ema_fast'] = df['close'].ewm(span=fast_period, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=slow_period, adjust=False).mean()
        df['atr'] = calculate_atr(df, SPEC_DEFAULTS['atr_period'])
        df['adx'] = calculate_adx(df, 14)
        
        current_bar = df.iloc[-1]
        atr_val = current_bar['atr']
        adx_val = current_bar['adx']
        # Rolling window baseline, not an unbounded all-time mean — an all-time mean
        # degenerates into a near-fixed threshold once enough history accumulates,
        # rather than reacting to CURRENT volatility as the "adaptive" trail intends.
        avg_atr = df['atr'].tail(300).mean()
        
        if pd.isna(atr_val) or atr_val <= 0:
            return None
            
        # Update Market Structure
        ms_state = self.ms_detector.update(candles)

        # Detect historical jumps only once
        if not self.history_scanned:
            self.jump_distances = []
            bars_since = 999999
            for i in range(1, len(df)-1):
                b = df.iloc[i]
                if self.detect_jump(b, symbol, b['atr']):
                    if bars_since != 999999:
                        self.jump_distances.append(bars_since)
                    bars_since = 0
                else:
                    if bars_since != 999999:
                        bars_since += 1
                    
            self.bars_since_jump = bars_since
            self.post_jump_regime_reset = False
            self.history_scanned = True
            
        if self.detect_jump(current_bar, symbol, atr_val):
            if getattr(self, 'bars_since_jump', 999999) != 999999:
                self.jump_distances.append(self.bars_since_jump)
            self.bars_since_jump = 0
            self.post_jump_regime_reset = True
            self.log_event("Jump detected! Resetting regime wait.")
            return None
        else:
            if getattr(self, 'bars_since_jump', 999999) != 999999:
                self.bars_since_jump += 1
            
        if not self.gate("jump_cooldown", getattr(self, 'bars_since_jump', 999999) >= 5):
            # [L1-opt] Was an INFO log on every cooldown bar. The gate records it.
            return None
            
        gap_pct = self.compute_gap_percentile(self.bars_since_jump)
        
        if gap_pct >= SPEC_DEFAULTS['flatten_all_at_percentile']:
            self.log_event(f"Gap percentile {gap_pct:.1f}% >= threshold {SPEC_DEFAULTS['flatten_all_at_percentile']}. Blocking entries.")
            return None

        # ---------------------------------------------------------
        # SETUP B: JUMP ENTRY (SELL)
        # ---------------------------------------------------------
        if trade_jumps and control_test_passed and gap_pct >= jump_threshold:
            swings = ms_state.get("swings", [])
            if swings and current_bar['close'] < current_bar['open']:
                lows = [s for s in swings if s["type"] == "LOW" and float(s["price"]) > current_bar["close"]]
                if lows:
                    # Bearish ChoCH/break of swing low achieved!
                    self.log_event(f"Setup B (Jump Entry) Triggered! Gap={gap_pct:.1f}% >= {jump_threshold}%")
                    
                    entry_price = float(current_bar['close'])
                    highs = [s for s in swings if s["type"] == "HIGH" and float(s["price"]) > entry_price]
                    buffer = 0.2 * atr_val
                    # Fallback SL (no valid swing structure): spec's buffer_atr_multiple
                    # for Setup B is 0.2 — intentionally tighter than Setup A's adaptive
                    # 1.5x/2.5x multiple. Reusing Setup A's width here undermined both
                    # the RRR calc and the lot-ceiling sizing rationale for Setup B.
                    sl = float(highs[-1]["price"]) + buffer if highs else entry_price + (atr_val * 0.2)

                    risk = abs(entry_price - sl)
                    min_reward = risk * 1.5
                    tp = entry_price - max(atr_val * 3, min_reward)

                    reward = abs(entry_price - tp)
                    rrr = reward / risk if risk > 0 else 0.0
                    min_rrr = getattr(self.params, 'min_rrr_to_accept_trade', 1.5) if self.params else 1.5
                    if not self.gate("min_rrr_jump", rrr >= min_rrr, detail=f"rrr={rrr:.2f}"):
                        self.log_event(f"[{symbol}] Setup B RRR {rrr:.2f} < min_rrr_to_accept_trade {min_rrr}. Discarding signal.")
                        return None

                    self.trades_today += 1
                    self.daily_risk_used_pct += risk_per_trade_pct

                    return self._tag_signal(TradeSignal(
                        strategy_id="DriftJumpAlpha_v1",
                        symbol=symbol,
                        direction="SELL",
                        signal_type="JUMP_ENTRY",
                        entry_price=entry_price,
                        stop_loss=sl,
                        take_profit=tp,
                        confluence_score=95,
                        timeframe=timeframe,
                        timestamp=candles.index[-1].timestamp(),
                        metadata={
                            "size_modifier": 1.0, # Sizing rules or ceilings applied at order execution layer
                            "gap_pct": gap_pct,
                            "trail_method": "NONE",
                            "atr_val": float(atr_val),
                            "setup": "setup_b_jump",
                            "reason": f"Jump SELL. Gap: {gap_pct:.1f}%",
                            "aggregate_max_lots_per_symbol": getattr(self.params, 'aggregate_max_lots_per_symbol', 0.0)
                        }
                    ))
            
        # ---------------------------------------------------------
        # SETUP A: DRIFT CONTINUATION (BUY)
        # ---------------------------------------------------------
        if gap_pct >= jump_threshold:
            self.log_event(f"Gap pct {gap_pct:.1f}% >= jump threshold {jump_threshold}%. Blocking Drift Buys.")
            return None

        ema_sep = abs(current_bar['ema_fast'] - current_bar['ema_slow'])
        min_sep = SPEC_DEFAULTS['min_ema_separation_atr_multiple'] * atr_val
        
        regime_active = current_bar['ema_fast'] > current_bar['ema_slow'] and ema_sep > min_sep
        
        # NaN ADX means zero net directional movement (+DI == -DI) — the exact
        # "insufficient trend strength" case this filter exists to reject. The old
        # `pd.notna(adx_val) and ...` check let NaN silently bypass the filter
        # instead of blocking on it.
        # [6.13/S18] adx_size_modifier feeds into the final size_modifier below —
        # 1.0 unless the REDUCED_SIZE clamp below actually reduces it.
        adx_size_modifier = 1.0
        if not pd.notna(adx_val) or adx_val < min_adx:
            adx_gate_mode = getattr(self.params, "adx_gate_mode", "REDUCED_SIZE") if self.params else "BLOCK"
            if adx_gate_mode == "BLOCK":
                self.log_event(f"ADX {adx_val:.1f} < {min_adx}. Drift Regime ignored due to weak trend.")
                regime_active = False
            else:
                # Percentile rank of the current ADX within its own recent
                # history, so "weak trend" is measured against this
                # instrument's own regime, not an arbitrary fixed threshold.
                recent_adx = df["adx"].tail(100).dropna()
                if len(recent_adx) >= 10 and pd.notna(adx_val):
                    percentile = float((recent_adx < adx_val).mean())
                else:
                    percentile = 0.0
                floor = getattr(self.params, "adx_gate_min_size_modifier", 0.1) if self.params else 0.1
                adx_size_modifier = max(floor, percentile)
                self.log_event(
                    f"ADX {adx_val:.1f} < {min_adx} — REDUCED_SIZE mode, sizing scaled to "
                    f"{adx_size_modifier:.0%} (ADX {percentile:.0%} percentile of recent history) "
                    f"instead of blocked."
                )
                # regime_active is left as computed from the EMA-separation
                # check above — the setup is allowed through, at reduced size.

        if not self.gate("drift_regime_active", regime_active,
                         detail=f"adx={adx_val if pd.notna(adx_val) else float('nan'):.1f}"):
            # Do NOT clear post_jump_regime_reset here — this branch fires on
            # essentially every bar right after a jump (regime is typically inactive
            # post-jump), which previously reset the flag before its own confirmation
            # condition (close >= ema_fast, below) ever got a chance to fire. The flag
            # is only cleared once that confirmation is satisfied.
            self.log_event("Drift Regime UP inactive.")
            return None
            
        if self.post_jump_regime_reset:
            if not self.gate("post_jump_ema_reclaim", current_bar['close'] >= current_bar['ema_fast']):
                self.log_event("Waiting for price to cross above Fast EMA to reset regime after jump.")
                return None
            self.post_jump_regime_reset = False
            self.log_event("Post-jump regime reset complete.")
            
        # Pullback Trigger
        dist_to_fast = abs(current_bar['close'] - current_bar['ema_fast'])
        max_dist = SPEC_DEFAULTS['pullback_max_distance_atr_multiple'] * atr_val
        
        if not self.gate("pullback_within_atr", dist_to_fast <= max_dist,
                         detail=f"dist_atr={dist_to_fast / atr_val:.2f}" if atr_val else None):
            return None
            
        # M6 Confirmation
        swings = ms_state.get("swings", [])
        if not swings: 
            return None
        
        if current_bar['close'] <= current_bar['open']: 
            self.log_event("Bullish drift requires bullish close candle.")
            return None
            
        highs = [s for s in swings if s["type"] == "HIGH" and float(s["price"]) < current_bar["close"]]
        if not highs: 
            self.log_event("No previous swing high breached.")
            return None

        self.log_event("Setup A Confirmation passed! Building signal...")

        entry_price = float(current_bar['close'])
        
        size_modifier = 1.0
        if gap_pct >= SPEC_DEFAULTS['gap_percentile_hard_reduce']:
            size_modifier = 1.0 - (SPEC_DEFAULTS['size_reduction_pct_at_hard_threshold'] / 100.0)
        # [6.13/S18] Combine with the ADX-percentile clamp — both are
        # independent size-reduction factors (gap-extremity vs. trend
        # strength), so they multiply rather than one overriding the other.
        size_modifier *= adx_size_modifier
            
        buffer = 1.5 * atr_val
        
        lows = [s for s in swings if s["type"] == "LOW" and float(s["price"]) < entry_price]
        sl = float(lows[-1]["price"]) - buffer if lows else entry_price - (atr_val * self.calculate_adaptive_atr_multiple(atr_val, avg_atr))
        
        risk = abs(entry_price - sl)
        min_reward = risk * 1.5
        atr_dist = atr_val * self.calculate_adaptive_atr_multiple(atr_val, avg_atr)
        
        recovery_pips = self.params.recovery_target_pips if self.params and getattr(self.params, 'recovery_target_pips', 0) > 0 else 0
        recovery_dist = recovery_pips * pip_size
        
        tp = entry_price + max(atr_dist * 5, min_reward, recovery_dist)

        reward = abs(tp - entry_price)
        rrr = reward / risk if risk > 0 else 0.0
        min_rrr = getattr(self.params, 'min_rrr_to_accept_trade', 1.5) if self.params else 1.5
        if not self.gate("min_rrr_drift", rrr >= min_rrr, detail=f"rrr={rrr:.2f}"):
            self.log_event(f"[{symbol}] Setup A RRR {rrr:.2f} < min_rrr_to_accept_trade {min_rrr}. Discarding signal.")
            return None

        self.trades_today += 1
        self.daily_risk_used_pct += risk_per_trade_pct

        sig = TradeSignal(
            strategy_id="DriftJumpAlpha_v1",
            symbol=symbol,
            direction="BUY",
            signal_type="PULLBACK_ENTRY",
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            confluence_score=80,
            timeframe=timeframe,
            timestamp=candles.index[-1].timestamp(),
            metadata={
                "size_modifier": size_modifier,
                "gap_pct": gap_pct,
                "trail_method": "ATR_TRAIL",
                "atr_val": float(atr_val),
                "setup": "setup_a_drift",
                "reason": f"Drift Continuation UP. Gap Pct: {gap_pct:.1f}%",
                "aggregate_max_lots_per_symbol": getattr(self.params, 'aggregate_max_lots_per_symbol', 0.0)
            }
        )
        
        return self._tag_signal(sig)

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        pass
