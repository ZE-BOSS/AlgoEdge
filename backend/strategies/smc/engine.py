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
from typing import Dict, Any, List, Optional

from backend.strategies.base_strategy import BaseStrategy, TradeSignal, TradeAction
from backend.strategies.registry import register_strategy
from backend.strategies.smc.params import UserConfig

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

        swing_len_htf = self.smc_params.swing_length_htf
        swing_len_ltf = self.smc_params.swing_length_ltf

        # Layer 1: H4 bias detector
        self.htf_structure = MarketStructureDetector(
            swing_length=swing_len_htf, min_bos_count=2
        )

        # Layer 2: H1 BOS + structure detector
        self.h1_structure = MarketStructureDetector(
            swing_length=swing_len_htf, min_bos_count=2
        )

        # Layer 3: M15 ChoCH detector
        self.ltf_structure = MarketStructureDetector(
            swing_length=swing_len_ltf, min_bos_count=2
        )

        # Sub-module detectors
        self.order_blocks = OrderBlockDetector()
        self.fvg = FVGDetector(self.smc_params.fvg_min_gap_pips)
        self.liquidity = LiquidityMapper(self.smc_params.liq_sweep_min_pips)
        self.scorer = ConfluenceScorer(self.smc_params)
        self.signal_gen = SignalGenerator(user_config)
        self.ipdm = IPDMDetector()

        # Optional modules
        self.premium_discount = PremiumDiscountCalculator() if PremiumDiscountCalculator else None
        self.supply_demand = SupplyDemandDetector() if SupplyDemandDetector else None

        # State
        self.context: Dict[str, Any] = {}
        
        # State tracking for frontend logs to prevent log spam
        self.last_logged_htf_bias = None
        self.last_logged_h1_trend = None
        self.last_logged_phase = None

    def get_required_timeframes(self) -> List[str]:
        return ["H4", "H1", "M15", "M5"]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> Optional[TradeSignal]:
        """
        Main evaluation loop — processes each timeframe layer.
        Called on M15 bar close with multi-TF data.
        """
        logger.debug(f"SMC Engine evaluating {symbol} on {timeframe}")

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
                bot_service.log_system_event(f"[{symbol} H4] Break of Structure ({last_bos}) confirmed at {ms_h4.get('last_bos_level')} | BOS count: {ms_h4.get('consecutive_bos')}", "INFO", "SMC")
            if last_choch:
                bot_service.log_system_event(f"[{symbol} H4] Change of Character (ChoCH) detected! Trend reversing to {last_choch}", "INFO", "SMC")

            # Log HTF Bias changes
            if self.bias != "NEUTRAL" and self.bias != self.last_logged_htf_bias:
                bot_service.log_system_event(f"[{symbol} H4] HTF Bias shifted to {self.bias}", "INFO", "SMC")
                self.last_logged_htf_bias = self.bias

            return None
            
        elif timeframe == "H1":
            ms_h1 = self.h1_structure.update(candles)
            self.context["h1"] = ms_h1
            
            # Granular H1 logging
            last_bos = ms_h1.get("last_bos")
            last_choch = ms_h1.get("last_choch")
            if last_bos:
                bot_service.log_system_event(f"[{symbol} H1] Break of Structure ({last_bos}) confirmed at {ms_h1.get('last_bos_level')} | BOS count: {ms_h1.get('consecutive_bos')}", "INFO", "SMC")
            if last_choch:
                bot_service.log_system_event(f"[{symbol} H1] Change of Character (ChoCH) detected! Trend reversing to {last_choch}", "INFO", "SMC")

            # Check if H1 trend aligns with H4
            h1_trend = ms_h1.get("trend", "NEUTRAL")
            if h1_trend != self.last_logged_h1_trend:
                if h1_trend != "NEUTRAL" and h1_trend == self.bias:
                    bot_service.log_system_event(f"[{symbol} H1] Structure aligned with H4 Bias ({self.bias})", "INFO", "SMC")
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
                    bot_service.log_system_event(f"[{symbol} IPDM] Entered EXPANSION phase! Hunting for entries...", "INFO", "SMC")
                else:
                    bot_service.log_system_event(f"[{symbol} IPDM] Entered {current_phase} phase. Waiting...", "INFO", "SMC")
                self.last_logged_phase = current_phase

            return None

        # ── LAYER 1: Check H4 bias ──
        htf_bias = self.context.get("htf_bias", "NEUTRAL")
        if htf_bias == "NEUTRAL":
            return None

        # ── LAYER 2: Check H1 bias agrees + 2+ BOS confirmed ──
        h1 = self.context.get("h1", {})
        h1_trend = h1.get("trend", "NEUTRAL")

        # Double-confirm: H1 must agree with H4
        if h1_trend != htf_bias:
            return None

        # Must have 2+ BOS on H1
        if not h1.get("trend_confirmed", False):
            return None

        # ── IPDM Phase Gate ── 
        ipdm = self.context.get("ipdm", {})
        ipdm_phase = ipdm.get("phase", "UNKNOWN")
        if ipdm_phase in ("ACCUMULATION", "MANIPULATION"):
            # Don't enter during accumulation or active manipulation
            if not ipdm.get("manipulation_completed", False):
                return None

        # ── LAYER 3: M15 ChoCH ──
        if timeframe == "M15":
            ms_m15 = self.ltf_structure.update(candles)
            self.context["ltf_structure"] = ms_m15

            # Granular M15 logging
            last_bos = ms_m15.get("last_bos")
            last_choch = ms_m15.get("last_choch")
            if last_bos:
                bot_service.log_system_event(f"[{symbol} M15] Break of Structure ({last_bos}) confirmed at {ms_m15.get('last_bos_level')} | BOS count: {ms_m15.get('consecutive_bos')}", "INFO", "SMC")
            if last_choch:
                bot_service.log_system_event(f"[{symbol} M15] Change of Character (ChoCH) detected! Trend reversing to {last_choch}", "INFO", "SMC")

            # Check if M15 trend has shifted to align with H1 bias (ChoCH occurred)
            m15_trend = ms_m15.get("trend")
            if m15_trend != htf_bias:
                return None  # M15 structure not aligned yet

            # Update entry zone detectors on M15
            obs = self.order_blocks.update(candles)
            fvgs = self.fvg.update(candles)
            self.context["obs"] = obs
            self.context["fvgs"] = fvgs
            self.context["liquidity"] = self.liquidity.update(
                candles, ms_m15["swings"]
            )
            
            if last_choch and (obs or fvgs):
                ob_str = f"{len(obs)} Order Block(s)" if obs else ""
                fvg_str = f"{len(fvgs)} FVG(s)" if fvgs else ""
                zones = " and ".join(filter(None, [ob_str, fvg_str]))
                bot_service.log_system_event(f"[{symbol} M15] Entry Zones Detected: {zones} identified near ChoCH.", "INFO", "SMC")

            # Check OTE zone if premium_discount module exists
            in_ote = False
            if self.premium_discount:
                try:
                    ote = self.premium_discount.update(candles)
                    in_ote = ote.get("in_ote_zone", False)
                except Exception:
                    pass
            self.context["in_ote_zone"] = in_ote

            return None  # Wait for M5 candlestick confirmation

        # ── LAYER 4: M5 Candlestick Confirmation ──
        if timeframe == "M5":
            pattern = detect_confirmation_pattern(candles, bias=htf_bias)
            if not pattern:
                return None

            # ── Build scorer context (Issue #19 fix) ──
            scorer_context = self._build_scorer_context(htf_bias, pattern)
            
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
            
            if htf_bias == "BULLISH":
                # Find last swing low on M15 for SL
                lows = [s for s in m15_swings if s["type"] == "LOW"]
                if lows:
                    # Buffer of ~3 pips
                    sl = float(lows[-1]["price"]) * 0.9995  
                
                # TP targets the H1 Swing High (origin of the ChoCH pullback)
                h1_highs = [s for s in h1_swings if s["type"] == "HIGH"]
                if h1_highs:
                    tp = float(h1_highs[-1]["price"])
            else:
                # Find last swing high on M15 for SL
                highs = [s for s in m15_swings if s["type"] == "HIGH"]
                if highs:
                    sl = float(highs[-1]["price"]) * 1.0005
                
                # TP targets the H1 Swing Low
                h1_lows = [s for s in h1_swings if s["type"] == "LOW"]
                if h1_lows:
                    tp = float(h1_lows[-1]["price"])
                    
            scorer_context["stop_loss"] = sl
            scorer_context["tp1_price"] = tp

            # ── Score ──
            score = self.scorer.calculate_score(scorer_context)

            # ── Generate signal if score meets threshold ──
            if score >= self.smc_params.min_signal_score:
                bot_service.log_system_event(f"[{symbol} M5] Candlestick Confirmation: {pattern.name} (Tier {pattern.tier}) detected.", "INFO", "SMC")
                
                breakdown = scorer_context.get("score_breakdown", {})
                breakdown_str = ", ".join(f"{k}: {v}" for k, v in breakdown.items() if v > 0)
                bot_service.log_system_event(f"[{symbol} M5] 🎯 SIGNAL VALIDATED! Score: {score}/100. Direction: {htf_bias} | Confluences: {breakdown_str}", "SIGNAL", "SMC")
                
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

    def _build_scorer_context(self, bias: str, pattern) -> Dict[str, Any]:
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

        # Check if any FVG is inside an OB
        fvg_inside_ob = False
        if fresh_ob and fvgs:
            ob_high = fresh_ob.get("high", 0)
            ob_low = fresh_ob.get("low", 0)
            for fvg in fvgs:
                fvg_mid = (fvg.get("high", 0) + fvg.get("low", 0)) / 2
                if ob_low <= fvg_mid <= ob_high:
                    fvg_inside_ob = True
                    break

        # Kill zone check
        try:
            session = detect_session()
            in_kill_zone = session in ("LONDON", "NY", "LONDON/NY")
        except Exception:
            in_kill_zone = False

        # Candle tier from pattern
        candle_tier = 0
        if pattern:
            candle_tier = getattr(pattern, 'tier', 0)
            if isinstance(candle_tier, str):
                candle_tier = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3}.get(candle_tier, 0)

        return {
            "signal_direction": bias,
            "htf_bias": htf.get("trend", "NEUTRAL"),
            "h1_structure": h1.get("trend", "NEUTRAL"),
            "liquidity_sweep": liq.get("recent_sweep"),
            "fresh_ob": fresh_ob,
            "fvg_inside_ob": fvg_inside_ob,
            "in_ote_zone": self.context.get("in_ote_zone", False),
            "candle_tier": candle_tier,
            "ltf_choch": True,  # We only reach here if ChoCH was confirmed
            "in_kill_zone": in_kill_zone,
        }
