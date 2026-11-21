"""A channel added to the Copy Trading page is not copied until switched on
(21 Nov 2026: channels added to backtest them copied to live at once)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import backend.api.routes.copytrade as rt


class _Res:
    def __init__(self, row):
        self.row = row

    def scalars(self):
        return self

    def first(self):
        return self.row


class _DB:
    def __init__(self, existing=None):
        self.existing, self.added = existing, []

    async def execute(self, _q):
        return _Res(self.existing)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        pass


def _run(body, db, monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(rt.telegram_manager, "reload_channels", _noop)
    return asyncio.run(rt.add_channel(body, user=SimpleNamespace(id="u1"), db=db))


def test_new_channel_is_added_switched_off(monkeypatch):
    db = _DB()
    out = _run(rt.ChannelIn(chat_id=-100123, title="Signals"), db, monkeypatch)
    assert db.added[0].enabled is False and out["enabled"] is False


def test_readding_a_switched_off_channel_keeps_it_off(monkeypatch):
    row = rt.CopyTradeChannel(user_id="u1", chat_id=-100123, title="Signals", enabled=False, rules={})
    _run(rt.ChannelIn(chat_id=-100123), _DB(existing=row), monkeypatch)
    assert row.enabled is False


def test_explicit_enable_still_works(monkeypatch):
    db = _DB()
    _run(rt.ChannelIn(chat_id=-100123, enabled=True), db, monkeypatch)
    assert db.added[0].enabled is True
