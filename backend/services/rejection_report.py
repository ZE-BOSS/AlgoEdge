"""
backend/services/rejection_report.py

Why signals did not become trades, in one place and in one shape.

WHY THIS EXISTS
---------------
On 2026-10-11 an RL training run reported 0 trades, +0.0000R and a seed
dispersion of 0.00 - which reads as "the five seeds agreed" and is in fact
"nothing was ever measured". The cause was an empty risk config, under which
the engine refuses every signal it is handed: 883 signals raised, 0 executed.
Finding that took a day of reading code, and the engine had been counting the
refusals the whole time in `rejection_funnel` - nothing ever showed them.

So: the funnel is reported after a training run, reported after a backtest,
and sent as it happens when the bot is live. A run that takes no trades should
say WHY on the first screen you look at, not on the fourth day.

THE SHAPE
---------
The engine's `rejection_funnel` (see `backtester/engine.py`):

    raw_signals          what the strategy found
    total_evaluated      what reached the risk engine
    approved             what became a trade
    strategy_rejections  {gate: n}   the strategy's own gates
    pre_risk_rejections  {gate: n}   concurrency/throttle, before risk saw it
    risk_rejections      {reason: n} the risk engine
    fill_rejections      {reason: n} re-validated against the actual fill
    errors               n

`merge` sums two of those; `format_funnel` turns one into a message.
"""

from __future__ import annotations

from typing import Any

# The dict-valued buckets, in the order a reader wants them: the strategy's own
# gates first (it never wanted the trade), then everything that took the trade
# away from it.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("strategy_rejections", "strategy gates"),
    ("pre_risk_rejections", "concurrency / throttle"),
    ("risk_rejections", "risk engine"),
    ("fill_rejections", "re-check at fill"),
)
_COUNTERS = ("raw_signals", "total_evaluated", "approved", "errors")


def empty_funnel() -> dict[str, Any]:
    return {k: 0 for k in _COUNTERS} | {k: {} for k, _ in BUCKETS}


def merge(dst: dict[str, Any] | None, src: dict[str, Any] | None) -> dict[str, Any]:
    """Sum `src` into `dst` and return it. Neither needs every key.

    Used to roll up one funnel per rollout into one funnel per training run -
    a run is thousands of rollouts and the per-rollout numbers mean nothing on
    their own.
    """
    out = dst if dst is not None else empty_funnel()
    for k in _COUNTERS:
        out[k] = int(out.get(k, 0) or 0) + int((src or {}).get(k, 0) or 0)
    for key, _ in BUCKETS:
        into = out.setdefault(key, {})
        for reason, n in ((src or {}).get(key) or {}).items():
            into[str(reason)] = int(into.get(str(reason), 0) or 0) + int(n or 0)
    return out


def totals(funnel: dict[str, Any] | None) -> dict[str, int]:
    """Headline numbers. `rejected` is counted from the buckets rather than as
    raised-minus-approved: the two disagree when a signal is dropped before the
    census, and the bucket sum is the one that names a cause."""
    f = funnel or {}
    by_bucket = {k: sum(int(v or 0) for v in (f.get(k) or {}).values())
                 for k, _ in BUCKETS}
    return {
        "raised": int(f.get("raw_signals", 0) or 0),
        "evaluated": int(f.get("total_evaluated", 0) or 0),
        "approved": int(f.get("approved", 0) or 0),
        "errors": int(f.get("errors", 0) or 0),
        "rejected": sum(by_bucket.values()),
        **{f"n_{k}": v for k, v in by_bucket.items()},
    }


def has_anything(funnel: dict[str, Any] | None) -> bool:
    t = totals(funnel)
    return bool(t["raised"] or t["evaluated"] or t["rejected"] or t["approved"])


def format_funnel(funnel: dict[str, Any] | None, *, title: str,
                  subtitle: str = "", top: int = 6) -> str:
    """A Telegram message. Plain text: the reasons are engine-generated keys
    and running them through a Markdown parser is a way to lose the message
    entirely to an unbalanced underscore."""
    t = totals(funnel)
    took = t["approved"]
    raised = t["raised"] or t["evaluated"]
    pct = (100.0 * took / raised) if raised else 0.0

    head = "⚠️" if (raised and not took) else "📉" if t["rejected"] else "✅"
    lines = [f"{head} {title}"]
    if subtitle:
        lines.append(subtitle)
    lines.append("")
    lines.append(f"raised     {raised:>8,}")
    lines.append(f"taken      {took:>8,}   ({pct:.1f}%)")
    lines.append(f"rejected   {t['rejected']:>8,}")
    if t["errors"]:
        lines.append(f"errors     {t['errors']:>8,}")

    any_reason = False
    for key, label in BUCKETS:
        reasons = {k: int(v or 0) for k, v in ((funnel or {}).get(key) or {}).items()
                   if int(v or 0) > 0}
        if not reasons:
            continue
        any_reason = True
        lines.append("")
        lines.append(f"{label}  ({sum(reasons.values()):,})")
        ranked = sorted(reasons.items(), key=lambda kv: -kv[1])
        for reason, n in ranked[:top]:
            lines.append(f"  {n:>7,}  {reason}")
        if len(ranked) > top:
            rest = sum(n for _, n in ranked[top:])
            lines.append(f"  {rest:>7,}  ... and {len(ranked) - top} more causes")

    if raised and not took and not any_reason:
        # The case that started this: signals raised, none taken, and no bucket
        # names a cause. Saying so is better than an empty report.
        lines.append("")
        lines.append("No cause was recorded for any of them, which is itself "
                     "the finding - the signals were dropped before the risk "
                     "engine counted them.")
    return "\n".join(lines)


async def notify(funnel: dict[str, Any] | None, *, title: str,
                 subtitle: str = "", only_if_rejections: bool = True) -> dict:
    """Send the funnel to Telegram. Never raises - a reporting path must not
    be able to fail the run it is reporting on."""
    try:
        if not has_anything(funnel):
            return {"ok": False, "reason": "nothing to report"}
        if only_if_rejections and not totals(funnel)["rejected"]:
            return {"ok": False, "reason": "no rejections"}
        from backend.services.telegram import load_telegram_config_any_user, telegram_service
        # The credentials are loaded from the database by the API's startup,
        # into THAT process. A training run happens in a separate worker
        # process where nothing had loaded them, so every training report
        # returned "no bot token configured" and was silently dropped. Load
        # them here when they are missing - a no-op in the API process.
        if not telegram_service.configured:
            await load_telegram_config_any_user()
        return await telegram_service.send_message(
            format_funnel(funnel, title=title, subtitle=subtitle), parse_mode="")
    except Exception as exc:                                # noqa: BLE001
        from backend.utils.logger import get_logger
        get_logger(__name__).warning(f"[REJECT] could not send report: {exc}")
        return {"ok": False, "reason": str(exc)}
