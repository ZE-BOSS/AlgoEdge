"""
backend/services/llm_service.py

LLM analysis service: Claude + OpenAI + Gemini providers.
Source: TradingBot_MasterPlan-2.md Section 9
"""

from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    from google import genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False


# Default models per provider
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "gemini": "gemini-1.5-pro",
}

FAST_MODELS = {
    "anthropic": "claude-haiku-4-5-20250514",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-1.5-flash",
}


class LLMService:
    """Multi-provider LLM analysis service."""

    def __init__(self, api_keys: dict[str, str] | None = None):
        self.api_keys = api_keys or {}

    async def analyze_trade(
        self,
        trade: Any,
        provider: str = "anthropic",
        model: str | None = None,
    ) -> str:
        """
        Analyze a single trade post-close.
        Source: TradingBot_MasterPlan-2.md Section 9.2 — Single Trade Analysis
        """
        prompt = self._build_trade_prompt(trade)
        model = model or DEFAULT_MODELS.get(provider, "")
        return await self._call_provider(provider, model, prompt)

    async def analyze_series(
        self,
        trades: list,
        provider: str = "anthropic",
        model: str | None = None,
    ) -> str:
        """Analyze a series of recent trades."""
        prompt = self._build_series_prompt(trades)
        model = model or DEFAULT_MODELS.get(provider, "")
        return await self._call_provider(provider, model, prompt)

    async def custom_question(
        self,
        question: str,
        context_data: dict | None = None,
        provider: str = "anthropic",
    ) -> str:
        """Answer a custom user question about their trading data."""
        prompt = f"Trading context:\n{context_data}\n\nUser question: {question}"
        model = DEFAULT_MODELS.get(provider, "")
        return await self._call_provider(provider, model, prompt)

    def _build_trade_prompt(self, trade: Any) -> str:
        """Build structured prompt for single trade analysis."""
        return f"""Analyze this completed trade:

Symbol: {getattr(trade, 'symbol', 'N/A')}
Direction: {getattr(trade, 'direction', 'N/A')}
Entry: {getattr(trade, 'entry_price', 0)}
Exit: {getattr(trade, 'exit_price', 0)}
Stop Loss: {getattr(trade, 'stop_loss', 0)}
Take Profit: {getattr(trade, 'take_profit', 0)}
P&L: {getattr(trade, 'pnl', 0)}
Risk/Reward: {getattr(trade, 'risk_reward', 0)}
Exit Reason: {getattr(trade, 'exit_reason', 'N/A')}

Provide:
1. Entry quality score (1-10) and reasoning
2. SMC confluence assessment
3. Trade management review (BE, trailing, partial closes)
4. One specific actionable takeaway
5. Risk flag if any parameter violated best practices"""

    def _build_series_prompt(self, trades: list) -> str:
        """Build prompt for trade series analysis."""
        summary = "\n".join([
            f"Trade {i+1}: {t.get('symbol')} {t.get('direction')} P&L={t.get('pnl', 0):.2f}"
            for i, t in enumerate(trades)
        ])
        return f"""Analyze this series of {len(trades)} trades:\n\n{summary}\n
Provide:
1. Pattern detection across the series
2. Session performance breakdown
3. Correlation between confluence score and outcomes
4. Specific parameter adjustment recommendations"""

    async def _call_provider(self, provider: str, model: str, prompt: str) -> str:
        """Route to the correct LLM provider."""
        try:
            if provider == "anthropic" and HAS_ANTHROPIC:
                return await self._call_anthropic(model, prompt)
            elif provider == "openai" and HAS_OPENAI:
                return await self._call_openai(model, prompt)
            elif provider == "gemini" and HAS_GEMINI:
                return await self._call_gemini(model, prompt)
            else:
                return f"Provider '{provider}' not available. Install the SDK."
        except Exception as e:
            logger.error(f"LLM call failed ({provider}): {e}")
            return f"Analysis failed: {e!s}"

    async def _call_anthropic(self, model: str, prompt: str) -> str:
        """Call Anthropic Claude API."""
        client = anthropic.AsyncAnthropic(api_key=self.api_keys.get("anthropic", ""))
        message = await client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    async def _call_openai(self, model: str, prompt: str) -> str:
        """Call OpenAI API."""
        client = openai.AsyncOpenAI(api_key=self.api_keys.get("openai", ""))
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        return response.choices[0].message.content

    async def _call_gemini(self, model: str, prompt: str) -> str:
        """Call Google Gemini API."""
        client = genai.Client(api_key=self.api_keys.get("gemini", ""))
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt
        )
        return response.text
