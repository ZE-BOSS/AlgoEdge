"""
backend/strategies/smc/engine.py

Main SMC Orchestrator — 4-Layer Multi-Timeframe model.
Integrates all SMC sub-components with proper context translation
for ConfluenceScorer.

Source: SMC_Strategy-1.md + Implementation Plan (Definitive Strategy Spec)

Flow: H4 Bias → H1 BOS + Zones → M15 ChoCH → M5 Candlestick → Execute
"""

import pandas as pd
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from backend.strategies.base_strategy import BaseStrategy, TradeSignal, TradeAction
from backend.strategies.registry import register_strategy
from backend.strategies.smc.params import UserConfig
from backend.risk.position_sizer import get_pip_size

from .market_structure import MarketStructureDetector
from .order_blocks import OrderBlockDetector
from .fvg import FVGDetector
from .liquidity import LiquidityMapper
from .confluence import ConfluenceScorer
from .signals import SignalGenerator
from .candlestick import detect_confirmation_pattern
from .ipdm import IPDMDetector

from backend.utils.logger import get_logger
from backend.services.bot_service import bot_service
from backend.utils.timeutils import detect_session

logger = get_logger(__name__)


# Try importing optional modules (may not exist yet)
try:
    from .premium_discount import PremiumDiscountCalculator
except ImportError:
    PremiumDiscountCalculator = None

try:
    from .asian_range import AsianRange
except ImportError:
    AsianRange = None

try:
    from .supply_demand import SupplyDemandDetector
except ImportError:
    SupplyDemandDetector = None


@register_strategy("SMC_v1")
class SMCEngine(BaseStrategy):
    """
    Core Smart Money Concepts trading strategy engine.
    Implements the 4-Layer Multi-Timeframe model with IPDM phase filter.
    """

    def __init__(self, user_config: UserConfig):
        super().__init__(user_config)
        self.smc_params = user_config.smc
        self.run_logs = []  # Store internal execution logs
        self.is_backtesting = False

        swing_len_htf = self.smc_params.swing_length_htf
        swing_len_ltf = self.smc_params.swing_length_ltf

        # Layer 1: H4 bias detector
        self.htf_structure = MarketStructureDetector(
            swing_length=swing_len_htf, min_bos_count=2
        )

        # Layer 2: H1 BOS + structure detector
        # Uses intermediate swing length (between HTF and LTF)
        swing_len_itf = getattr(self.smc_params, 'swing_length_mtf', max(3, swing_len_htf - 2))
        self.h1_structure = MarketStructureDetector(
            swing_length=swing_len_itf, min_bos_count=2
        )

        # Layer 3: M15 ChoCH detector
        self.ltf_structure = MarketStructureDetector(
            swing_length=swing_len_ltf, min_bos_count=2
        )

        # Sub-module detectors
        self.order_blocks = OrderBlockDetector()
        self.h1_order_blocks = OrderBlockDetector()
        self.fvg = FVGDetector(self.smc_params.fvg_min_gap_pips)
        self.liquidity = LiquidityMapper(self.smc_params.liq_sweep_min_pips)
        self.scorer = ConfluenceScorer(self.smc_params)
        self.signal_gen = SignalGenerator(user_config)
        self.ipdm = IPDMDetector()

        # Optional modules
        self.premium_discount = PremiumDiscountCalculator(
            ote_min=self.smc_params.ote_fib_min, 
            ote_max=self.smc_params.ote_fib_max
        ) if PremiumDiscountCalculator else None
        
        self.supply_demand = SupplyDemandDetector() if SupplyDemandDetector else None
        self.asian_range = AsianRange(
            start_hour=self.smc_params.asian_range_start_hour,
            end_hour=self.smc_params.asian_range_end_hour
        ) if AsianRange else None

        # State
        self.context: Dict[str, Any] = {}
        
        # State tracking for frontend logs to prevent log spam
        self.last_logged_htf_bias = None
        self.last_logged_h1_trend = None
        self.last_logged_phase = None

    def log_event(self, message: str, level: str = "INFO", category: str = "SMC"):
        """Intercept logs. If backtesting, store them. Otherwise broadcast to bot_service."""
        if self.is_backtesting:
            self.run_logs.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "category": category,
                "message": message
            })
            bot_service.log_system_event(message, level, f"BT-{category}")
        else:
            bot_service.log_system_event(message, level, category)

    def get_required_timeframes(self) -> List[str]:
        return ["H4", "H1", "M15", "M5"]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> Optional[TradeSignal]:
        """
        Main evaluation loop — processes each timeframe layer.
        Called on M15 bar close with multi-TF data.
        """
        self.log_event(f"SMC Engine evaluating {symbol} on {timeframe}", "DEBUG", "SMC")

        # This method expects to be called with the primary (M15) timeframe
        # HTF data should be pre-loaded into self.context by the caller
        # ── Update HTF & Secondary Data Structures ──
        if timeframe == "H4":
            ms_h4 = self.htf_structure.update(candles)
            self.context["htf"] = ms_h4
            self.bias = ms_h4.get("trend", "NEUTRAL")
            self.context["htf_bias"] = self.bias
            
            # Granular H4 logging
            last_bos = ms_h4.get("last_bos")
            last_choch = ms_h4.get("last_choch")
            if last_bos:
                self.log_event(f"[{symbol} H4] Break of Structure ({last_bos}) confirmed at {ms_h4.get('last_bos_level')} | BOS count: {ms_h4.get('consecutive_bos')}", "INFO", "SMC")
            if last_choch:
                self.log_event(f"[{symbol} H4] Change of Character (ChoCH) detected! Trend reversing to {last_choch}", "INFO", "SMC")

            # Log HTF Bias changes
            if self.bias != "NEUTRAL" and self.bias != self.last_logged_htf_bias:
                self.log_event(f"[{symbol} H4] HTF Bias shifted to {self.bias}", "INFO", "SMC")
                self.last_logged_htf_bias = self.bias

            return None
            
        elif timeframe == "H1":
            ms_h1 = self.h1_structure.update(candles)
            ms_h1["obs"] = self.h1_order_blocks.update(candles)
            self.context["h1"] = ms_h1
            
            # Granular H1 logging
            last_bos = ms_h1.get("last_bos")
            last_choch = ms_h1.get("last_choch")
            if last_bos:
                self.log_event(f"[{symbol} H1] Break of Structure ({last_bos}) confirmed at {ms_h1.get('last_bos_level')} | BOS count: {ms_h1.get('consecutive_bos')}", "INFO", "SMC")
            if last_choch:
                self.log_event(f"[{symbol} H1] Change of Character (ChoCH) detected! Trend reversing to {last_choch}", "INFO", "SMC")

            # Check if H1 trend aligns with H4
            h1_trend = ms_h1.get("trend", "NEUTRAL")
            if h1_trend != self.last_logged_h1_trend:
                if h1_trend != "NEUTRAL" and h1_trend == self.bias:
                    self.log_event(f"[{symbol} H1] Structure aligned with H4 Bias ({self.bias})", "INFO", "SMC")
                self.last_logged_h1_trend = h1_trend
            
            # Update IPDM on H1
            h1_swings = ms_h1.get("swings", [])
            h1_highs = [s for s in h1_swings if s["type"] == "HIGH"]
            h1_lows = [s for s in h1_swings if s["type"] == "LOW"]
            ipdm_state = self.ipdm.update(candles, h1_highs, h1_lows)
            self.context["ipdm"] = ipdm_state
            
            current_phase = ipdm_state.get("phase", "UNKNOWN")
            if current_phase != self.last_logged_phase:
                if current_phase == "EXPANSION":
                    self.log_event(f"[{symbol} IPDM] Entered EXPANSION phase! Hunting for entries...", "INFO", "SMC")
                else:
                    self.log_event(f"[{symbol} IPDM] Entered {current_phase} phase. Waiting...", "INFO", "SMC")
                self.last_logged_phase = current_phase

            # Cache ATR per H1 bar (Issue 6.4)
            lookback = min(14, len(candles) - 1)
            if lookback > 0:
                recent_candles = candles.iloc[-(lookback+1):]
                tr_list = []
                for i in range(1, len(recent_candles)):
                    c = recent_candles.iloc[i]
                    prev_c = recent_candles.iloc[i-1]
                    tr = max(
                        c["high"] - c["low"],
                        abs(c["high"] - prev_c["close"]),
                        abs(c["low"] - prev_c["close"])
                    )
                    tr_list.append(tr)
                self.context["h1_atr"] = sum(tr_list) / len(tr_list) if tr_list else (candles.iloc[-1]["high"] - candles.iloc[-1]["low"])
            return None

        elif timeframe == "M15":
            ms_m15 = self.ltf_structure.update(candles)
            self.context["ltf_structure"] = ms_m15

            last_bos = ms_m15.get("last_bos")
            last_choch = ms_m15.get("last_choch")
            if last_bos:
                self.log_event(f"[{symbol} M15] Break of Structure ({last_bos}) confirmed at {ms_m15.get('last_bos_level')} | BOS count: {ms_m15.get('consecutive_bos')}", "INFO", "SMC")
            if last_choch:
                self.log_event(f"[{symbol} M15] Change of Character (ChoCH) detected! Trend reversing to {last_choch}", "INFO", "SMC")

            obs = self.order_blocks.update(candles)
            fvgs = self.fvg.update(candles)
            self.context["obs"] = obs
            self.context["fvgs"] = fvgs
            self.context["liquidity"] = self.liquidity.update(candles, ms_m15["swings"])
            if self.asian_range:
                self.asian_range.update(candles)

        # ── LAYER 1: Check H4 bias ──
        htf_bias = self.context.get("htf_bias", "NEUTRAL")
        
        # Apply manual bias override if specified
        is_manual_override = False
        manual_bias = self.smc_params.manual_bias_overrides.get(symbol)
        if manual_bias and manual_bias.upper() in ["BULLISH", "BEARISH", "NEUTRAL"]:
            htf_bias = manual_bias.upper()
            is_manual_override = True
            
        if htf_bias == "NEUTRAL":
            return None

        # ── LAYER 2: Check H1 bias agrees + 2+ BOS confirmed ──
        h1 = self.context.get("h1", {})
        h1_trend = h1.get("trend", "NEUTRAL")

        # Double-confirm: H1 must agree with H4 (unless manually overridden)
        if not is_manual_override and h1_trend != htf_bias:
            return None

        # Must have 2+ BOS on H1 (unless manually overridden)
        if not is_manual_override and not h1.get("trend_confirmed", False):
            return None



        # ── LAYER 3: M15 ChoCH ──
        if timeframe == "M15":
            ms_m15 = self.context.get("ltf_structure", {})
            
            # Check if M15 trend has shifted to align with H1 bias (ChoCH occurred)
            m15_trend = ms_m15.get("trend")
            if m15_trend != htf_bias:
                return None  # M15 structure not aligned yet
                
            obs = self.context.get("obs", [])
            fvgs = self.context.get("fvgs", [])
            last_choch = ms_m15.get("last_choch")
            
            if not last_choch:
                return None

            
            # Supply & Demand Context
            in_sd_zone = False
            if self.supply_demand:
                sd_zones = self.supply_demand.update(candles)
                current_price = float(candles.iloc[-1]["close"])
                if htf_bias == "BULLISH":
                    for d in sd_zones.get("demand", []):
                        if float(d["bottom"]) <= current_price <= float(d["top"]):
                            in_sd_zone = True
                            break
                elif htf_bias == "BEARISH":
                    for s in sd_zones.get("supply", []):
                        if float(s["bottom"]) <= current_price <= float(s["top"]):
                            in_sd_zone = True
                            break
            self.context["in_sd_zone"] = in_sd_zone
            if last_choch and (obs or fvgs):
                ob_str = f"{len(obs)} Order Block(s)" if obs else ""
                fvg_str = f"{len(fvgs)} FVG(s)" if fvgs else ""
                zones = " and ".join(filter(None, [ob_str, fvg_str]))
                self.log_event(f"[{symbol} M15] Entry Zones Detected: {zones} identified near ChoCH.", "INFO", "SMC")

            # ── HARD FILTER: Killzones (Non-Synthetics Only) ──
            current_price = float(candles.iloc[-1]["close"])
            from backend.risk.compounding import get_instrument_profile
            profile = get_instrument_profile(symbol)
            instrument_type = profile.instrument_type if profile else "FOREX"
            
            if self.smc_params.session_filter_enabled and instrument_type != "SYNTHETIC":
                current_time_val = candles.iloc[-1]["time"] if "time" in candles.columns else (candles.index[-1].timestamp() if hasattr(candles.index[-1], 'timestamp') else float(candles.iloc[-1].get("time", 0)))
                if current_time_val:
                    from backend.utils.timeutils import is_kill_zone
                    dt_kz = datetime.fromtimestamp(current_time_val, timezone.utc)
                    if not is_kill_zone(dt_kz):
                        self.log_event(f"[{symbol} M15] Rejected by Killzone filter.", "DEBUG", "SMC")
                        return None

            # ── HARD FILTER: Asian Range Sweep ──
            if self.smc_params.enforce_asian_range_sweep and self.asian_range and self.asian_range.is_mapped and instrument_type != "SYNTHETIC":
                if not self.asian_range.check_sweep(current_price, htf_bias):
                    self.log_event(f"[{symbol} M15] Rejected: Asian Range not swept yet.", "DEBUG", "SMC")
                    return None

            # ── SOFT FILTER: FVG Displacement ──
            if self.smc_params.enforce_fvg_displacement:
                if not fvgs:
                    self.log_event(f"[{symbol} M15] Warning: No FVG Displacement found with ChoCH. Proceeding to fallback zones.", "DEBUG", "SMC")
                    # We no longer reject here; we allow fallback to Order Blocks or Fibs

            # ── HARD FILTER: Premium/Discount (PD) Array ──
            in_ote = False
            if self.premium_discount and len(ms_m15.get("swings", [])) >= 2:
                # Use H4 swings to find HTF dealing range
                htf_swings = self.context.get("htf", {}).get("swings", [])
                if len(htf_swings) >= 2:
                    h_high = max(float(s["price"]) for s in htf_swings[-4:])
                    h_low = min(float(s["price"]) for s in htf_swings[-4:])
                    pd_zones = self.premium_discount.calculate(h_high, h_low)
                    
                    if pd_zones:
                        eq = pd_zones.get("equilibrium", 0)
                        if self.smc_params.enforce_htf_pd:
                            if htf_bias == "BULLISH" and current_price > eq:
                                self.log_event(f"[{symbol} M15] Rejected: Price is in Premium (Buying high).", "DEBUG", "SMC")
                                return None
                            elif htf_bias == "BEARISH" and current_price < eq:
                                self.log_event(f"[{symbol} M15] Rejected: Price is in Discount (Selling low).", "DEBUG", "SMC")
                                return None
                        
                        # Set in_ote for scoring
                        if htf_bias == "BULLISH":
                            in_ote = pd_zones["ote_long_bottom"] <= current_price <= pd_zones["ote_long_top"]
                        else:
                            in_ote = pd_zones["ote_short_bottom"] <= current_price <= pd_zones["ote_short_top"]

            self.context["in_ote_zone"] = in_ote

            return None  # Wait for M5 candlestick confirmation

        # ── LAYER 4: M5 Candlestick Confirmation ──
        if timeframe == "M5":
            self.context["_last_m5_candles"] = candles
            pattern = detect_confirmation_pattern(candles, bias=htf_bias)
            if not pattern:
                return None

            # ── Build scorer context (Issue #19 fix) ──
            scorer_context = self._build_scorer_context(symbol, htf_bias, pattern)
            
            # ── Calculate Entry, SL, TP ──
            entry_price = float(candles['close'].iloc[-1])
            scorer_context["entry_price"] = entry_price
            
            # SL = M15 previous swing extreme + buffer
            ms_m15 = self.context.get("ltf_structure", {})
            m15_swings = ms_m15.get("swings", [])
            
            # TP = H1 structural reversal point (from strategy2.md)
            ms_h1 = self.context.get("h1", {})
            h1_swings = ms_h1.get("swings", [])
            
            # Fallback values
            sl = entry_price * 0.99 if htf_bias == "BULLISH" else entry_price * 1.01
            tp = entry_price * 1.03 if htf_bias == "BULLISH" else entry_price * 0.97
            
            pip_size = get_pip_size(symbol)
            buffer = 5.0 * pip_size

            if htf_bias == "BULLISH":
                # SL Priority 1: Last M15 swing low below entry (structural)
                lows = [s for s in m15_swings if s["type"] == "LOW" and float(s["price"]) < entry_price]
                if lows:
                    sl = float(lows[-1]["price"]) - buffer
                else:
                    # SL Priority 2: OB bottom
                    obs_ctx = self.context.get("obs", [])
                    bullish_obs = [ob for ob in obs_ctx if ob.get("type") == "BULLISH"]
                    if bullish_obs:
                        sl = float(bullish_obs[-1].get("bottom", entry_price * 0.99)) - buffer
                    else:
                        # SL Priority 3: Fallback — confirmation candle low
                        sl = float(candles.iloc[-1]["low"]) - buffer

                # TP: nearest H1 swing HIGH above entry (closest resistance)
                h1_highs = [s for s in h1_swings if s["type"] == "HIGH" and float(s["price"]) > entry_price]
                if h1_highs:
                    tp = float(min(h1_highs, key=lambda s: float(s["price"]))["price"])
            else:
                # SL Priority 1: Last M15 swing high above entry (structural)
                highs = [s for s in m15_swings if s["type"] == "HIGH" and float(s["price"]) > entry_price]
                if highs:
                    sl = float(highs[-1]["price"]) + buffer
                else:
                    # SL Priority 2: OB top
                    obs_ctx = self.context.get("obs", [])
                    bearish_obs = [ob for ob in obs_ctx if ob.get("type") == "BEARISH"]
                    if bearish_obs:
                        sl = float(bearish_obs[-1].get("top", entry_price * 1.01)) + buffer
                    else:
                        # SL Priority 3: Fallback — confirmation candle high
                        sl = float(candles.iloc[-1]["high"]) + buffer

                # TP: nearest H1 swing LOW below entry (closest support)
                h1_lows = [s for s in h1_swings if s["type"] == "LOW" and float(s["price"]) < entry_price]
                if h1_lows:
                    tp = float(max(h1_lows, key=lambda s: float(s["price"]))["price"])
                    
            scorer_context["stop_loss"] = sl
            scorer_context["tp1_price"] = tp

            # Use cached H1 ATR instead of dynamic M5 calculation
            atr = self.context.get("h1_atr", candles.iloc[-1]["high"] - candles.iloc[-1]["low"])
            scorer_context["atr"] = atr

            # === Generate Markings for Frontend ===
            markings = []
            m15_obs = self.context.get("obs", [])
            m15_fvgs = self.context.get("fvgs", [])
            m15_swings = self.context.get("ltf_structure", {}).get("swings", [])
            
            # Helper to extract time safely
            def _get_time(obj):
                idx = obj.get("index")
                if hasattr(idx, "timestamp"):
                    return int(idx.timestamp())
                return int(idx) if idx is not None else 0

            # Mark OBs
            for ob in m15_obs:
                markings.append({
                    "type": "OB",
                    "timeframe": "M15",
                    "top": float(ob["top"]),
                    "bottom": float(ob["bottom"]),
                    "start_time": _get_time(ob),
                    "end_time": int(candles.iloc[-1]["time"]) if 'time' in candles.columns else _get_time({"index": candles.index[-1]}),
                    "color": "rgba(59, 130, 246, 0.2)" if ob["type"] == "BULLISH" else "rgba(239, 68, 68, 0.2)",
                    "text": f"M15 OB ({ob['type']})"
                })
                
            # Mark FVGs
            for fvg in m15_fvgs:
                markings.append({
                    "type": "FVG",
                    "timeframe": "M15",
                    "top": float(fvg["top"]),
                    "bottom": float(fvg["bottom"]),
                    "start_time": _get_time(fvg),
                    "end_time": int(candles.iloc[-1]["time"]) if 'time' in candles.columns else _get_time({"index": candles.index[-1]}),
                    "color": "rgba(234, 179, 8, 0.2)",
                    "text": "M15 FVG"
                })
                
            # Mark CHOCH / BOS
            # The structure logic identifies these as swings
            for i, s in enumerate(m15_swings):
                if i > 0:
                    prev_s = m15_swings[i-1]
                    if s["type"] == "HIGH" and prev_s["type"] == "LOW" and float(s["price"]) > float(m15_swings[i-2]["price"]) if i>=2 else False:
                        text = "BOS"
                    else:
                        text = "Swing"
                    markings.append({
                        "type": "STRUCTURE",
                        "timeframe": "M15",
                        "price": float(s["price"]),
                        "time": int(s["time"]),
                        "text": text
                    })
                    
            # H1 Markings (HTF Context)
            for ob in self.context.get("h1", {}).get("obs", []):
                markings.append({
                    "type": "OB",
                    "timeframe": "H1",
                    "top": float(ob["top"]),
                    "bottom": float(ob["bottom"]),
                    "start_time": _get_time(ob),
                    "end_time": int(candles.iloc[-1]["time"]) if 'time' in candles.columns else _get_time({"index": candles.index[-1]}),
                    "color": "rgba(16, 185, 129, 0.2)" if ob["type"] == "BULLISH" else "rgba(249, 115, 22, 0.2)",
                    "text": f"H1 OB ({ob['type']})"
                })
                
            h1_swings = self.context.get("h1", {}).get("swings", [])
            for i, s in enumerate(h1_swings):
                if i > 0:
                    prev_s = h1_swings[i-1]
                    if s["type"] == "HIGH" and prev_s["type"] == "LOW" and float(s["price"]) > float(h1_swings[i-2]["price"]) if i>=2 else False:
                        text = "H1 BOS/CHOCH"
                    else:
                        text = "H1 Swing"
                    markings.append({
                        "type": "STRUCTURE",
                        "timeframe": "H1",
                        "price": float(s["price"]),
                        "time": int(s["time"]),
                        "text": text
                    })
                
            scorer_context["markings"] = markings


            # ── Score ──
            score = self.scorer.calculate_score(scorer_context)

            # ── Generate signal if score meets threshold ──
            if score >= self.smc_params.min_signal_score:
                self.log_event(f"[{symbol} M5] Candlestick Confirmation: {pattern.name} (Tier {pattern.tier}) detected.", "INFO", "SMC")
                
                breakdown = scorer_context.get("score_breakdown", {})
                breakdown_str = ", ".join(f"{k}: {v}" for k, v in breakdown.items() if v > 0)
                self.log_event(f"[{symbol} M5] 🎯 SIGNAL VALIDATED! Score: {score}/100. Direction: {htf_bias} | Confluences: {breakdown_str}", "SIGNAL", "SMC")
                
                sig = self.signal_gen.generate(scorer_context, score)
                if sig:
                    # Generate Base64 Snapshot
                    from backend.analytics.snapshots import generate_trade_snapshot_b64
                    try:
                        # Slice to last 80 candles to prevent OOM / blocking warning
                        snapshot_candles = candles.iloc[-80:].copy()
                        
                        b64 = await asyncio.to_thread(
                            generate_trade_snapshot_b64,
                            symbol=symbol,
                            timeframe="M5",
                            candles=snapshot_candles,
                            order_blocks=self.context.get("obs", []),
                            fvgs=self.context.get("fvgs", []),
                            entry_price=sig.entry_price,
                            stop_loss=sig.stop_loss,
                            take_profit=sig.take_profit,
                            direction=sig.direction,
                            snapshot_type="ENTRY",
                            trade_id="signal_preview"
                        )
                        if b64:
                            sig.metadata["entry_snapshot_b64"] = b64
                    except Exception as e:
                        logger.error(f"Base64 snapshot generation failed: {e}")

                    return sig

        return None

    async def on_tick(self, symbol: str, tick: Dict[str, Any]) -> Optional[List[TradeAction]]:
        """Optional intra-bar management (e.g. precise trailing)."""
        return []

    def _build_scorer_context(self, symbol: str, bias: str, pattern) -> Dict[str, Any]:
        """
        Translate sub-module outputs into the exact keys ConfluenceScorer expects.
        Fixes Issue #12/#19: scorer was getting wrong keys and scoring near-zero.
        """
        htf = self.context.get("htf", {})
        h1 = self.context.get("h1", {})
        obs = self.context.get("obs", [])
        fvgs = self.context.get("fvgs", [])
        liq = self.context.get("liquidity", {})

        # Find fresh OB in bias direction
        fresh_ob = None
        for ob in reversed(obs):
            if ob.get("type") == bias and ob.get("touches", 99) == 0:
                fresh_ob = ob
                break

        # Check if any FVG in bias direction is present
        fvg_present = False
        fvg_inside_ob = False
        for fvg in fvgs:
            if fvg.get("type") == bias:
                fvg_present = True
                if fresh_ob:
                    ob_high = fresh_ob.get("top", 0)
                    ob_low = fresh_ob.get("bottom", 0)
                    fvg_mid = (fvg.get("high", 0) + fvg.get("low", 0)) / 2
                    if ob_low <= fvg_mid <= ob_high:
                        fvg_inside_ob = True

        # Kill zone check — use the candle's own timestamp so backtests
        # evaluate the historical session, not today's current time.
        try:
            from datetime import timezone as _tz
            _candles = self.context.get("_last_m5_candles")
            if _candles is not None and len(_candles) > 0:
                _bar_idx = _candles.index[-1]
                if hasattr(_bar_idx, 'to_pydatetime'):
                    _bar_dt = _bar_idx.to_pydatetime()
                    if _bar_dt.tzinfo is None:
                        _bar_dt = _bar_dt.replace(tzinfo=_tz.utc)
                    _bar_ts = int(_bar_dt.timestamp())
                else:
                    _bar_ts = int(float(_bar_idx))
            else:
                _bar_ts = None
            session = detect_session(_bar_ts) if _bar_ts is not None else "UNKNOWN"
            in_kill_zone = session in ("LONDON", "NY", "LONDON/NY")
        except Exception:
            in_kill_zone = False

        # Candle tier from pattern
        candle_tier = 0
        if pattern:
            tier_obj = getattr(pattern, 'tier', 0)
            if hasattr(tier_obj, 'value'):
                candle_tier = tier_obj.value
            elif isinstance(tier_obj, str):
                candle_tier = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3}.get(tier_obj, 0)
            else:
                candle_tier = tier_obj

        return {
            "symbol": symbol,
            "signal_direction": bias,
            "htf_bias": htf.get("trend", "NEUTRAL"),
            "h1_structure": h1.get("trend", "NEUTRAL"),
            "liquidity_sweep": liq.get("recent_sweep"),
            "fresh_ob": fresh_ob,
            "fvg_present": fvg_present,
            "fvg_inside_ob": fvg_inside_ob,
            "in_ote_zone": self.context.get("in_ote_zone", False),
            "in_sd_zone": self.context.get("in_sd_zone", False),
            "candle_tier": candle_tier,
            "ltf_choch": self.context.get("ltf_structure", {}).get("last_choch") == bias,
            "in_kill_zone": in_kill_zone,
            "is_backtesting": self.is_backtesting,
        }
