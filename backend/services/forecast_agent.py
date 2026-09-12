"""
backend/services/forecast_agent.py — [P3.5 / P3.6]

The LLM forecasting agent, and the validator that stands between it and the
risk engine.

THE DIVISION OF AUTHORITY
-------------------------
The agent forecasts. It never sizes, never places, never manages. It returns a
structured proposal in RELATIVE units, the validator converts that to real price
levels and rejects anything incoherent, and `RiskEngine` remains the sole
authority on how much money is at stake. That boundary is the whole design:
a model that is wrong about direction costs one R, a model that is wrong about
size costs the account.

WHY THE OUTPUT IS RELATIVE
--------------------------
The context is blinded (see analytics/forecast_context.py) — no instrument name,
no dates, no absolute prices — so the model cannot answer in price terms even if
it wanted to. It answers in ATR multiples, and the validator maps those back.
That is not a workaround; it is what makes the blinded and unblinded runs
directly comparable, and the gap between them is the leakage estimate.

ABSTENTION IS THE DEFAULT
-------------------------
Every failure path — a refusal, malformed JSON, a nonsensical level, a
budget refusal — resolves to FLAT with a reason. An agent that emits a position
when it is confused is worse than one that emits nothing, and the scoring
harness treats a wrong FLAT as free and a wrong LONG as expensive.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from backend.analytics.forecast_context import ForecastContext
from backend.strategies.baseline_forecaster import Forecast
from backend.utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a forecasting component in a systematic trading system.

You do not size positions, place orders, or manage risk. A separate risk engine
does that. Your only job is to read the market state you are given and return a
structured forecast.

The data is deliberately anonymised: you are not told which instrument this is or
what date it is, and all levels are relative. Do not guess the instrument. Do not
reason from anything you think you remember about a particular market on a
particular date — you have not been told either, and inventing one would make the
forecast worse, not better.

Rules:
- Answer ONLY with a single JSON object. No prose, no markdown fence.
- All price levels are in ATR multiples relative to the latest close:
  +1.5 means one and a half ATR ABOVE the close, -2.0 means two ATR below.
- FLAT is a real and frequently correct answer. Most windows contain no
  actionable setup. Abstain unless the evidence is specific.
- `conviction` is a probability-like number in [0,1]. It will be calibrated
  against outcomes, so inflating it is detected and penalised.
- `contradicting_evidence` must be populated whenever you take a direction. If
  you cannot name what would make you wrong, you do not have a thesis.

Schema:
{
  "direction": "LONG" | "SHORT" | "FLAT",
  "conviction": 0.0,
  "horizon_bars": 24,
  "invalidation_atr": -2.0,
  "target_atr": 4.0,
  "expected_move_atr": 1.5,
  "regime": "TRENDING" | "RANGING" | "EXPANDING" | "COMPRESSING",
  "primary_evidence": ["..."],
  "contradicting_evidence": ["..."],
  "abstain_reason": null
}

For LONG: invalidation_atr must be negative and target_atr positive.
For SHORT: invalidation_atr must be positive and target_atr negative.
For FLAT: set conviction 0, both levels null, and give abstain_reason."""

_VALID_DIRECTIONS = {"LONG", "SHORT", "FLAT"}
_VALID_REGIMES = {"TRENDING", "RANGING", "EXPANDING", "COMPRESSING", "UNKNOWN"}


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the JSON object out of a model response.

    Tolerant of a markdown fence or a stray sentence, because those are ordinary
    model behaviour rather than errors — but never tolerant enough to guess at
    missing fields. A response we cannot parse becomes an abstention.
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


@dataclass
class ProposalValidator:
    """Turns a raw model proposal into a Forecast, or into an abstention.

    Every rejection is a named reason rather than a silent FLAT, so a run that
    abstains 90% of the time can be diagnosed as "the model is cautious" or
    "the model is emitting garbage" rather than guessed at.
    """

    max_conviction: float = 1.0
    max_target_atr: float = 20.0
    min_stop_atr: float = 0.25
    max_stop_atr: float = 10.0

    def validate(
        self,
        raw: dict[str, Any] | None,
        ctx: ForecastContext,
        instrument: str,
    ) -> Forecast:
        if not isinstance(raw, dict):
            return self._abstain(instrument, "model returned no parseable JSON")

        direction = str(raw.get("direction", "FLAT")).upper().strip()
        if direction not in _VALID_DIRECTIONS:
            return self._abstain(instrument, f"unrecognised direction {direction!r}")

        regime = str(raw.get("regime", "UNKNOWN")).upper().strip()
        if regime not in _VALID_REGIMES:
            regime = "UNKNOWN"

        if direction == "FLAT":
            return Forecast(
                instrument=instrument, direction="FLAT", conviction=0.0,
                regime=regime, source="llm",
                primary_evidence=_strlist(raw.get("primary_evidence")),
                contradicting_evidence=_strlist(raw.get("contradicting_evidence")),
                abstain_reason=str(raw.get("abstain_reason") or "model abstained"),
            )

        try:
            conviction = float(raw.get("conviction", 0.0))
            inval_atr = float(raw.get("invalidation_atr"))
            target_atr = float(raw.get("target_atr"))
        except (TypeError, ValueError):
            return self._abstain(instrument, "conviction/levels were not numeric")

        if not (0.0 < conviction <= self.max_conviction):
            return self._abstain(instrument, f"conviction {conviction} outside (0, "
                                             f"{self.max_conviction}]")

        # Geometry. A LONG whose stop is above the entry is not a typo to be
        # helpfully corrected — it means the model did not understand the task,
        # and silently flipping it would hide that.
        if direction == "LONG" and not (inval_atr < 0 < target_atr):
            return self._abstain(instrument, f"LONG with invalidation {inval_atr:+.2f} / "
                                             f"target {target_atr:+.2f} — wrong sides")
        if direction == "SHORT" and not (target_atr < 0 < inval_atr):
            return self._abstain(instrument, f"SHORT with invalidation {inval_atr:+.2f} / "
                                             f"target {target_atr:+.2f} — wrong sides")

        stop_dist = abs(inval_atr)
        if not (self.min_stop_atr <= stop_dist <= self.max_stop_atr):
            return self._abstain(instrument, f"stop {stop_dist:.2f} ATR outside "
                                             f"[{self.min_stop_atr}, {self.max_stop_atr}]")
        if abs(target_atr) > self.max_target_atr:
            return self._abstain(instrument, f"target {abs(target_atr):.2f} ATR beyond "
                                             f"the {self.max_target_atr} ceiling")

        rr = abs(target_atr) / stop_dist
        if rr < 0.5:
            return self._abstain(instrument, f"reward:risk {rr:.2f} — not worth the spread")

        # Relative -> absolute, using values the model never saw.
        last = float(ctx._private.get("last_close", 0.0))
        atr = float(ctx._private.get("atr", 0.0))
        if last <= 0 or atr <= 0:
            return self._abstain(instrument, "context carries no usable price anchor")

        return Forecast(
            instrument=instrument,
            direction="LONG" if direction == "LONG" else "SHORT",
            conviction=round(min(conviction, self.max_conviction), 4),
            horizon_bars=int(raw.get("horizon_bars") or 24),
            entry_zone=(last, last),
            invalidation=last + inval_atr * atr,
            targets=[{"level": last + target_atr * atr,
                      "probability": round(1.0 / (1.0 + rr), 4)}],
            expected_move_atr=abs(float(raw.get("expected_move_atr") or target_atr)),
            regime=regime,
            primary_evidence=_strlist(raw.get("primary_evidence")),
            contradicting_evidence=_strlist(raw.get("contradicting_evidence")),
            source="llm",
        )

    @staticmethod
    def _abstain(instrument: str, reason: str) -> Forecast:
        logger.info(f"[FORECAST] {instrument}: abstained — {reason}")
        return Forecast(instrument=instrument, direction="FLAT",
                        abstain_reason=reason, source="llm")


def _strlist(v: Any, limit: int = 6) -> list[str]:
    if isinstance(v, str):
        return [v][:limit]
    if isinstance(v, list):
        return [str(x) for x in v][:limit]
    return []


@dataclass
class ForecastAgent:
    """Claude as a forecasting component. Nothing else."""

    model: str = "claude-opus-5"
    provider: str = "anthropic"
    max_tokens: int = 2000
    rendering: str = "blinded"
    validator: ProposalValidator = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.validator is None:
            self.validator = ProposalValidator()

    async def forecast(
        self,
        instrument: str,
        ctx: ForecastContext,
        instrument_name_for_plain: str | None = None,
    ) -> Forecast:
        """One forecast. Never raises — every failure becomes an abstention."""
        from backend.services.llm_service import LLMService

        try:
            body = ctx.render(self.rendering, instrument_name_for_plain)
        except Exception as e:
            return self.validator._abstain(instrument, f"context render failed: {e}")

        svc = LLMService(caller=f"forecast:{instrument}")
        try:
            text = await svc._call_provider(
                provider=self.provider, model=self.model,
                prompt=f"Market state:\n\n{body}\nReturn the JSON object now.",
                system=SYSTEM_PROMPT, max_tokens=self.max_tokens,
            )
        except Exception as e:
            return self.validator._abstain(instrument, f"provider call failed: {e}")

        # `_call_provider` reports failures as a STRING rather than raising, so
        # an API error (no credit, bad key, rate limit) arrives looking like a
        # model response. Without this check the validator blames "no parseable
        # JSON" and the real cause — which is operational, not statistical —
        # never surfaces. That distinction matters: an abstention rate driven by
        # billing is not evidence about the model.
        if not text:
            return self.validator._abstain(instrument, "empty response from provider")
        for prefix in ("LLM call refused", "Analysis failed", "No Anthropic API key",
                       "Provider '", "The model declined"):
            if text.startswith(prefix):
                return self.validator._abstain(instrument, text.strip()[:300])

        return self.validator.validate(extract_json(text), ctx, instrument)
