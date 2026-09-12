"""
backend/services/llm_budget.py — [P3.12]

A hard ceiling on what the forecasting agent can spend.

WHY THIS SHIPS BEFORE THE AGENT
-------------------------------
Phase 3's design point is 8 instruments on a 15-minute trigger. Unguarded that
is 8 x 96 = 768 calls a day, and at Opus 5 prices ($5/Mtok in, $25/Mtok out) a
context-heavy prompt makes that a four-figure monthly bill arrived at by
accident. Worse, the failure modes are silent: a retry loop, a scheduler that
fires twice, or a context builder that stops truncating all spend money at a
rate nobody is watching.

So the budget is enforced at the point of the call, not in a code review:

  - a per-call token ceiling,
  - a rolling daily cost cap per provider,
  - a circuit breaker that latches OPEN for the rest of the day once tripped,
  - spend recorded per model and per caller, so "which instrument is expensive"
    is answerable.

It refuses rather than truncating. A forecast made on a deliberately shortened
context is a different forecast, and silently swapping one for the other would
corrupt the very comparison Phase 3 exists to make.

STATE
-----
Persisted to a JSON file so a process restart cannot reset the day's spend —
a crash loop would otherwise be unlimited. Keyed by UTC date.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_STATE_PATH = Path("data") / "llm_budget.json"


class BudgetExceeded(RuntimeError):
    """Raised instead of making a call that would breach the cap.

    Deliberately an exception rather than a truncated prompt or a None: a caller
    that silently degrades is how an agent ends up forecasting from half a
    context and reporting the result as comparable.
    """


@dataclass
class _DayState:
    date: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    tripped: bool = False
    trip_reason: str = ""
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    by_caller: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class LLMBudget:
    """Daily spend ceiling with a latching circuit breaker."""

    daily_cost_cap_usd: float = 10.0
    max_tokens_per_call: int = 32_000
    max_calls_per_day: int = 500
    state_path: Path = DEFAULT_STATE_PATH
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _state: _DayState | None = field(default=None, repr=False)

    # ── state ────────────────────────────────────────────────────────────────
    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> _DayState:
        today = self._today()
        if self._state is not None and self._state.date == today:
            return self._state
        data: dict[str, Any] = {}
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[LLM_BUDGET] could not read state, starting fresh: {e}")
            data = {}
        if data.get("date") == today:
            self._state = _DayState(
                date=today,
                input_tokens=int(data.get("input_tokens", 0)),
                output_tokens=int(data.get("output_tokens", 0)),
                cost_usd=float(data.get("cost_usd", 0.0)),
                calls=int(data.get("calls", 0)),
                tripped=bool(data.get("tripped", False)),
                trip_reason=str(data.get("trip_reason", "")),
                by_model=dict(data.get("by_model", {})),
                by_caller=dict(data.get("by_caller", {})),
            )
        else:
            self._state = _DayState(date=today)
        return self._state

    def _save(self) -> None:
        s = self._state
        if s is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(s.__dict__, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[LLM_BUDGET] could not persist state: {e}")

    # ── the gate ─────────────────────────────────────────────────────────────
    def check(self, provider: str, model: str, max_tokens: int | None = None) -> None:
        """Raise BudgetExceeded if this call must not be made. Call BEFORE spending."""
        with self._lock:
            s = self._load()
            if s.tripped:
                raise BudgetExceeded(
                    f"LLM budget circuit breaker is OPEN for {s.date}: {s.trip_reason}. "
                    f"Spend so far ${s.cost_usd:.2f} over {s.calls} calls."
                )
            if max_tokens is not None and max_tokens > self.max_tokens_per_call:
                raise BudgetExceeded(
                    f"requested max_tokens={max_tokens:,} exceeds the per-call ceiling "
                    f"of {self.max_tokens_per_call:,}"
                )
            if s.calls >= self.max_calls_per_day:
                self._trip(f"daily call cap reached ({s.calls}/{self.max_calls_per_day})")
                raise BudgetExceeded(s.trip_reason)
            if s.cost_usd >= self.daily_cost_cap_usd:
                self._trip(f"daily cost cap reached (${s.cost_usd:.2f} / "
                           f"${self.daily_cost_cap_usd:.2f})")
                raise BudgetExceeded(s.trip_reason)

    def record(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        caller: str = "unknown",
    ) -> dict[str, Any]:
        """Book the spend of a completed call, and trip the breaker if it took us
        over. Returns the running totals."""
        cost = estimate_cost(provider, model, input_tokens, output_tokens)
        with self._lock:
            s = self._load()
            s.input_tokens += int(input_tokens)
            s.output_tokens += int(output_tokens)
            s.cost_usd += cost
            s.calls += 1
            for bucket, key in ((s.by_model, model), (s.by_caller, caller)):
                e = bucket.setdefault(key, {"calls": 0, "cost_usd": 0.0,
                                            "input_tokens": 0, "output_tokens": 0})
                e["calls"] += 1
                e["cost_usd"] = round(e["cost_usd"] + cost, 6)
                e["input_tokens"] += int(input_tokens)
                e["output_tokens"] += int(output_tokens)
            if s.cost_usd >= self.daily_cost_cap_usd:
                self._trip(f"daily cost cap reached (${s.cost_usd:.2f} / "
                           f"${self.daily_cost_cap_usd:.2f})")
            self._save()
            return self.status_unlocked()

    def _trip(self, reason: str) -> None:
        s = self._load()
        if not s.tripped:
            s.tripped = True
            s.trip_reason = reason
            logger.error(f"[LLM_BUDGET] circuit breaker OPEN — {reason}")
            self._save()

    # ── introspection ────────────────────────────────────────────────────────
    def status_unlocked(self) -> dict[str, Any]:
        s = self._load()
        return {
            "date": s.date,
            "calls": s.calls,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "cost_usd": round(s.cost_usd, 4),
            "daily_cost_cap_usd": self.daily_cost_cap_usd,
            "remaining_usd": round(max(0.0, self.daily_cost_cap_usd - s.cost_usd), 4),
            "tripped": s.tripped,
            "trip_reason": s.trip_reason,
            "by_model": s.by_model,
            "by_caller": s.by_caller,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self.status_unlocked()

    def reset_today(self) -> None:
        """Clear the day's spend and close the breaker. Operator action only —
        never call this from the agent, which is the whole point of the cap."""
        with self._lock:
            self._state = _DayState(date=self._today())
            self._save()
            logger.warning("[LLM_BUDGET] daily spend manually reset")


# ── pricing ──────────────────────────────────────────────────────────────────

def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """USD for a call, from the model registry's own published rates.

    Unknown models are priced at the most expensive entry in the registry rather
    than zero: a pricing table that silently under-counts a model it does not
    recognise is a budget that does not bind.
    """
    try:
        from backend.services.llm_service import ANTHROPIC_MODELS, model_info
    except Exception:
        return 0.0
    info = model_info(provider, model) or {}
    inp = info.get("input_per_mtok")
    out = info.get("output_per_mtok")
    if inp is None or out is None:
        known = [m for m in (ANTHROPIC_MODELS or {}).values()
                 if isinstance(m, dict) and m.get("input_per_mtok") is not None]
        if known:
            inp = max(m["input_per_mtok"] for m in known)
            out = max(m["output_per_mtok"] for m in known)
            logger.warning(f"[LLM_BUDGET] unknown model {model!r} — priced at the "
                           f"registry maximum (${inp}/${out} per Mtok)")
        else:
            inp = out = 0.0
    return (input_tokens / 1e6) * float(inp) + (output_tokens / 1e6) * float(out)


#: Process-wide budget. Constructed with conservative defaults; the agent is
#: expected to pass its own instance in tests.
llm_budget = LLMBudget()
