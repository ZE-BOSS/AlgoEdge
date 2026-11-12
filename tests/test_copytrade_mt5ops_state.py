"""mt5ops.ticket_state must never call a live trade "gone" because the terminal
could not answer (disconnected, re-authorising, still syncing)."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.copytrade import mt5ops


class Term:
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY, DEAL_REASON_TP = 0, 1, 3, 5

    def __init__(self, orders=(), positions=(), deals=(), hist_orders=(), connected=True, logged_in=True):
        self._o, self._p, self._d, self._h = orders, positions, deals, hist_orders
        self.connected, self.logged_in = connected, logged_in

    def orders_get(self, ticket):
        return self._o

    def positions_get(self, ticket):
        return self._p

    def history_deals_get(self, position):
        return self._d

    def history_orders_get(self, ticket):
        return self._h

    def terminal_info(self):
        return SimpleNamespace(connected=self.connected)

    def account_info(self):
        return SimpleNamespace(login=1) if self.logged_in else None


@pytest.fixture
def term(monkeypatch):
    async def run(fn, *a, **kw):
        return fn(*a, **kw)
    monkeypatch.setattr(mt5ops, "run_mt5", run)

    def use(t):
        monkeypatch.setattr(mt5ops, "mt5", t)
    return use


def state(t=None):
    return asyncio.run(mt5ops.ticket_state(7))


IN = SimpleNamespace(entry=0, reason=0)
OUT_TP = SimpleNamespace(entry=1, reason=5)


@pytest.mark.parametrize("kw,expected", [
    (dict(orders=None), "UNKNOWN"),                                 # terminal error
    (dict(positions=None), "UNKNOWN"),
    (dict(orders=(1,)), "PENDING"),
    (dict(positions=(1,)), "OPEN"),
    (dict(deals=(IN, OUT_TP)), "GONE"),                             # opened and closed
    (dict(deals=(IN,)), "UNKNOWN"),                                 # opened, never closed: still syncing
    (dict(hist_orders=(1,)), "GONE"),                               # cancelled / expired order
    (dict(), "MISSING"),                                            # no trace anywhere: another account's ticket
    (dict(hist_orders=None), "UNKNOWN"),                            # history did not answer
    (dict(deals=(IN, OUT_TP), connected=False), "UNKNOWN"),          # disconnected
    (dict(deals=(IN, OUT_TP), logged_in=False), "UNKNOWN"),          # "Authorization failed"
    (dict(deals=None), "UNKNOWN"),
])
def test_ticket_state(term, kw, expected):
    term(Term(**kw))
    assert state() == expected


def test_closed_by_tp_unknown_is_not_no(term):
    term(Term(deals=None))
    assert asyncio.run(mt5ops.closed_by_tp(7)) is None
    term(Term(deals=(IN, OUT_TP)))
    assert asyncio.run(mt5ops.closed_by_tp(7)) is True
