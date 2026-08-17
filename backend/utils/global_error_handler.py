"""
backend/utils/global_error_handler.py

A unified exception handler that intercepts unhandled errors globally,
logs them to the local logger (which feeds the frontend), and dispatches
a Telegram alert.
"""
import sys
import traceback
import asyncio
from loguru import logger

_is_setup = False
_telegram_rate_limit = {}

def setup_global_error_handler():
    global _is_setup
    if _is_setup:
        return
    _is_setup = True

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
                from backend.services.telegram import telegram_service
                from backend.services.bot_service import bot_service
                
                # Log to frontend activity log
                bot_service._log_event(f"SYSTEM ERROR: {msg_text}", level="ERROR", category="SYSTEM")
                
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
                
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(telegram_service.send_message(formatted_msg))
                except RuntimeError:
                    # No running loop
                    asyncio.run(telegram_service.send_message(formatted_msg))
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
