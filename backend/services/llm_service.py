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


# Default models per provider.
#
# Two Anthropic ids here were wrong before Phase 13 and would have failed at
# call time: the default was pinned to `claude-sonnet-4-20250514`, and the fast
# model to `claude-haiku-4-5-20250514` — a date-suffixed variant of an id that
# does not take a date suffix, so every fast-path request would have 404'd. The
# current ids are complete as written; do not append dates to them.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4o",
    "gemini": "gemini-1.5-pro",
}

FAST_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-1.5-flash",
}

# Environment variables consulted when no key is passed to the constructor.
_KEY_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

# ── Model registry ───────────────────────────────────────────────────────
#
# `max_output` is each model's REAL ceiling, not a house cap. You asked for max
# tokens uncapped, and this is what "uncapped" actually means per model — there
# is no single number that is simultaneously correct for Opus 5 (128K) and
# Haiku 4.5 (64K), so the ceiling is looked up rather than assumed.
#
# The 128K ceilings are only safe because `_call_anthropic` streams. The SDK
# requires streaming at that size: a non-streaming request with max_tokens set
# this high will hit the HTTP timeout before the model finishes. Do not
# "simplify" that call back to messages.create().
#
# `supports_thinking` gates the adaptive-thinking parameter: Haiku 4.5 predates
# it and returns an error if it is sent. `supports_effort` gates
# output_config.effort for the same reason.
ANTHROPIC_MODELS: dict[str, dict] = {
    "claude-opus-5": {
        "label": "Claude Opus 5",
        "tier": "flagship",
        "context": 1_000_000,
        "max_output": 128_000,
        "input_per_mtok": 5.00,
        "output_per_mtok": 25.00,
        "supports_thinking": True,
        "supports_effort": True,
        "note": "Best general analysis. Thinking on by default.",
    },
    "claude-fable-5": {
        "label": "Claude Fable 5",
        "tier": "frontier",
        "context": 1_000_000,
        "max_output": 128_000,
        "input_per_mtok": 10.00,
        "output_per_mtok": 50.00,
        "supports_thinking": True,
        "supports_effort": True,
        "note": "Most capable. Thinking always on; highest cost.",
    },
    "claude-opus-4-8": {
        "label": "Claude Opus 4.8",
        "tier": "flagship",
        "context": 1_000_000,
        "max_output": 128_000,
        "input_per_mtok": 5.00,
        "output_per_mtok": 25.00,
        "supports_thinking": True,
        "supports_effort": True,
        "note": "Previous Opus generation.",
    },
    "claude-sonnet-5": {
        "label": "Claude Sonnet 5",
        "tier": "balanced",
        "context": 1_000_000,
        "max_output": 128_000,
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "supports_thinking": True,
        "supports_effort": True,
        "note": "Good cost/quality balance for bulk analysis.",
    },
    "claude-sonnet-4-6": {
        "label": "Claude Sonnet 4.6",
        "tier": "balanced",
        "context": 1_000_000,
        "max_output": 128_000,
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "supports_thinking": True,
        "supports_effort": True,
        "note": "Previous Sonnet generation.",
    },
    "claude-haiku-4-5": {
        "label": "Claude Haiku 4.5",
        "tier": "fast",
        "context": 200_000,
        "max_output": 64_000,
        "input_per_mtok": 1.00,
        "output_per_mtok": 5.00,
        # Predates adaptive thinking and output_config.effort — sending either
        # is an API error, not a silently ignored field.
        "supports_thinking": False,
        "supports_effort": False,
        "note": "Cheapest and fastest. Use for high-volume or simple checks.",
    },
}

# Effort levels, in the order the UI should present them. `high` is the API
# default; `max` trades cost for correctness.
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_EFFORT = "high"

# Fallback ceiling for a provider/model with no registry entry. Deliberately
# generous — the point of this change is to stop truncating answers.
FALLBACK_MAX_TOKENS = 32_000


def resolve_max_tokens(provider: str, model: str, requested: int | None = None) -> int:
    """
    The output ceiling for one request.

    `requested` lets a caller ask for LESS (a cheap quick answer) but never
    more than the model can actually produce — an over-large max_tokens is a
    400, not a bigger answer.
    """
    if provider == "anthropic":
        ceiling = ANTHROPIC_MODELS.get(model, {}).get("max_output", FALLBACK_MAX_TOKENS)
    else:
        ceiling = FALLBACK_MAX_TOKENS
    if requested and requested > 0:
        return min(requested, ceiling)
    return ceiling


def model_info(provider: str, model: str) -> dict:
    if provider == "anthropic":
        return ANTHROPIC_MODELS.get(model, {})
    return {}


def _join(system: str | None, prompt: str) -> str:
    """Fold a system prompt into a single-prompt provider's input."""
    return f"{system}\n\n{prompt}" if system else prompt


class LLMService:
    """Multi-provider LLM analysis service."""

    def __init__(self, api_keys: dict[str, str] | None = None):
        self.api_keys = api_keys or {}

    def resolve_key(self, provider: str) -> str:
        """
        Find a provider's key: explicit constructor argument first, then the
        environment.

        The environment fallback matters because every call site constructs
        `LLMService()` with no arguments, so before this the key was always the
        empty string and every request failed authentication.
        """
        explicit = self.api_keys.get(provider)
        if explicit:
            return explicit
        import os
        for name in _KEY_ENV.get(provider, ()):
            val = os.environ.get(name)
            if val:
                return val
        return ""

    def available_providers(self) -> dict[str, dict]:
        """Which providers are installed AND have a usable key."""
        installed = {"anthropic": HAS_ANTHROPIC, "openai": HAS_OPENAI, "gemini": HAS_GEMINI}
        return {
            name: {
                "installed": ok,
                "configured": bool(self.resolve_key(name)),
                "default_model": DEFAULT_MODELS[name],
                "fast_model": FAST_MODELS[name],
                # The full catalogue, so the frontend can offer a real model
                # picker with context/ceiling/price rather than a hardcoded
                # two-entry dropdown.
                "models": self.models_for(name),
            }
            for name, ok in installed.items()
        }

    @staticmethod
    def models_for(provider: str) -> list[dict]:
        """
        Selectable models for a provider, cheapest-capable last.

        Only Anthropic has a curated registry — that is the provider this
        platform actually runs on, and inventing per-model ceilings for the
        others would be guessing. The other two report their default and fast
        ids so the picker still has something valid to show.
        """
        if provider == "anthropic":
            return [
                {"id": mid, **meta} for mid, meta in ANTHROPIC_MODELS.items()
            ]
        seen, out = set(), []
        for mid in (DEFAULT_MODELS.get(provider), FAST_MODELS.get(provider)):
            if mid and mid not in seen:
                seen.add(mid)
                out.append({
                    "id": mid,
                    "label": mid,
                    "tier": "default" if mid == DEFAULT_MODELS.get(provider) else "fast",
                    "max_output": FALLBACK_MAX_TOKENS,
                    "supports_thinking": False,
                    "supports_effort": False,
                })
        return out

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

    async def _call_provider(
        self,
        provider: str,
        model: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
    ) -> str:
        """Route to the correct LLM provider."""
        try:
            if provider == "anthropic" and HAS_ANTHROPIC:
                return await self._call_anthropic(
                    model, prompt, system=system, max_tokens=max_tokens, effort=effort
                )
            # OpenAI/Gemini keep their existing single-prompt shape; `system`
            # is prepended rather than sent as a separate field.
            elif provider == "openai" and HAS_OPENAI:
                return await self._call_openai(model, _join(system, prompt), max_tokens=max_tokens)
            elif provider == "gemini" and HAS_GEMINI:
                return await self._call_gemini(model, _join(system, prompt))
            else:
                return f"Provider '{provider}' not available. Install the SDK."
        except Exception as e:
            logger.error(f"LLM call failed ({provider}): {e}")
            return f"Analysis failed: {e!s}"

    async def _call_anthropic(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
    ) -> str:
        """
        Call the Claude API.

        Streaming rather than a plain create(): analysis prompts carry a whole
        backtest's diagnostics and the answers are long, and a non-streaming
        request at this `max_tokens` risks hitting the SDK's HTTP timeout.
        `get_final_message()` collapses the stream back to one message, so the
        caller sees no difference.

        Adaptive thinking is on because these are genuinely analytical questions
        — "why is this strategy's expectancy negative" is not a lookup.
        """
        key = self.resolve_key("anthropic")
        if not key:
            return (
                "No Anthropic API key configured. Add one in Settings -> AI, or "
                "set ANTHROPIC_API_KEY in the "
                "backend environment — the key stays server-side and is never "
                "sent to the browser."
            )
        client = anthropic.AsyncAnthropic(api_key=key)
        info = model_info("anthropic", model)
        kwargs: dict = {
            "model": model,
            # The model's own ceiling, not a house cap. Safe only because this
            # call streams — see resolve_max_tokens' note.
            "max_tokens": resolve_max_tokens("anthropic", model, max_tokens),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        # Haiku 4.5 predates adaptive thinking and output_config.effort;
        # sending either is an API error, not a silently ignored field.
        if info.get("supports_thinking", not model.startswith("claude-haiku")):
            # display="summarized" because the default on the 5-family is
            # "omitted", which makes a long analysis look like a dead UI.
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if info.get("supports_effort", False):
            kwargs["output_config"] = {"effort": effort or DEFAULT_EFFORT}

        async with client.messages.stream(**kwargs) as stream:
            message = await stream.get_final_message()

        if getattr(message, "stop_reason", None) == "refusal":
            detail = getattr(message, "stop_details", None)
            return f"The model declined this request ({getattr(detail, 'category', 'unspecified')})."

        return "".join(b.text for b in message.content if b.type == "text")

    async def _call_openai(self, model: str, prompt: str, max_tokens: int | None = None) -> str:
        """Call OpenAI API."""
        client = openai.AsyncOpenAI(api_key=self.resolve_key("openai"))
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=resolve_max_tokens("openai", model, max_tokens),
        )
        return response.choices[0].message.content

    async def _call_gemini(self, model: str, prompt: str) -> str:
        """Call Google Gemini API."""
        client = genai.Client(api_key=self.resolve_key("gemini"))
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt
        )
        return response.text
