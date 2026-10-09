"""
backend/rl/notify.py

What the admin is told, and when.

WHY TELEGRAM AND NOT THE ACTIVITY FEED
--------------------------------------
The feed is only read by someone already looking at the screen. These events
are the ones where nobody is: a training run that finished at 3am, an agent
that drifted off its charter and suspended itself, a proposal waiting for a
decision, a trade the agent closed on its own. Each of those is a thing the
admin would want to know about within minutes and would otherwise discover
hours later.

WHAT IS DELIBERATELY NOT SENT
-----------------------------
Every bar, every signal, every scan. A channel that fires constantly is a
channel nobody reads, and the events below were chosen because each one is
either a decision to make or a state change that cannot be undone.

EVERY SEND IS GUARDED
---------------------
A notification failure must never change what the trading system does. These
functions swallow their own errors and log; the thing being announced has
already happened by the time they are called.
"""

from __future__ import annotations

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# One place, so the message shapes stay recognisable to someone scanning a
# phone screen. A leading glyph per kind is the whole formatting budget.
TRAINED = "\U0001F9EA"        # test tube
APPROVAL = "\U0001F4DD"       # memo
SUSPENDED = "\U000026D4"      # no entry
EXIT = "\U0001F3C1"           # chequered flag
OPENED = "\U0001F4C8"         # chart up


async def _send(text: str) -> None:
    try:
        from backend.services.telegram import telegram_service

        if not telegram_service.configured:
            logger.debug("[RL] Telegram is not configured - not sending")
            return
        await telegram_service.send_message(text)
    except Exception as exc:                            # noqa: BLE001
        # The event has already happened. Telling someone about it is the part
        # that is allowed to fail.
        logger.warning(f"[RL] Telegram notification failed: {exc}")


def _fire(text: str) -> None:
    """Send without making the caller async.

    Most of these are called from the scan loop or a worker thread, neither of
    which can await. A task on the running loop when there is one, a plain
    blocking send when there is not - and nothing at all if both fail.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(_send(text))
        except Exception as exc:                        # noqa: BLE001
            logger.warning(f"[RL] could not send a Telegram notification: {exc}")
        return
    loop.create_task(_send(text))


# -- the events -------------------------------------------------------------


def training_finished(agent_id: str, report: dict) -> None:
    """A run finished. The numbers, not just the fact.

    "Training complete" on its own means another trip to the laptop. The seeds,
    the dispersion and the best fold's result are the three things that decide
    whether it is worth looking at now or in the morning.
    """
    results = report.get("results") or []
    best = report.get("best") or {}
    dispersion = report.get("sharpe_dispersion")
    lines = [
        f"{TRAINED} *Training finished* — `{agent_id}`",
        f"optimiser: {report.get('optimiser', '?')}",
        f"{len(results)} (seed, path) results in "
        f"{float(report.get('seconds') or 0) / 60:.0f} min",
    ]
    if best:
        lines.append(f"best: seed {best.get('seed')} path {best.get('fold')} — "
                     f"{best.get('test_trades', 0)} test trades at "
                     f"{float(best.get('test_mean_r') or 0):+.4f}R")
    if dispersion is not None:
        lines.append(f"Sharpe spread across seeds: {float(dispersion):.2f}"
                     + ("  (over 1.0 — that is not a result)"
                        if float(dispersion) > 1.0 else ""))
    if report.get("error"):
        lines.append(f"error: {report['error']}")
    lines.append("Distil its charter and run the gate before approving anything.")
    _fire("\n".join(lines))


def validation_finished(agent_id: str, report: dict) -> None:
    """The gate ran. Pass or fail, and which checks failed."""
    passed = report.get("passed")
    head = "passed the gate" if passed else "was REJECTED by the gate"
    lines = [
        f"{TRAINED} `{agent_id}` {head}",
        f"{report.get('trades', 0)} trades at "
        f"{float(report.get('expectancy_r') or 0):+.4f}R",
    ]
    failures = report.get("failures") or []
    if failures:
        lines.append("failed: " + ", ".join(failures))
    skipped = report.get("skipped") or []
    if skipped:
        lines.append("could not be measured: " + ", ".join(skipped))
    if passed:
        lines.append("It is waiting for you to approve its charter. Nothing reaches "
                     "a live slot until you do.")
    _fire("\n".join(lines))


def approval_wanted(agent_id: str, what: str, detail: str = "") -> None:
    """Something is waiting for a decision - a charter or a proposal."""
    lines = [f"{APPROVAL} *{what}* — `{agent_id}`"]
    if detail:
        lines.append(detail)
    lines.append("It cannot trade live until you approve it.")
    _fire("\n".join(lines))


def suspended(agent_id: str, version: int, message: str) -> None:
    """An agent drifted off its charter and stopped itself."""
    _fire(f"{SUSPENDED} *Agent suspended* — `{agent_id}` v{version}\n{message}\n"
          f"It does not fall back to trading unsupervised. Re-enable it by hand "
          f"once you have looked at why.")


def trade_opened(agent_id: str, symbol: str, direction: str, rr: float,
                 tier: str | None, adherence: float,
                 stop_atr_multiple: float | None = None) -> None:
    """An agent opened a trade, and the two risk numbers it chose.

    The stop distance and the R:R are the whole of what an agent decides about
    risk. The lot size that follows from them is the risk layer's, and so are
    the risk percent and every cap - none of which is in the agent's action
    space. So this line is the complete statement, not a summary of one.
    """
    bits = [f"{OPENED} `{agent_id}` opened {direction} {symbol}"]
    risk = f"target 1:{rr:.1f}"
    if stop_atr_multiple:
        risk = f"stop {stop_atr_multiple:g}x ATR · " + risk
    bits.append(risk + (f" · tier {tier}" if tier else "")
                + f" · adherence {adherence:.2f}")
    _fire("\n".join(bits))


def position_action(agent_id: str, symbol: str, action: str, reason: str,
                    gates: tuple | list = (), confidence: float = 0.0,
                    foreign_owner: str = "") -> None:
    """The agent closed, part-closed or tightened a running trade."""
    what = {
        "CLOSE_ALL": "closed", "CLOSE_PARTIAL": "took a partial on",
        "TIGHTEN_STOP": "tightened the stop on",
    }.get(action, action)
    lines = [f"{EXIT} `{agent_id}` {what} {symbol}"]
    if foreign_owner:
        lines.append(f"supervising {foreign_owner}")
    lines.append(f"{reason} · confidence {confidence:.2f}")
    if gates:
        lines.append("because: " + ", ".join(gates))
    _fire("\n".join(lines))
