"""A rejection report must actually reach Telegram from the training worker.

Telegram credentials are loaded from the database by the API's startup, into
the API process only. Training runs in a separate worker process, so the
training report found an empty token every time and was dropped with "no bot
token configured" - and nothing anywhere said so.
"""

import asyncio

from backend.services import rejection_report as rr

FUNNEL = {"raw_signals": 10, "approved": 0, "risk_rejections": {"below_min_lot": 10}}


def test_an_unconfigured_process_loads_credentials_before_sending(monkeypatch):
    from backend.services import telegram as T

    calls = []
    svc = T.TelegramService()                      # fresh: no token, as in a worker

    async def fake_load():
        calls.append("load")
        svc.update_config("token", "123")
        return True

    async def fake_send(message, parse_mode="Markdown"):
        calls.append("send")
        return {"ok": svc.configured}

    monkeypatch.setattr(T, "telegram_service", svc)
    monkeypatch.setattr(T, "load_telegram_config_any_user", fake_load)
    monkeypatch.setattr(svc, "send_message", fake_send)

    out = asyncio.run(rr.notify(FUNNEL, title="t"))
    assert calls == ["load", "send"]
    assert out == {"ok": True}


def test_an_already_configured_process_does_not_reload(monkeypatch):
    from backend.services import telegram as T

    svc = T.TelegramService()
    svc.update_config("token", "123")
    loaded = []

    async def fake_load():
        loaded.append(1)
        return True

    async def fake_send(message, parse_mode="Markdown"):
        return {"ok": True}

    monkeypatch.setattr(T, "telegram_service", svc)
    monkeypatch.setattr(T, "load_telegram_config_any_user", fake_load)
    monkeypatch.setattr(svc, "send_message", fake_send)

    asyncio.run(rr.notify(FUNNEL, title="t"))
    assert loaded == []
