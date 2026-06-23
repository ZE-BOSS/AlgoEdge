"""
backend/strategies/smc/engine.py

Main SMC Orchestrator (6-step sniper entry logic).
Integrates all SMC sub-components.
Source: SMC_Strategy.md
"""

import pandas as pd
from typing import Dict, Any, Optional, List
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

from backend.utils.logger import get_logger
logger = get_logger(__name__)


@register_strategy("SMC_v1")
class SMCEngine(BaseStrategy):
    """
    Core Smart Money Concepts trading strategy engine.
    """

    def __init__(self, user_config: UserConfig):
        super().__init__(user_config)
        self.smc_params = user_config.smc
        
        # Initialize sub-modules
        self.structure = MarketStructureDetector(self.smc_params.swing_length_ltf)
        self.order_blocks = OrderBlockDetector()
        self.fvg = FVGDetector(self.smc_params.fvg_min_gap_pips)
        self.liquidity = LiquidityMapper(self.smc_params.liq_sweep_min_pips)
        self.scorer = ConfluenceScorer(self.smc_params)
        self.signal_gen = SignalGenerator(self.smc_params)
        
        # State
        self.context: Dict[str, Any] = {}

    def get_required_timeframes(self) -> List[str]:
        return self.smc_params.timeframes

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> Optional[TradeSignal]:
        """
        Main evaluation loop called on M15 bar close.
        Runs the 6-step entry logic.
        """
        logger.debug(f"SMC Engine evaluating {symbol} on {timeframe}")
        
        # 1. Update Market Structure & Bias
        self.context["structure"] = self.structure.update(candles)
        
        # 2. Update OBs and FVGs
        self.context["obs"] = self.order_blocks.update(candles)
        self.context["fvgs"] = self.fvg.update(candles)
        
        # 3. Update Liquidity Map
        self.context["liquidity"] = self.liquidity.update(candles, self.context["structure"]["swings"])
        
        # 4. Check Candlestick Confirmation
        pattern = detect_confirmation_pattern(candles, bias=self.structure.get_bias())
        self.context["pattern"] = pattern
        
        # 5. Calculate Confluence Score
        score = self.scorer.calculate_score(self.context)
        
        # 6. Generate Signal (Gates will validate inside)
        return self.signal_gen.generate(self.context, score)

    async def on_tick(self, symbol: str, tick: Dict[str, Any]) -> Optional[List[TradeAction]]:
        """Optional intra-bar management (e.g. precise trailing)."""
        return []
