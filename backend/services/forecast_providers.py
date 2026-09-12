"""
backend/services/forecast_providers.py — [P3.13]

One interface in front of every forecaster.

The post-cutoff harness and, later, the live engine talk to a `ForecastProvider`
and nothing else. What sits behind it — the free momentum baseline, Claude via
the API in blinded or plain rendering, a disk cache in front of either — is a
configuration string, not a code path:

    build_provider("baseline")
    build_provider("anthropic:claude-haiku-4-5")          # blinded (default)
    build_provider("anthropic:claude-haiku-4-5:plain")    # leakage cross-check only
    build_provider("anthropic:claude-opus-5", cache_dir=Path("data/forecast_cache"))

A new backend is one class with the four members of `ForecastProvider`; nothing
in the harness or the engine changes when one is added or swapped.

TWO RULES EVERY PROVIDER INHERITS
---------------------------------
1. **It never sees the future.** It receives the context and the bars up to and
   including the decision bar. The harness slices; the provider cannot ask for
   more.
2. **Plumbing failures are not model behaviour.** A billing error, a missing key
   or a budget refusal comes back as a FLAT forecast with a reason, and
   `is_operational_failure` tells those apart from a model that genuinely
   abstained. Scoring a credit-balance error as "the model was cautious" would
   corrupt exactly the statistic Phase 3 exists to measure — and on 2026-09-11
   that is precisely the failure the agent produced before its error attribution
   was fixed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from backend.analytics.forecast_context import ForecastContext
from backend.strategies.baseline_forecaster import BaselineForecaster, Forecast
from backend.utils.logger import get_logger

logger = get_logger(__name__)

#: Abstention reasons that describe the plumbing rather than the model. Kept in
#: step with the prefixes `ForecastAgent` and `LLMService` actually emit.
#: "The model declined this request" is deliberately ABSENT: a refusal is the
#: model's own output and is scored as an abstention like any other.
OPERATIONAL_FAILURE_PREFIXES: tuple[str, ...] = (
    "LLM call refused",
    "Analysis failed",
    "No Anthropic API key",
    "Provider '",
    "provider call failed",
    "empty response from provider",
    "context render failed",
)

#: Prompt size measured on 2026-09-11 across four live instruments (1,785-char
#: system prompt + ~1,284-char blinded context). Used for cost PLANNING only —
#: actual spend is booked from the API's own usage figures by llm_budget.
MEASURED_INPUT_TOKENS = 869


def is_operational_failure(forecast: Forecast) -> bool:
    """True when a FLAT came from the plumbing, not from the model."""
    reason = forecast.abstain_reason or ""
    return forecast.direction == "FLAT" and reason.startswith(OPERATIONAL_FAILURE_PREFIXES)


@dataclass(frozen=True)
class Bars:
    """Raw OHLC up to AND INCLUDING the decision bar — never beyond it.

    `decision_time` is metadata for cache keys and journals. No provider renders
    it: the prompt is dateless by design, and the baseline has no use for it.
    """

    open: Sequence[float]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    decision_time: int | None = None


@runtime_checkable
class ForecastProvider(Protocol):
    """Everything the harness and the engine are allowed to know about a forecaster."""

    @property
    def name(self) -> str: ...

    @property
    def costs_money(self) -> bool: ...

    def estimate_call_cost_usd(self) -> float: ...

    async def forecast(self, instrument: str, ctx: ForecastContext, bars: Bars) -> Forecast: ...


# ── the free baseline ─────────────────────────────────────────────────────────

@dataclass
class BaselineProvider:
    """Vol-filtered time-series momentum. Costs nothing; validates the pipeline."""

    forecaster: BaselineForecaster = field(default_factory=BaselineForecaster)

    @property
    def name(self) -> str:
        return "baseline"

    @property
    def costs_money(self) -> bool:
        return False

    def estimate_call_cost_usd(self) -> float:
        return 0.0

    async def forecast(self, instrument: str, ctx: ForecastContext, bars: Bars) -> Forecast:
        return self.forecaster.forecast(instrument, bars.high, bars.low, bars.close)


# ── Claude via the API ────────────────────────────────────────────────────────

@dataclass
class AnthropicProvider:
    """Claude as a forecasting component, through `ForecastAgent`.

    The default model is Claude Opus 5. A cheaper model is a deliberate choice
    made in the provider spec (`anthropic:claude-haiku-4-5`), never a silent
    downgrade — for a feasibility question it is a reasonable one, and the CLI's
    dry run prices every option so the choice is made with the numbers in view.
    """

    model: str = "claude-opus-5"
    rendering: str = "blinded"
    max_tokens: int | None = None
    est_input_tokens: int = MEASURED_INPUT_TOKENS
    est_output_tokens: int | None = None

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}" + (":plain" if self.rendering == "plain" else "")

    @property
    def costs_money(self) -> bool:
        return True

    def _supports_thinking(self) -> bool:
        try:
            from backend.services.llm_service import model_info
            info = model_info("anthropic", self.model) or {}
        except Exception:
            info = {}
        return bool(info.get("supports_thinking", not self.model.startswith("claude-haiku")))

    def resolved_max_tokens(self) -> int:
        """Adaptive thinking bills its reasoning as output and counts it against
        `max_tokens`. A 2,000-token ceiling on a thinking model can end the turn
        mid-reasoning with no JSON at all — which the validator would then score
        as a malformed answer. Thinking models get headroom; the estimate below
        is what is PAID, this is only the ceiling."""
        if self.max_tokens is not None:
            return int(self.max_tokens)
        return 8000 if self._supports_thinking() else 1500

    def estimated_output_tokens(self) -> int:
        if self.est_output_tokens is not None:
            return int(self.est_output_tokens)
        return 1500 if self._supports_thinking() else 400

    def estimate_call_cost_usd(self) -> float:
        from backend.services.llm_budget import estimate_cost
        return estimate_cost("anthropic", self.model,
                             self.est_input_tokens, self.estimated_output_tokens())

    async def forecast(self, instrument: str, ctx: ForecastContext, bars: Bars) -> Forecast:
        from backend.services.forecast_agent import ForecastAgent
        agent = ForecastAgent(model=self.model, max_tokens=self.resolved_max_tokens(),
                              rendering=self.rendering)
        # The real name is only rendered in `plain` mode; the blinded prompt ignores it.
        return await agent.forecast(instrument, ctx, instrument_name_for_plain=instrument)


# ── a cache in front of a paid provider ──────────────────────────────────────

def _forecast_from_dict(d: dict[str, Any]) -> Forecast:
    names = {f.name for f in fields(Forecast)}
    kw = {k: v for k, v in d.items() if k in names}
    if kw.get("entry_zone") is not None:
        kw["entry_zone"] = tuple(kw["entry_zone"])
    return Forecast(**kw)


@dataclass
class CachedProvider:
    """Never pay for the same forecast twice.

    A long paid run that dies at forecast 180 of 200 — a network drop, a closed
    laptop — should cost 20 calls to finish, not 200. Entries are keyed by the
    provider, instrument, decision time, context hash AND the price anchor, so a
    surrogate series at the same timestamp can never collide with the real one.

    Operational failures are never written: caching a credit-balance error would
    make it permanent, and every later run would silently replay the outage as
    if it were the model's answer.
    """

    inner: Any
    path: Path
    hits: int = field(default=0, init=False)
    misses: int = field(default=0, init=False)
    _cache: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def costs_money(self) -> bool:
        return self.inner.costs_money

    def estimate_call_cost_usd(self) -> float:
        return self.inner.estimate_call_cost_usd()

    @staticmethod
    def key(provider_name: str, instrument: str, ctx: ForecastContext, bars: Bars) -> str:
        anchor = (round(float(ctx._private.get("last_close", 0.0)), 8),
                  round(float(ctx._private.get("atr", 0.0)), 8))
        raw = f"{provider_name}|{instrument}|{bars.decision_time}|{ctx.version}|{ctx.context_hash}|{anchor}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    self._cache[rec["key"]] = rec["forecast"]
                except Exception:
                    continue  # a torn final line from an interrupted write

    async def forecast(self, instrument: str, ctx: ForecastContext, bars: Bars) -> Forecast:
        self._load()
        k = self.key(self.name, instrument, ctx, bars)
        if k in self._cache:
            self.hits += 1
            return _forecast_from_dict(self._cache[k])
        f = await self.inner.forecast(instrument, ctx, bars)
        self.misses += 1
        if not is_operational_failure(f):
            d = f.to_dict()
            self._cache[k] = d
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": k, "forecast": d}, default=str) + "\n")
        return f


# ── construction ─────────────────────────────────────────────────────────────

def build_provider(spec: str, *, cache_dir: Path | None = None) -> Any:
    """`baseline` | `anthropic:<model>` | `anthropic:<model>:plain`.

    A cache is added only in front of a provider that costs money — the baseline
    is faster to recompute than to look up.
    """
    s = (spec or "").strip()
    if s == "baseline":
        return BaselineProvider()
    if s.startswith("anthropic:"):
        parts = s.split(":")
        model = parts[1] if len(parts) > 1 and parts[1] else "claude-opus-5"
        rendering = "blinded"
        if len(parts) > 2:
            if parts[2] not in ("plain", "blinded"):
                raise ValueError(f"unknown rendering {parts[2]!r} — use 'blinded' or 'plain'")
            rendering = parts[2]
        provider: Any = AnthropicProvider(model=model, rendering=rendering)
        if cache_dir is not None:
            safe = provider.name.replace(":", "_")
            provider = CachedProvider(inner=provider, path=Path(cache_dir) / f"{safe}.cache.jsonl")
        return provider
    raise ValueError(f"unknown provider spec {spec!r} — use 'baseline' or "
                     f"'anthropic:<model>[:plain]'")
