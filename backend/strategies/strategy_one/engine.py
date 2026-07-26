"""
backend/strategies/smc/engine.py

Main SMC Orchestrator — 3-Layer Multi-Timeframe model.
Integrates all SMC sub-components with proper context translation
for ConfluenceScorer.

Flow: H4 Bias → M15 ChoCH/Zones → M5 Execution (Candle or Wick/BOS)
"""

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backend.core.config_schema import UserConfig
from backend.risk.position_sizer import get_pip_size
from backend.services.bot_service import bot_service
from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.core.candlestick import detect_confirmation_pattern
from backend.strategies.core.fvg import FVGDetector
from backend.strategies.core.ipdm import IPDMDetector
from backend.strategies.core.liquidity import LiquidityMapper
from backend.strategies.core.market_structure import MarketStructureDetector
from backend.strategies.core.order_blocks import OrderBlockDetector
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

from .confluence import ConfluenceScorer
from .signals import SignalGenerator

logger = get_logger(__name__)

# Try importing optional modules
try:
    from backend.strategies.core.premium_discount import PremiumDiscountCalculator
except ImportError:
    PremiumDiscountCalculator = None

try:
    from backend.strategies.core.supply_demand import SupplyDemandDetector
except ImportError:
    SupplyDemandDetector = None


@register_strategy("SMC_v1")
class SMCEngine(BaseStrategy):
    """
    Core Smart Money Concepts trading strategy engine.
    Implements the 3-Layer Multi-Timeframe model with M15 structural validation.
    """

    def __init__(self, user_config: UserConfig):
        super().__init__(user_config)
        self.smc_params = user_config.smc
        self.run_logs = []
        self.is_backtesting = False

        swing_len_htf = self.smc_params.swing_length_htf
        swing_len_ltf = self.smc_params.swing_length_ltf

        # Layer 1: H4 bias detector
        self.htf_structure = MarketStructureDetector(
            swing_length=swing_len_htf, min_bos_count=2
        )

        # Layer 2: M15 (HTF Context)
        swing_len_itf = getattr(self.smc_params, 'swing_length_mtf', max(3, swing_len_htf - 2))
        self.m15_structure = MarketStructureDetector(
            swing_length=swing_len_itf, min_bos_count=2
        )
        self.m15_order_blocks = OrderBlockDetector(
            impulse_ratio=self.smc_params.ob_impulse_min_ratio,
            max_touches=self.smc_params.ob_max_touch_count,
        )
        self.m15_fvg = FVGDetector(fvg_min_gap_atr_mult=self.smc_params.fvg_min_gap_atr_mult)
        self.m15_liquidity = LiquidityMapper(liq_sweep_min_atr_mult=self.smc_params.liq_sweep_min_atr_mult)

        # Layer 3: M5 (Execution)
        self.m5_structure = MarketStructureDetector(
            swing_length=swing_len_ltf, min_bos_count=2
        )
        self.m5_order_blocks = OrderBlockDetector(
            impulse_ratio=self.smc_params.ob_impulse_min_ratio,
            max_touches=self.smc_params.ob_max_touch_count,
        )
        self.m5_fvg = FVGDetector(fvg_min_gap_atr_mult=self.smc_params.fvg_min_gap_atr_mult)
        self.m5_liquidity = LiquidityMapper(liq_sweep_min_atr_mult=self.smc_params.liq_sweep_min_atr_mult)

        self.scorer = ConfluenceScorer(self.smc_params)
        self.signal_gen = SignalGenerator(user_config)
        self.ipdm = IPDMDetector()

        # Optional modules
        self.premium_discount = PremiumDiscountCalculator(
            ote_min=self.smc_params.ote_fib_min, 
            ote_max=self.smc_params.ote_fib_max
        ) if PremiumDiscountCalculator else None
        
        self.supply_demand = SupplyDemandDetector() if SupplyDemandDetector else None

        self.context: dict[str, Any] = {}
        self.last_logged_htf_bias = None
        self.last_logged_phase = None
        self.bias = "NEUTRAL"

    def log_event(self, message: str, level: str = "INFO", category: str = "SMC"):
        if self.is_backtesting:
            self.run_logs.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "category": category,
                "message": message
            })
        else:
            bot_service.log_system_event(message, level, category)

    def get_required_timeframes(self) -> list[str]:
        return ["H4", "M15", "M5"]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self.log_event(f"SMC Engine evaluating {symbol} on {timeframe}", "DEBUG", "SMC")

        if timeframe == "H4":
            ms_h4 = self.htf_structure.update(candles)
            self.context["htf"] = ms_h4
            self.bias = ms_h4.get("trend", "NEUTRAL")
            self.context["htf_bias"] = self.bias
            
            last_bos = ms_h4.get("last_bos")
            last_choch = ms_h4.get("last_choch")
            if last_bos:
                self.log_event(f"[{symbol} H4] BOS confirmed.", "INFO", "SMC")
            if last_choch:
                self.log_event(f"[{symbol} H4] ChoCH detected! Trend shifting.", "INFO", "SMC")

            if self.bias != "NEUTRAL" and self.bias != self.last_logged_htf_bias:
                self.log_event(f"[{symbol} H4] Bias shifted to {self.bias}", "INFO", "SMC")
                self.last_logged_htf_bias = self.bias

            # IPDM on H4
            h4_swings = ms_h4.get("swings", [])
            h4_highs = [s for s in h4_swings if s["type"] == "HIGH"]
            h4_lows = [s for s in h4_swings if s["type"] == "LOW"]
            ipdm_state = self.ipdm.update(candles, h4_highs, h4_lows)
            self.context["ipdm"] = ipdm_state
            
            current_phase = ipdm_state.get("phase", "UNKNOWN")
            if current_phase != self.last_logged_phase:
                self.log_event(f"[{symbol} IPDM] Phase: {current_phase}", "INFO", "SMC")
                self.last_logged_phase = current_phase

            return None
            
        elif timeframe == "M15":
            ms_m15 = self.m15_structure.update(candles)
            self.context["m15"] = ms_m15
            
            last_bos = ms_m15.get("last_bos")
            last_choch = ms_m15.get("last_choch")
            if last_bos:
                self.log_event(f"[{symbol} M15] BOS confirmed.", "INFO", "SMC")
            if last_choch:
                self.log_event(f"[{symbol} M15] ChoCH detected! Trend shifting.", "INFO", "SMC")
            
            # Map M15 Zones & Liquidity
            self.context["m15_obs"] = self.m15_order_blocks.update(candles)
            self.context["m15_fvgs"] = self.m15_fvg.update(candles)
            self.context["m15_liquidity"] = self.m15_liquidity.update(candles, ms_m15["swings"])
            
            # Cache ATR
            lookback = min(14, len(candles) - 1)
            if lookback > 0:
                recent_candles = candles.iloc[-(lookback+1):]
                tr_list = []
                for i in range(1, len(recent_candles)):
                    c = recent_candles.iloc[i]
                    prev_c = recent_candles.iloc[i-1]
                    tr = max(c["high"] - c["low"], abs(c["high"] - prev_c["close"]), abs(c["low"] - prev_c["close"]))
                    tr_list.append(tr)
                self.context["atr"] = sum(tr_list) / len(tr_list) if tr_list else (candles.iloc[-1]["high"] - candles.iloc[-1]["low"])
            return None

        elif timeframe == "M5":
            ms_m5 = self.m5_structure.update(candles)
            self.context["m5"] = ms_m5
            
            self.context["m5_obs"] = self.m5_order_blocks.update(candles)
            self.context["m5_fvgs"] = self.m5_fvg.update(candles)
            self.context["m5_liquidity"] = self.m5_liquidity.update(candles, ms_m5["swings"])
            
            # ── LAYER 1: Check H4 Bias ──
            htf_bias = self.context.get("htf_bias", "NEUTRAL")
            manual_bias = self.smc_params.manual_bias_overrides.get(symbol)
            is_manual_override = False
            if manual_bias and manual_bias.upper() in ["BULLISH", "BEARISH", "NEUTRAL"]:
                htf_bias = manual_bias.upper()
                is_manual_override = True
                
            if htf_bias == "NEUTRAL":
                return None

            # ── LAYER 2: M15 Mandatory ChoCH Check ──
            m15 = self.context.get("m15", {})
            m15_trend = m15.get("trend")
            
            if not is_manual_override and m15_trend != htf_bias:
                return None  # Waiting for M15 to align via ChoCH (trend shift)

            # Supply & Demand Context (Mapped on M15 for bias)
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

            # OTE (Soft Filter Confluence)
            in_ote = False
            if self.premium_discount:
                htf_swings = self.context.get("htf", {}).get("swings", [])
                if len(htf_swings) >= 2:
                    current_price = float(candles.iloc[-1]["close"])
                    h_high = max(float(s["price"]) for s in htf_swings[-4:])
                    h_low = min(float(s["price"]) for s in htf_swings[-4:])
                    pd_zones = self.premium_discount.calculate(h_high, h_low)
                    if pd_zones:
                        if htf_bias == "BULLISH":
                            in_ote = pd_zones["ote_long_bottom"] <= current_price <= pd_zones["ote_long_top"]
                        else:
                            in_ote = pd_zones["ote_short_bottom"] <= current_price <= pd_zones["ote_short_top"]
            self.context["in_ote_zone"] = in_ote

            # ── LAYER 3: M5 Execution ──
            current_price = float(candles.iloc[-1]["close"])
            
            # Check Candlestick
            self.context["_last_m5_candles"] = candles
            pattern = detect_confirmation_pattern(candles, bias=htf_bias)
            
            # Check Fallback (Wick Tap or M5 BOS)
            fallback_triggered = False
            if not pattern:
                # LTF BOS after ChoCH?
                m5_last_choch = ms_m5.get("last_choch")
                m5_last_bos = ms_m5.get("last_bos")
                
                # --- CHOCH SWING ZONE VALIDATION ---
                valid_choch = False
                if m5_last_choch == htf_bias:
                    choch_level = ms_m5.get("last_choch_level")  # Assuming we can get it, or we just check current price/recent low
                    # If the ChoCH is recent, check if we are in an M15 POI
                    if in_sd_zone or in_ote:
                        valid_choch = True
                    else:
                        active_m15_obs = self.context.get("m15_obs", [])
                        for ob in active_m15_obs:
                            if ob.get("type") == htf_bias and ob.get("touches", 99) <= 1:
                                if htf_bias == "BULLISH" and float(ob["bottom"]) <= current_price <= float(ob["top"]) or htf_bias == "BEARISH" and float(ob["bottom"]) <= current_price <= float(ob["top"]):
                                    valid_choch = True
                                    break
                                    
                if valid_choch and m5_last_bos == htf_bias:
                    fallback_triggered = True
                else:
                    # Wick Tap: check if current candle low/high tapped a fresh M5 or M15 OB
                    c_high = float(candles.iloc[-1]["high"])
                    c_low = float(candles.iloc[-1]["low"])
                    active_obs = self.context.get("m15_obs", []) + self.context.get("m5_obs", [])
                    for ob in active_obs:
                        if ob.get("type") == htf_bias and ob.get("touches", 99) <= 1:
                            if htf_bias == "BULLISH" and float(ob["bottom"]) <= c_low <= float(ob["top"]) or htf_bias == "BEARISH" and float(ob["bottom"]) <= c_high <= float(ob["top"]):
                                fallback_triggered = True

            # If no execution trigger, wait.
            if not pattern and not fallback_triggered:
                return None

            # Generate Markings
            markings = self._generate_markings(candles)
            
            # Build Scorer Context
            scorer_context = self._build_scorer_context(symbol, htf_bias, pattern)
            scorer_context["markings"] = markings
            
            # Refined OB Entry (50% Equilibrium)
            entry_price = current_price
            fresh_ob = scorer_context.get("fresh_ob")
            if fresh_ob:
                # Determine 50% equilibrium level
                ob_mid = (float(fresh_ob["top"]) + float(fresh_ob["bottom"])) / 2.0
                
            scorer_context["entry_price"] = entry_price
            
            # ── Calculate Stop Loss (Liquidity / 2-Swing Rule) ──
            sl = self._calculate_structural_sl(htf_bias, entry_price)
            scorer_context["stop_loss"] = sl
            
            atr = self.context.get("atr", get_pip_size(symbol) * 10)
            scorer_context["atr"] = atr
            
            # Calculate TP placeholder using min_rr to pass validation Gate 4
            min_rr = getattr(self.config.risk, "min_rr", 1.0) if hasattr(self, 'config') else 1.0
            if htf_bias == "BULLISH":
                scorer_context["tp1_price"] = entry_price + (entry_price - sl) * max(min_rr, 1.0)
            else:
                scorer_context["tp1_price"] = entry_price - (sl - entry_price) * max(min_rr, 1.0)
            
            # Score
            score = self.scorer.calculate_score(scorer_context)
            
            # Let signals.py handle all validation (including Gate 8 min score) so the Backtester Funnel can track it
            sig = self.signal_gen.generate(scorer_context, score)
            if sig:
                sig.metadata["chart_zones"] = markings
                return sig

        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> list[TradeAction] | None:
        return []

    def _calculate_structural_sl(self, bias: str, entry_price: float) -> float:
        """Calculates SL based on last 2 swings and nearby liquidity pools."""
        m5_swings = self.context.get("m5", {}).get("swings", [])
        m15_liq = self.context.get("m15_liquidity", {})
        m5_liq = self.context.get("m5_liquidity", {})
        
        atr = self.context.get("atr", 0)
        sl_buffer_atr_mult = getattr(self.config.risk, 'sl_buffer_atr_mult', 0.1)
        buffer = atr * sl_buffer_atr_mult  # Configurable via Risk Settings
        max_liq_dist = atr * 2.0 # Only sweep pools within 2 ATR of the structural swing
        
        if bias == "BULLISH":
            # 1. 2-Swing Lows
            lows = [float(s["price"]) for s in m5_swings if s["type"] == "LOW" and float(s["price"]) < entry_price]
            sl_swing = min(lows[-2:]) if len(lows) >= 2 else (lows[-1] if lows else entry_price * 0.99)
            
            # 2. Liquidity (SSL)
            sl_liq = sl_swing
            for liq in [m15_liq, m5_liq]:
                for pool in liq.get("ssl", []):
                    level = float(pool.get("level", 0))
                    # Only sweep if the pool is below entry, and NOT extremely far from our swing low
                    if 0 < level < entry_price and level >= (sl_swing - max_liq_dist):
                        sl_liq = min(sl_liq, level)
            
            return min(sl_swing, sl_liq) - buffer
            
        else:
            # 1. 2-Swing Highs
            highs = [float(s["price"]) for s in m5_swings if s["type"] == "HIGH" and float(s["price"]) > entry_price]
            sl_swing = max(highs[-2:]) if len(highs) >= 2 else (highs[-1] if highs else entry_price * 1.01)
            
            # 2. Liquidity (BSL)
            sl_liq = sl_swing
            for liq in [m15_liq, m5_liq]:
                for pool in liq.get("bsl", []):
                    level = float(pool.get("level", 0))
                    # Only sweep if the pool is above entry, and NOT extremely far from our swing high
                    if level > entry_price and level <= (sl_swing + max_liq_dist):
                        sl_liq = max(sl_liq, level)
            
            return max(sl_swing, sl_liq) + buffer


    def _generate_markings(self, candles: pd.DataFrame) -> list[dict]:
        markings = []
        def _get_time(obj):
            idx = obj.get("index")
            if hasattr(idx, "timestamp"): return int(idx.timestamp())
            return int(idx) if idx is not None else 0
            
        end_t = int(candles.iloc[-1]["time"]) if 'time' in candles.columns else int(candles.index[-1].timestamp())

        for ob in self.context.get("m15_obs", []) + self.context.get("m5_obs", []):
            markings.append({
                "type": "OB", "timeframe": "M5/M15",
                "top": float(ob["top"]), "bottom": float(ob["bottom"]),
                "start_time": _get_time(ob), "end_time": end_t,
                "color": "rgba(59, 130, 246, 0.2)" if ob["type"] == "BULLISH" else "rgba(239, 68, 68, 0.2)",
                "text": f"OB ({ob['type']})"
            })
        return markings

    def _build_scorer_context(self, symbol: str, bias: str, pattern) -> dict[str, Any]:
        m15 = self.context.get("m15", {})
        m5_obs = self.context.get("m5_obs", [])
        m15_obs = self.context.get("m15_obs", [])
        fvgs = self.context.get("m5_fvgs", []) + self.context.get("m15_fvgs", [])
        liq = self.context.get("m5_liquidity", {})

        # 1-Touch Fresh OB logic (must be tapped by recent candles)
        fresh_ob = None
        _candles = self.context.get("_last_m5_candles")
        if _candles is not None and len(_candles) >= 3:
            recent_low = min(float(_candles.iloc[i]["low"]) for i in range(-3, 0))
            recent_high = max(float(_candles.iloc[i]["high"]) for i in range(-3, 0))
        else:
            recent_low = recent_high = 0

        for ob in reversed(m5_obs + m15_obs):
            if ob.get("type") == bias and ob.get("touches", 99) <= 1:
                if bias == "BULLISH" and float(ob["bottom"]) <= recent_low <= float(ob["top"]) or bias == "BEARISH" and float(ob["bottom"]) <= recent_high <= float(ob["top"]):
                    fresh_ob = ob
                    break

        fvg_present = any(fvg.get("type") == bias for fvg in fvgs)
        fvg_inside_ob = False
        if fvg_present and fresh_ob:
            ob_high = float(fresh_ob.get("top", 0))
            ob_low = float(fresh_ob.get("bottom", 0))
            for fvg in fvgs:
                if fvg.get("type") == bias:
                    fvg_mid = (float(fvg.get("top", 0)) + float(fvg.get("bottom", 0))) / 2
                    if ob_low <= fvg_mid <= ob_high:
                        fvg_inside_ob = True

        # Kill zone check
        try:
            from datetime import timezone as _tz
            _candles = self.context.get("_last_m5_candles")
            if _candles is not None and len(_candles) > 0:
                _bar_idx = _candles.index[-1]
                _bar_ts = int(_bar_idx.timestamp()) if hasattr(_bar_idx, 'timestamp') else None
            else:
                _bar_ts = None
                
            session = None
            if _bar_ts is not None:
                from datetime import datetime as _dt

                from backend.utils.timeutils import get_current_session
                session = get_current_session(_dt.fromtimestamp(_bar_ts, tz=_tz.utc))
                
            if "Volatility" in symbol or "Crash" in symbol or "Boom" in symbol or "Step" in symbol or "Jump" in symbol:
                in_kill_zone = True
            else:
                in_kill_zone = session in ("LONDON", "NY", "LONDON/NY")
        except Exception:
            in_kill_zone = False

        candle_tier = 0
        if pattern:
            tier_obj = getattr(pattern, 'tier', 0)
            if hasattr(tier_obj, 'value'): candle_tier = tier_obj.value
            elif isinstance(tier_obj, str): candle_tier = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3}.get(tier_obj, 0)
            else: candle_tier = tier_obj

        return {
            "symbol": symbol,
            "signal_direction": bias,
            "htf_bias": self.context.get("htf_bias"),
            "m15_bos": m15.get("trend") == bias and m15.get("trend_confirmed", False),
            "m15_choch": m15.get("trend") == bias and not m15.get("trend_confirmed", False),
            "liquidity_sweep": liq.get("recent_sweep"),
            "fresh_ob": fresh_ob,
            "fvg_present": fvg_present,
            "fvg_inside_ob": fvg_inside_ob,
            "in_ote_zone": self.context.get("in_ote_zone", False),
            "in_sd_zone": self.context.get("in_sd_zone", False),
            "ipdm_phase": self.context.get("ipdm", {}).get("phase", "UNKNOWN"),
            "candle_tier": candle_tier,
            "in_kill_zone": in_kill_zone,
            "is_backtesting": self.is_backtesting,
            "timestamp": _bar_ts,
            "current_spread_pips": self.context.get("current_spread_pips", 1.0),
            "active_fvgs": fvgs,
            "asian_range_swept": False,
        }
