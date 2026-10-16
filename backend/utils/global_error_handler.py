"""
backend/utils/global_error_handler.py

A unified exception handler that intercepts unhandled errors globally,
logs them to the local logger (which feeds the frontend), and dispatches
a Telegram alert.
"""
import asyncio
import queue
import sys
import threading
import traceback

from loguru import logger

_is_setup = False
_telegram_rate_limit = {}

# ── WHY ALERTS GO THROUGH A SEPARATE THREAD ─────────────────────────────────
#
# `error_sink` is registered with `enqueue=True`, so loguru runs it on its own
# WRITER THREAD. Two things this sink used to do there are each enough to
# freeze the process, and both were found by a test that hung for 1.7 hours
# (2026-10-13) - the stack dump showed the main thread waiting on loguru's
# handler lock while the writer thread was blocked inside this sink.
#
#  1. It LOGGED. `bot_service._log_event` writes through the logger, so the
#     sink put a new record onto the very queue the writer thread is the only
#     consumer of. One error at a time that drains; a burst fills the pipe,
#     `put` blocks, nothing can drain it, and every thread that logs from then
#     on waits forever. The `func == "_log_event"` guard below stops an
#     INFINITE loop but not this - each original error still re-entered once.
#     Fixed by `_log_event(..., echo=False)`: record for the activity log,
#     write nothing to the logger. The error was already logged once; that is
#     why the sink fired at all, and why the VPS log showed every MT5 error
#     twice.
#
#  2. It sent the Telegram alert SYNCHRONOUSLY. The writer thread has no event
#     loop, so `get_running_loop()` always raised and the fallback was
#     `asyncio.run(send_message(...))` - a blocking HTTP round trip to
#     api.telegram.org inside the logging pipeline, once per error. On a slow
#     or dropped connection every log call in the process queued behind it.
#
# So the sink now only drops the message onto a bounded in-memory queue and
# returns. One daemon thread owns the network. If that queue is full the
# alert is DROPPED: losing one notification is a far better failure than
# stalling every log call in a process that places live orders.
_ALERT_QUEUE_MAX = 200
_alert_queue: "queue.Queue[str]" = queue.Queue(maxsize=_ALERT_QUEUE_MAX)
_alert_sender_started = False
_alert_sender_lock = threading.Lock()
_alerts_dropped = 0


def _alert_sender_loop() -> None:
    """Owns the network for error alerts, so nothing else has to."""
    from backend.services.telegram import telegram_service

    while True:
        msg = _alert_queue.get()
        try:
            asyncio.run(telegram_service.send_message(msg))
        except Exception:
            # Never log from here either: this thread exists precisely so a
            # failing send cannot reach back into the logging pipeline.
            pass


def _ensure_alert_sender() -> None:
    global _alert_sender_started
    if _alert_sender_started:
        return
    with _alert_sender_lock:
        if _alert_sender_started:
            return
        threading.Thread(target=_alert_sender_loop, name="error-alert-sender",
                         daemon=True).start()
        _alert_sender_started = True


def _queue_alert(msg: str) -> bool:
    """Hand an alert to the sender thread. Never blocks. False if dropped."""
    global _alerts_dropped
    _ensure_alert_sender()
    try:
        _alert_queue.put_nowait(msg)
        return True
    except queue.Full:
        _alerts_dropped += 1
        return False


def alert_queue_status() -> dict:
    """For /api/health: how many alerts are waiting and how many were lost."""
    return {"queued": _alert_queue.qsize(), "max": _ALERT_QUEUE_MAX,
            "dropped": _alerts_dropped, "sender_running": _alert_sender_started}

def setup_global_error_handler():
    global _is_setup
    if _is_setup:
        return
    _is_setup = True

    # Resolve what the alert path needs NOW, on the calling thread - never
    # from inside the sink. `backend.services.telegram` imports
    # `backend.utils.logger`, whose module body calls `logger.remove()`. Run
    # that for the first time on loguru's writer thread and `remove` waits for
    # the writer thread to stop - i.e. for itself. Imported here, before the
    # sink is added, the remove() cannot take the sink with it either.
    try:
        import backend.services.telegram  # noqa: F401
    except Exception:
        pass

    # 1. Add a loguru sink for ERROR and CRITICAL logs to Telegram
    def error_sink(message):
        record = message.record
        if record["level"].name in ("ERROR", "CRITICAL"):
            msg_text = record["message"]
            module = record["name"]
            func = record["function"]
            line = record["line"]

            # Prevent infinite loops if telegram or logger itself errors
            if "telegram" in module.lower():
                return
            if "bot_service" in module.lower() and func == "_log_event":
                return
                
            formatted_msg = f"🚨 *SYSTEM ERROR* 🚨\n\n*Module:* `{module}:{func}:{line}`\n*Level:* `{record['level'].name}`\n\n*Message:*\n```\n{msg_text}\n```"

            if record["exception"]:
                exc = record["exception"]
                if exc:
                    formatted_msg += f"\n*Exception:*\n```\n{exc.type.__name__}: {exc.value}\n```"

            try:
                # NEVER import from in here - an import runs module code, and
                # module code here includes logger reconfiguration (see setup).
                # bot_service is only imported inside the app's lifespan, so an
                # error raised before that simply has no activity log to write
                # to yet; it is still logged and still alerted.
                _bs_mod = sys.modules.get("backend.services.bot_service")
                bot_service = getattr(_bs_mod, "bot_service", None) if _bs_mod else None

                # Log to frontend activity log
                # echo=False: this runs on loguru's writer thread, and logging
                # from here is the deadlock described at the top of the file.
                if bot_service is not None:
                    bot_service._log_event(f"SYSTEM ERROR: {msg_text}", level="ERROR",
                                           category="SYSTEM", echo=False)
                
                # Rate limiting — known infrastructure issues get 1 hour suppression
                import time
                now = time.time()
                _INFRA_PATTERNS = (
                    "not found in mt5",
                    "data fetch failed",
                    "symbol not found",
                    "mt5 is offline",
                    "backtest cancelled",
                    "portfolio backtest cancelled",
                    "modify sl failed",
                    "failed to modify sl",
                    "invalid stops",
                )
                is_infra = any(p in msg_text.lower() for p in _INFRA_PATTERNS)
                rate_limit_secs = 3600 if is_infra else 60

                if msg_text in _telegram_rate_limit:
                    if now - _telegram_rate_limit[msg_text] < rate_limit_secs:
                        return # Skip duplicate within window
                _telegram_rate_limit[msg_text] = now

                # Demote known infra warnings to ⚠️ instead of 🚨 SYSTEM ERROR
                if is_infra:
                    formatted_msg = f"⚠️ *Infrastructure Warning*\n\n*Module:* `{module}:{func}:{line}`\n\n*Message:*\n```\n{msg_text}\n```"
                
                # Not `loop.create_task` and not `asyncio.run`: this thread has
                # no loop, so the first always raised and the second was a
                # blocking HTTP call inside the logging pipeline. Queued instead.
                _queue_alert(formatted_msg)
            except Exception:
                pass
                
    # Add sink to catch errors generated via logger.error()
    logger.add(error_sink, level="ERROR", enqueue=True)

    # 2. Intercept unhandled Python exceptions globally
    def global_excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        # loguru handles formatting and will trigger the error_sink above
        logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(
            f"Unhandled global exception: {exc_value}"
        )

    sys.excepthook = global_excepthook
