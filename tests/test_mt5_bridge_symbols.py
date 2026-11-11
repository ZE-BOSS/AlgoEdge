"""The bridge sends the broker's own name for a symbol the backend asks for by
its plain name: Exness lists gold as XAUUSDm, the dashboard / bot / copier ask
for XAUUSD. Reported 11 Nov on the live (Exness) server."""

from __future__ import annotations

import collections

from backend.mt5.bridge_client import BridgedMT5, pick_broker_symbol

Sym = collections.namedtuple("SymbolInfo", ["name", "visible"])


class FakeBridge(BridgedMT5):
    def __init__(self, names):
        super().__init__(("127.0.0.1", 1), b"k")
        self.__dict__["names"] = names
        self.__dict__["sent"] = []

    def _request(self, msg, *, resend=True):
        _, fn, args, kwargs = msg
        self.__dict__["sent"].append((fn, args, kwargs))
        names = self.__dict__["names"]
        if fn == "symbol_info":
            return Sym(args[0], True) if args[0] in names else None
        if fn == "symbols_get":
            return tuple(Sym(n, False) for n in names)
        if fn == "order_send":
            return {"symbol": args[0]["symbol"]}
        return (fn, args, kwargs)


EXNESS = ["XAUUSDm", "EURUSDm", "GBPJPYm", "BTCUSDm", "US30m", "USTECm", "XAGUSDm"]


def test_pick_broker_symbol():
    assert pick_broker_symbol("XAUUSD", EXNESS) == "XAUUSDm"
    assert pick_broker_symbol("XAUUSD", ["XAUUSD.pro", "XAUUSD.m.old"]) == "XAUUSD.pro"
    assert pick_broker_symbol("XAUUSD", ["XAUUSDm", "XAUUSDc"], suffix="c") == "XAUUSDc"
    assert pick_broker_symbol("XAUUSD", ["XAUEUR", "XAGUSD"]) is None
    assert pick_broker_symbol("EURUSD", ["EURUSDCHFX_long_name"]) is None


def test_every_kind_of_call_gets_the_brokers_name():
    b = FakeBridge(EXNESS)
    assert b.copy_rates_from_pos("XAUUSD", 15, 0, 200)[1][0] == "XAUUSDm"      # dashboard chart
    assert b.symbol_info_tick("XAUUSD")[1][0] == "XAUUSDm"                      # prices
    assert b.symbol_select("XAUUSD", True)[1][0] == "XAUUSDm"
    assert b.positions_get(symbol="XAUUSD")[2]["symbol"] == "XAUUSDm"
    assert b.order_calc_margin(0, "XAUUSD", 0.1, 4000.0)[1][1] == "XAUUSDm"
    assert b.order_send({"symbol": "XAUUSD", "volume": 0.1})["symbol"] == "XAUUSDm"   # the bot's orders
    assert b.symbol_info("XAUUSD").name == "XAUUSDm"


def test_names_that_exist_are_left_alone_and_lookups_are_cached():
    b = FakeBridge(["XAUUSD", "Volatility 75 Index", "XAUUSDm"])
    assert b.symbol_info_tick("XAUUSD")[1][0] == "XAUUSD"
    assert b.symbol_info_tick("Volatility 75 Index")[1][0] == "Volatility 75 Index"
    b.symbol_info_tick("XAUUSD")
    lookups = [s for s in b.__dict__["sent"] if s[0] == "symbol_info"]
    assert len(lookups) == 2                                                      # once per name


def test_unknown_name_passes_through_and_a_new_login_forgets_the_map():
    b = FakeBridge(EXNESS)
    assert b.symbol_info_tick("NOPE")[1][0] == "NOPE"
    assert b.broker_symbol("XAUUSD") == "XAUUSDm"
    b.__dict__["names"] = ["XAUUSD"]                                              # logged in elsewhere
    b.login(1, password="x", server="y")
    assert b.broker_symbol("XAUUSD") == "XAUUSD"
