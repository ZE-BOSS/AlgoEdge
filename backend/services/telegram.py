"""
backend/services/telegram.py

Service for sending Telegram notifications asynchronously.
"""

import asyncio

import aiohttp

from backend.utils.logger import get_logger

logger = get_logger(__name__)

class TelegramService:
    def __init__(self):
        self.bot_token = ""
        self.chat_id = ""

    def update_config(self, token: str, chat_id: str):
        self.bot_token = token
        self.chat_id = chat_id

    def escape_markdown(self, text: str) -> str:
        """Escape special characters for Telegram Markdown V1."""
        if not text:
            return ""
        # In Markdown V1, _, *, `, [ are special.
        # But we only want to escape _ and * inside strings to prevent format breaking.
        text = str(text)
        text = text.replace('_', '\\_')
        text = text.replace('*', '\\*')
        text = text.replace('`', '\\`')
        text = text.replace('[', '\\[')
        return text

    async def send_message(self, message: str, parse_mode: str = "Markdown"):
        if not self.bot_token or not self.chat_id:
            return
            
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        
        chat_ids = [cid.strip() for cid in self.chat_id.split(",") if cid.strip()]
        
        async def _send(session, cid):
            payload = {
                "chat_id": cid,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            }
            try:
                async with session.post(url, json=payload, timeout=5.0) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        logger.warning(f"Telegram API error for chat {cid}: {err}")
            except Exception as e:
                logger.error(f"Failed to send Telegram message to {cid}: {e}")

        try:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = [_send(session, cid) for cid in chat_ids]
                await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Telegram session error: {e}")

telegram_service = TelegramService()
