"""
backend/rl/confluence.py

Mode B on SEVERAL strategies at once: what the agent sees about them.

WHY (2026-10-18)
----------------
A mode-B agent on one strategy can only learn which of THAT strategy's setups
to take. The user's ask was one agent over every strategy on a pair, picking
the combinations that pay. So the agent gets one input per base strategy - the
direction of that strategy's most recent setup, fading over SETUP_DECAY_BARS -
plus their sum. A setup from ORB that arrives three bars after a Trend Breakout
setup in the same direction then LOOKS different from a lone ORB setup, and the
policy can learn whether that agreement is worth taking.

WHY HERE, AND WHY TWO FUNCTIONS
-------------------------------
Training computes these for every bar at once (`setup_flags`); live computes
them for the bar in front of it (`flags_now`). Both are written from one
definition below so they cannot drift: `tests/test_rl_multi_strategy.py`
checks that the live row equals the training row for the same history.

CAUSALITY
---------
A setup is keyed by the agent's DECISION row - the last bar that had closed
when the strategy decided (see `base_signals.generate`). A row's flag only
looks at setups at that row or earlier, so nothing here reads the future.
"""

from __future__ import annotations

import numpy as np

# How long a setup keeps influencing the inputs, in agent bars. 24 M5 bars is
# two hours: long enough for one strategy's setup to meet another's, short
# enough that yesterday's breakout is not read as agreement today.
SETUP_DECAY_BARS = 24


def column_names(strategies) -> list[str]:
    """One column per strategy, then their sum."""
    return [f"setup:{s}" for s in strategies] + ["setup_agreement"]


def _value(direction: str, age: int) -> float:
    if age < 0 or age >= SETUP_DECAY_BARS:
        return 0.0
    sign = 1.0 if str(direction).upper() == "BUY" else -1.0
    return sign * (1.0 - age / SETUP_DECAY_BARS)


def setup_flags(by_strategy: dict[str, dict[int, dict]], strategies, n_rows: int) -> np.ndarray:
    """`(n_rows, len(strategies) + 1)`: each strategy's latest setup, fading, and the sum."""
    k = len(strategies)
    out = np.zeros((n_rows, k + 1), dtype=float)
    for c, sid in enumerate(strategies):
        setups = sorted((by_strategy.get(sid) or {}).items())
        p = 0
        last_row, last_dir = None, None
        for r in range(n_rows):
            while p < len(setups) and setups[p][0] <= r:
                last_row, last_dir = setups[p][0], setups[p][1].get("direction", "BUY")
                p += 1
            if last_row is not None:
                out[r, c] = _value(last_dir, r - last_row)
    out[:, k] = out[:, :k].sum(axis=1)
    return out


def flags_now(latest: dict[str, tuple[int, str]], strategies, row: int) -> np.ndarray:
    """The same row, live: `latest[sid] = (row_it_was_found_on, direction)`."""
    k = len(strategies)
    out = np.zeros(k + 1, dtype=float)
    for c, sid in enumerate(strategies):
        hit = latest.get(sid)
        if hit is not None:
            out[c] = _value(hit[1], row - int(hit[0]))
    out[k] = out[:k].sum()
    return out


def merge(by_strategy: dict[str, dict[int, dict]]) -> dict[int, list[dict]]:
    """Every strategy's setups by decision row - several strategies may fire on
    the same bar, and the agent is offered each of them."""
    out: dict[int, list[dict]] = {}
    for sid, setups in by_strategy.items():
        for row, sig in (setups or {}).items():
            out.setdefault(int(row), []).append(dict(sig, strategy_name=sid))
    return out
