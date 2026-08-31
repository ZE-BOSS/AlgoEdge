"""
backend/strategies/gate_recorder.py

Per-confluence telemetry for strategy entry logic.

WHY THIS EXISTS
---------------
Every strategy engine filters candidates through a chain of silent
`return None` / `continue` statements — roughly 101 of them across the seven
engines. None of them recorded anything. The backtest engine reads
`sig.get("metadata", {}).get("passed_gates", True)` and no strategy has ever
set that key, so `rejection_funnel["strategy_rejections"]` was structurally
incapable of being non-empty and every saved trade carried the single
confluence tag "base_structure".

The consequence: there was no way to answer "which confluences actually earn
their place?" — the question the whole optimization effort turns on.

WHAT IT PROVIDES
----------------
1. `rec.gate(name, passed, detail)` — a one-line wrapper around an existing
   condition. Returns `passed`, so the call site changes from

       if not self._is_within_session(t):
           return None
   to
       if not rec.gate("session_filter", self._is_within_session(t)):
           return None

   and nothing else about the strategy's control flow moves.

2. An ordered record of every gate evaluated for a candidate, so we know which
   gate BLOCKED it (the first failure) and which gates it had already cleared.

3. `disabled_gates` — force named gates to report True. This is what makes a
   true ablation re-run possible: disable `session_filter`, re-run, and compare
   expectancy. Overrides are recorded so an ablation run is never mistaken for
   a baseline.

4. Near-zero cost when `enabled` is False, which is the live-trading default —
   `gate()` becomes a return of its own argument.

DESIGN NOTE — why not evaluate every gate unconditionally?
----------------------------------------------------------
Strategies short-circuit: once a gate fails, later gates are never evaluated,
so a single pass cannot say what gate #7 would have decided about a candidate
that gate #3 rejected. Making every gate independently evaluable would mean
rewriting all seven engines into pure predicate functions — a far larger and
riskier change than this.

So the split is:
  * Frequency, block-rate, and forward-excursion stats for candidates that
    REACHED each gate  -> available from one instrumented pass (cheap).
  * True marginal contribution of removing a gate -> needs a re-run with that
    gate disabled (expensive, but only worth doing for gates that actually
    block often; a gate that never blocks cannot change the result).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GateEvent:
    """One gate evaluation for one candidate."""
    name: str
    passed: bool
    detail: str | None = None
    overridden: bool = False  # forced True by disabled_gates


@dataclass
class CandidateRecord:
    """The full gate chain for a single candidate setup."""
    symbol: str
    timeframe: str
    bar_index: int
    bar_time: Any
    events: list[GateEvent] = field(default_factory=list)
    emitted: bool = False          # did this candidate become a signal?
    direction: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None

    @property
    def blocking_gate(self) -> str | None:
        """The first gate that failed, i.e. the one that killed this candidate."""
        for e in self.events:
            if not e.passed:
                return e.name
        return None

    @property
    def passed_gates(self) -> bool:
        return self.blocking_gate is None

    def vector(self) -> dict[str, bool]:
        """Flat {gate_name: passed} map. Later evaluations win on duplicates."""
        return {e.name: e.passed for e in self.events}

    def cleared(self) -> list[str]:
        """Gates this candidate passed before being blocked (if it was)."""
        out = []
        for e in self.events:
            if not e.passed:
                break
            out.append(e.name)
        return out


class GateRecorder:
    """
    Records gate outcomes for one strategy instance.

    A "candidate" is one pass through the entry logic for one bar. Strategies
    call `begin(...)` at the top of on_bar and `gate(...)` at each decision.
    `begin()` implicitly closes any previous candidate, so a strategy that
    returns early never has to remember to finalise anything.
    """

    __slots__ = (
        "enabled", "disabled_gates", "records", "_current",
        "_counts", "_blocks", "max_records",
    )

    def __init__(
        self,
        enabled: bool = False,
        disabled_gates: set[str] | None = None,
        max_records: int = 200_000,
    ):
        self.enabled = enabled
        self.disabled_gates = disabled_gates or set()
        self.records: list[CandidateRecord] = []
        self._current: CandidateRecord | None = None
        # Running tallies so a summary is available without walking `records`.
        self._counts: dict[str, list[int]] = {}   # name -> [evaluated, passed]
        self._blocks: dict[str, int] = {}         # name -> times it was THE blocker
        self.max_records = max_records

    # ── lifecycle ────────────────────────────────────────────────────────
    def begin(self, symbol: str, timeframe: str, bar_index: int = -1, bar_time: Any = None) -> None:
        """Open a new candidate record. Closes the previous one."""
        if not self.enabled:
            return
        self._flush()
        self._current = CandidateRecord(
            symbol=symbol, timeframe=timeframe, bar_index=bar_index, bar_time=bar_time
        )

    def emitted(self, signal: Any = None) -> None:
        """Mark the open candidate as having produced a signal."""
        if not self.enabled or self._current is None:
            return
        self._current.emitted = True
        if signal is not None:
            self._current.direction = getattr(signal, "direction", None)
            self._current.entry_price = getattr(signal, "entry_price", None)
            self._current.stop_loss = getattr(signal, "stop_loss", None)

    def _flush(self) -> None:
        cur = self._current
        self._current = None
        if cur is None or not cur.events:
            return
        blocker = cur.blocking_gate
        if blocker is not None:
            self._blocks[blocker] = self._blocks.get(blocker, 0) + 1
        # Cap retained detail; tallies keep accumulating regardless.
        if len(self.records) < self.max_records:
            self.records.append(cur)

    def finish(self) -> None:
        """Close the final candidate. Call once at end of run."""
        if self.enabled:
            self._flush()

    # ── the hot path ─────────────────────────────────────────────────────
    def gate(self, name: str, passed: Any, detail: str | None = None) -> bool:
        """
        Record a gate outcome and return it.

        Returns True for gates named in `disabled_gates`, which is what lets an
        ablation run push candidates past a gate that would normally stop them.
        """
        if not self.enabled:
            # Live path: no allocation, no bookkeeping.
            return bool(passed)

        result = bool(passed)
        overridden = False
        if name in self.disabled_gates and not result:
            result = True
            overridden = True

        c = self._counts.get(name)
        if c is None:
            c = self._counts[name] = [0, 0]
        c[0] += 1
        if result:
            c[1] += 1

        if self._current is not None:
            self._current.events.append(
                GateEvent(name=name, passed=result, detail=detail, overridden=overridden)
            )
        return result

    # ── reporting ────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        """Aggregate view suitable for rejection_funnel / a report panel."""
        gates = {}
        for name, (evaluated, passed) in sorted(self._counts.items()):
            gates[name] = {
                "evaluated": evaluated,
                "passed": passed,
                "failed": evaluated - passed,
                "pass_rate": (passed / evaluated) if evaluated else 0.0,
                "blocked_candidates": self._blocks.get(name, 0),
            }
        total = len(self.records)
        emitted = sum(1 for r in self.records if r.emitted)
        return {
            "gates": gates,
            "candidates_recorded": total,
            "candidates_emitted": emitted,
            "candidates_blocked": total - emitted,
            "blocking_gate_counts": dict(
                sorted(self._blocks.items(), key=lambda kv: -kv[1])
            ),
            "disabled_gates": sorted(self.disabled_gates),
        }

    def strategy_rejections(self) -> dict[str, int]:
        """
        {gate_name: times it was the blocking gate}.

        This is what backend/backtester/engine.py wants for
        `rejection_funnel["strategy_rejections"]`, which has been permanently
        empty because no strategy ever reported a rejection.
        """
        return dict(sorted(self._blocks.items(), key=lambda kv: -kv[1]))

    def metadata_for_signal(self) -> dict[str, Any]:
        """
        The keys the backtest engine and reports.py read off a signal.

        `gate_vector` is the per-candidate confluence map the ablation study
        groups on; `confluence_tags` replaces the single hardcoded
        "base_structure" bucket that every trade used to carry.
        """
        cur = self._current
        if not self.enabled or cur is None:
            return {}
        vec = cur.vector()
        return {
            "passed_gates": cur.passed_gates,
            "rejection_reasons": (
                [] if cur.passed_gates
                else [f"{cur.blocking_gate}: blocked"]
            ),
            "gate_vector": vec,
            "confluence_tags": [k for k, v in vec.items() if v],
            "gates_cleared": cur.cleared(),
        }

    def reset(self) -> None:
        self.records.clear()
        self._counts.clear()
        self._blocks.clear()
        self._current = None
