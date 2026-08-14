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

            # Prevent infinite loops if telegram itself errors
            if "telegram" in module.lower():
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
                
                # Check rate limiting per exact message
                import time
                now = time.time()
                if msg_text in _telegram_rate_limit:
                    if now - _telegram_rate_limit[msg_text] < 60:
                        return # Skip duplicate within 60s
                _telegram_rate_limit[msg_text] = now
                
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
