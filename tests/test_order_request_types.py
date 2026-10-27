"""
The live order request must carry exact Python types, and a refused request
must say why.

On the Linux VPS the MetaTrader5 package (5.0.6231, through the Wine bridge)
refuses a request whose `deviation` is a float - 20.0 from a JSON-saved setting
- or that carries numpy numbers, and it refuses by returning None. The order
manager logged that as "MT5 returned None (connection lost?)" and every live
signal failed with no reason anyone could act on (2026-10-26).
"""

import asyncio
import collections

import numpy as np

from backend.mt5 import order_manager as om

Tick = collections.namedtuple("Tick", "bid ask")
SymInfo = collections.namedtuple(
    "SymInfo", "digits trade_tick_size volume_step volume_max volume_min trade_stops_level point filling_mode")
Term = collections.namedtuple("Term", "connected")


class StrictMT5:
    """Accepts a request only with the exact types the package wants."""
    TRADE_ACTION_DEAL, ORDER_TYPE_BUY, ORDER_TYPE_SELL = 1, 0, 1
    ORDER_TIME_GTC, ORDER_FILLING_FOK, ORDER_FILLING_IOC = 0, 0, 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self, refuse=False):
        self.refuse, self.sent = refuse, None

    def terminal_info(self): return Term(True)
    def symbol_info_tick(self, s): return Tick(2000.0, 2000.5)
    def symbol_info(self, s): return SymInfo(2, 0.01, 0.01, 100.0, 0.01, 0, 0.01, 1)
    def last_error(self): return (-2, 'Invalid "deviation" argument')

    def order_send(self, req):
        self.sent = req
        if self.refuse:
            return None
        types = {"action": int, "volume": float, "type": int, "price": float, "sl": float, "tp": float,
                 "deviation": int, "magic": int, "comment": str, "type_time": int, "type_filling": int}
        for k, t in types.items():
            if type(req[k]) is not t:
                return None
        Result = collections.namedtuple("Result", "retcode order price volume comment")
        return Result(10009, 777, req["price"], req["volume"], "done")


def _place(fake, monkeypatch, **kw):
    monkeypatch.setattr(om, "mt5", fake)
    args = dict(symbol="XAUUSD", direction="BUY", volume=np.float64(0.07), sl=np.float64(1990.0),
                tp=np.float64(2030.0), magic=np.int64(1011), comment="AE_TP1_016959f2",
                deviation_points=20.0)
    args.update(kw)
    return asyncio.run(om.OrderManager.place_market_order(**args))


def test_numpy_numbers_and_a_float_deviation_are_sent_as_exact_types(monkeypatch):
    fake = StrictMT5()
    res = _place(fake, monkeypatch)
    assert res.get("success"), res
    assert type(fake.sent["deviation"]) is int and fake.sent["deviation"] == 20
    assert type(fake.sent["magic"]) is int and type(fake.sent["volume"]) is float


def test_a_refused_request_reports_mt5s_reason(monkeypatch):
    res = _place(StrictMT5(refuse=True), monkeypatch)
    assert not res["success"]
    assert "Invalid" in res["error"] and "deviation" in res["error"]


def test_a_close_only_symbol_is_refused_before_anything_is_sent(monkeypatch):
    fake = StrictMT5()
    Info = collections.namedtuple("SymInfo", SymInfo._fields + ("trade_mode",))
    fake.symbol_info = lambda s: Info(2, 0.01, 0.01, 100.0, 0.01, 0, 0.01, 1, 3)
    res = _place(fake, monkeypatch, symbol="Vol over Crash 550")
    assert not res["success"] and "CLOSE-ONLY" in res["error"] and res.get("permanent")
    assert fake.sent is None
