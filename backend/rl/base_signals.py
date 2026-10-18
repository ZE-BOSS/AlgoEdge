"""
backend/rl/base_signals.py

Mode B: the agent learns an EXISTING strategy rather than inventing one.

WHAT MODE B IS
--------------
`VWAP_v1` finds its setups. The agent does not second-guess that — it decides,
per setup, whether to **take** it, **where to put the stop**, and **what to aim
at**, and then manages the exit. That is "optimise the strategy" stated
precisely: same setups, better selection and better trade shape.

WHY THE SIGNALS ARE GENERATED ONCE, HERE
----------------------------------------
Running `VWAP_v1` over 80,000 bars costs real time, and a population optimiser
evaluates hundreds of policies against the SAME setups. Generating them per
rollout would make the base strategy's cost dominate the whole search for no
benefit: the setups do not depend on the policy. So they are produced once,
attached to the `EpisodeData`, and sliced with it.

This also gives mode B the one baseline that makes it measurable: the same
signal list, run through the engine untouched, is the strategy as it trades
today. The agent has to beat that, not a benchmark of its own choosing.

THE BAR STAMP IS THE SAME SCAR AS EVERYWHERE ELSE
-------------------------------------------------
A signal found on bar `i` is stamped with bar `i-1`'s time, so the engine fills
it at bar `i`'s open — which is what live does. Stamping bar `i` instead made
every entry fill one bar late and cost 10R over 152 GBPJPY trades the last time
it happened in this codebase.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# The same guard the backtest routes use. A strategy that re-detects the same
# setup every bar can emit tens of thousands of signals, and the cost is not
# just a bad result - it is unbounded memory.
MAX_BASE_SIGNALS = 5000


def build_strategy(strategy_id: str, config: Any):
    """Construct a registered strategy for signal generation.

    `is_backtesting = True` matters: it is what keeps the strategy's per-bar
    INFO chatter out of the activity feed and, in `RiskEngine`, out of the
    logging hot path. Generating 80,000 bars of signals with live logging on
    was measured at 25ms per signal.
    """
    from backend.strategies.registry import get_strategy

    engine = get_strategy(strategy_id)(config)
    engine.is_backtesting = True
    return engine




def derive_missing(frames_by_tf: dict[str, pd.DataFrame], required: list[str],
                   primary: str, symbol: str = "", strategy_id: str = "") -> dict:
    """Build any higher timeframe the base strategy needs from the primary bars.

    A mode-B agent must see exactly what its base strategy sees, and most
    strategies here want two or three timeframes. Asking the caller to supply
    every one would mean an H1 and an H4 export per symbol before an agent can
    be trained, which is a lot of friction for frames that are a deterministic
    function of the M5 bars.

    Derived through `datasets.resample`, which builds HTF bars only from
    COMPLETE periods and drops the one still forming. That is the whole safety
    argument: an H1 bar built from the M5 bars available at 10:20 is not the
    10:00 H1 bar, and handing it to a strategy is up to 55 minutes of
    lookahead. Afterwards `assert_htf_alignment` checks the result rather than
    trusting it.

    A LOWER timeframe cannot be derived - there is no way to invent M1 bars
    from M5 - so that is refused rather than guessed.
    """
    from backend.rl import datasets as ds
    from backend.strategies.bar_feed import TF_MINUTES

    out = dict(frames_by_tf or {})
    if primary not in out:
        raise ValueError(f"the primary timeframe {primary!r} was not supplied")
    base = out[primary]
    base_minutes = TF_MINUTES.get(str(primary).upper())

    for tf in required:
        if tf in out:
            continue
        minutes = TF_MINUTES.get(str(tf).upper())
        if not minutes or not base_minutes:
            raise ValueError(f"unknown timeframe {tf!r}")
        if minutes < base_minutes:
            raise ValueError(
                f"{strategy_id or 'the base strategy'} needs {tf}, which is FASTER "
                f"than the {primary} bars supplied. A lower timeframe cannot be "
                f"derived - supply {tf} bars for {symbol or 'this symbol'}.")
        built = ds.resample(base.reset_index(drop=False) if "time" not in base.columns
                            else base, minutes)
        ds.assert_htf_alignment(base, built, minutes)
        out[tf] = built.set_index(pd.to_datetime(built["time"], unit="s")).sort_index()
        logger.info(f"[RL] {symbol}/{strategy_id}: derived {len(out[tf])} {tf} bars "
                    f"from {primary}")
    return out


def generate(symbol: str, timeframe: str, frames_by_tf: dict[str, pd.DataFrame],
             strategy_id: str, config: Any, *, start_index: int = 300) -> list[dict]:
    """Every signal `strategy_id` finds on these bars, in time order.

    `frames_by_tf` must be indexed by bar-open time and sorted, which is what
    `BarFeed` expects — the same frames a backtest route hands it. The agent
    inherits the feed's causality guarantee for free this way: at primary bar
    `i` the strategy sees bars up to `i-1` and only HTF bars that had closed.
    """
    from backend.strategies.bar_feed import BarFeed, primary_timeframe

    engine = build_strategy(strategy_id, config)
    required = list(getattr(engine, "get_required_timeframes", lambda: [timeframe])())
    frames_by_tf = derive_missing(frames_by_tf, required, timeframe, symbol, strategy_id)

    feed = BarFeed(engine, symbol, required)
    feed.bind(frames_by_tf)
    primary = primary_timeframe(required)
    times = frames_by_tf[primary].index.values
    # The AGENT's bars, which `bar_index` must index. When the strategy's own
    # primary timeframe differs (ORB_v1 decides on M15, an agent on M5), bar
    # `i` of the strategy is not bar `i` of the agent, and keying on it
    # attached every setup to the wrong M5 bar - three times too early. So the
    # setup is re-addressed by TIME: the agent bar that opens when the
    # strategy's bar `i` opens, which is where the engine fills it.
    agent_times = (frames_by_tf[timeframe].index.values
                   if timeframe in frames_by_tf else times)
    remap = timeframe in frames_by_tf and primary != timeframe

    out: list[dict] = []

    async def run() -> None:
        for i in range(int(start_index), len(times)):
            sig = await feed.step(i)
            if not sig:
                continue
            j = i
            if remap:
                j = int(np.searchsorted(agent_times, times[i], side="left"))
                if j <= 0 or j >= len(agent_times):
                    continue
            out.append({
                "symbol": sig.symbol,
                "direction": sig.direction,
                # bar j-1, so the engine fills at bar j's open. See the note
                # at the top of this module.
                "time": int(pd.Timestamp(agent_times[j - 1]).timestamp()),
                "entry_price": float(sig.entry_price),
                "stop_loss": float(sig.stop_loss),
                "take_profit": float(sig.take_profit),
                "timeframe": sig.timeframe,
                "confluence_score": int(sig.confluence_score),
                "metadata": dict(sig.metadata or {}),
                "strategy_name": strategy_id,
                # The agent's DECISION row: the last bar that had CLOSED when the
                # strategy decided (j-1), the same bar the signal is stamped
                # with. The env decides on this row and stamps it, so the agent
                # fills at bar j's open - the bar the strategy itself fills on.
                # It used to be j, so the agent decided a bar later and filled
                # one bar AFTER its base strategy - not look-ahead, but the same
                # one-bar-late error that cost GBPJPY 10R in September, and it
                # did not match how live decides (found 2026-10-18, building
                # live mode B).
                "bar_index": j - 1,
            })
            if len(out) >= MAX_BASE_SIGNALS:
                logger.warning(
                    f"[RL] {symbol}/{strategy_id}: base-signal cap {MAX_BASE_SIGNALS} "
                    f"reached at bar {i} of {len(times)} — the rest of this frame is "
                    f"not covered")
                break

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run())
    else:
        # Generating signals synchronously inside a running loop would block
        # it for the whole frame. The callers that have a loop (the API) must
        # use `asyncio.to_thread`, which lands in the branch above.
        raise RuntimeError(
            "base signals must not be generated on the event loop — call this "
            "through asyncio.to_thread")
    logger.info(f"[RL] {symbol}/{strategy_id}: {len(out)} base signals over "
                f"{len(times)} bars")
    return out


def index_frames(frames_by_tf: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Index each frame by bar-open time, which is what `BarFeed` requires."""
    out = {}
    for tf, df in (frames_by_tf or {}).items():
        if df is None or not len(df):
            continue
        d = df.copy()
        if "time" in d.columns:
            d = d.set_index(pd.to_datetime(d["time"], unit="s"))
        d = d.sort_index()
        out[tf] = d
    return out


def by_bar(signals: list[dict]) -> dict[int, dict]:
    """Base signals keyed by the bar they were FOUND on.

    Keyed by `bar_index` rather than by time because that is what the policy's
    observation matrix is indexed by, and the join between "what the strategy
    saw" and "what the agent saw" has to be exact. Keying on the wrong one is
    the mistake that made every trade score zero adherence earlier in this
    build.
    """
    return {int(s["bar_index"]): s for s in (signals or []) if "bar_index" in s}
