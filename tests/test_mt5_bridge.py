"""
The Linux MT5 bridge (backend/mt5/bridge_client.py <-> deploy/linux/mt5_bridge_server.py),
tested against a FAKE MetaTrader5 so it runs anywhere.

The case that matters most for real money: an order_send whose reply is lost
must not be sent a second time (it may have executed).
"""

from __future__ import annotations

import collections
import importlib.util
import socket
import sys
import threading
import time
import types
from multiprocessing.connection import Listener
from pathlib import Path

import numpy as np
import pytest

from backend.mt5.bridge_client import BRIDGE_ERROR, BridgedMT5

KEY = b"test-bridge-key"
Account = collections.namedtuple("AccountInfo", ["login", "balance", "server"])


def _fake_mt5() -> types.ModuleType:
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "fake"
    m.ORDER_TYPE_BUY = 0
    m.TIMEFRAME_M1 = 1
    m.calls = []
    m.initialize = lambda path=None: m.calls.append(("initialize", path)) or True
    m.account_info = lambda: Account(123, 1000.5, "Demo-Server")
    m.positions_get = lambda **kw: ()
    m.last_error = lambda: (1, "Success")
    m.copy_rates_from_pos = lambda s, tf, a, n: np.array(
        [(1_700_000_000 + 60 * i, 1.0 + i, 2.0, 0.5, 1.5, 10) for i in range(n)],
        dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
               ("close", "<f8"), ("tick_volume", "<u8")])

    def order_send(req):
        m.calls.append(("order_send", req))
        return collections.namedtuple("OrderSendResult", ["retcode", "order"])(10009, 77)
    m.order_send = order_send
    return m


def _load_server(fake):
    sys.modules["MetaTrader5"] = fake
    try:
        spec = importlib.util.spec_from_file_location(
            "mt5_bridge_server_under_test", Path("deploy/linux/mt5_bridge_server.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        del sys.modules["MetaTrader5"]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def bridge():
    fake = _fake_mt5()
    server = _load_server(fake)
    port = _free_port()
    t = threading.Thread(target=server.serve, args=("127.0.0.1", port, KEY), daemon=True)
    t.start()
    time.sleep(0.3)
    client = BridgedMT5(("127.0.0.1", port), KEY, timeout_s=5)
    yield client, fake
    client._drop("test over")


def test_results_come_back_in_the_real_packages_shapes(bridge):
    mt5, fake = bridge
    assert mt5.initialize(path=r"C:\MT5\demo\terminal64.exe") is True
    assert fake.calls[-1] == ("initialize", r"C:\MT5\demo\terminal64.exe")
    a = mt5.account_info()
    assert (a.login, a.balance, a.server) == (123, 1000.5, "Demo-Server")
    assert a._asdict()["balance"] == 1000.5
    r = mt5.copy_rates_from_pos("X", mt5.TIMEFRAME_M1, 0, 500)
    assert isinstance(r, np.ndarray) and r.dtype.names[0] == "time" and len(r) == 500
    assert r["open"][3] == 4.0
    assert mt5.positions_get(symbol="X") == ()
    assert mt5.ORDER_TYPE_BUY == 0                      # constants from the shipped table


def test_a_dead_bridge_looks_like_a_disconnected_terminal():
    mt5 = BridgedMT5(("127.0.0.1", _free_port()), KEY, timeout_s=1)
    assert mt5.initialize() is False
    assert mt5.account_info() is None
    code, msg = mt5.last_error()
    assert code == BRIDGE_ERROR and "unreachable" in msg


def test_order_send_is_never_resent_after_a_lost_reply():
    """The server receives the order and dies before replying: the client must
    report failure, not reconnect and send the order again."""
    port = _free_port()
    received = []

    def one_shot_then_die():
        lst = Listener(("127.0.0.1", port), authkey=KEY)
        for _ in range(2):                              # accept a reconnect too, to catch a resend
            try:
                conn = lst.accept()
            except Exception:                           # noqa: BLE001
                return
            try:
                msg = conn.recv()
            except EOFError:
                continue
            received.append(msg)
            conn.close()                                # no reply: the reply is "lost"

    threading.Thread(target=one_shot_then_die, daemon=True).start()
    time.sleep(0.3)
    mt5 = BridgedMT5(("127.0.0.1", port), KEY, timeout_s=2)
    assert mt5.order_send({"action": 1, "symbol": "X", "volume": 0.1}) is None
    assert [m[1] for m in received if m[0] == "call"] == ["order_send"], "order_send was resent"


def test_reads_reconnect_after_a_bridge_restart(bridge):
    mt5, _ = bridge
    assert mt5.account_info().login == 123
    mt5._drop("simulated broken pipe")                  # the connection died
    assert mt5.account_info().login == 123              # next call reconnects


def test_dotenv_fills_the_key_file_when_only_the_address_is_in_the_environment(tmp_path, monkeypatch):
    """systemd exports the bridge address, the key file is named only in .env:
    the key file must still be read, or `import backend` fails on 'no bridge key'."""
    import backend
    (tmp_path / ".env").write_text("ALGOEDGE_MT5_BRIDGE=127.0.0.1:1\nALGOEDGE_MT5_BRIDGE_KEY_FILE=/k/bridge.key\n",
                                   encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALGOEDGE_MT5_BRIDGE", "127.0.0.1:18812")
    monkeypatch.delenv("ALGOEDGE_MT5_BRIDGE_KEY_FILE", raising=False)
    backend._bridge_setting_from_dotenv()
    import os
    assert os.environ["ALGOEDGE_MT5_BRIDGE"] == "127.0.0.1:18812"      # the environment wins
    assert os.environ["ALGOEDGE_MT5_BRIDGE_KEY_FILE"] == "/k/bridge.key"


def test_on_the_bridge_the_broker_attaches_without_logging_in(monkeypatch):
    """Linux VPS: the terminal is logged in by hand (VNC). The broker must attach
    and read the account - never mt5.login() with .env credentials, which could
    move a working terminal onto a stale account. Startup used to skip connecting
    on Linux entirely, so every trade was refused 'broker not connected'."""
    import asyncio
    from backend.brokers import mt5_broker as mb
    from backend.config import settings

    calls = []
    acct = collections.namedtuple("AccountInfo", "balance equity margin free_margin leverage login")

    class FakeBridge:
        def bridge_ping(self): return True
        def initialize(self, path=None, **k): calls.append(("initialize", path)); return True
        def login(self, *a, **k): calls.append(("login",)); return True
        def account_info(self): return acct(1000.0, 1000.0, 0.0, 1000.0, 100, 32283616)

    monkeypatch.setattr(mb, "mt5", FakeBridge())
    monkeypatch.setattr(settings.mt5, "account", 999, raising=False)
    monkeypatch.setattr(settings.mt5, "password", "stale", raising=False)
    monkeypatch.setattr(settings.mt5, "server", "Somewhere", raising=False)
    broker = mb.MT5Broker()
    monkeypatch.setattr(broker, "_reconnect_loop", lambda: asyncio.sleep(0))

    async def go():
        ok = await broker.ensure_connected()
        assert ok and broker.connected and broker.account_info.balance == 1000.0
        assert await broker.ensure_connected()            # already connected: no second initialize
    asyncio.run(go())
    assert ("login",) not in calls
    assert [c for c in calls if c[0] == "initialize"].__len__() == 1


def test_startup_connects_the_broker_on_the_linux_bridge_too():
    import inspect
    import backend.main as m
    src = inspect.getsource(m)
    assert 'os.name == "nt" or os.environ.get("ALGOEDGE_MT5_BRIDGE")' in src
    assert "ensure_connected()" in src


def test_order_send_falls_back_to_the_keyword_form_newer_mt5_builds_demand():
    """MetaTrader5 5.0.6231 (the VPS) refuses order_send(request) with
    (-2, 'Unnamed arguments not allowed') and returns None - every live order
    failed on 2026-10-26. The bridge retries by keyword, once, and only on that
    pre-send refusal, so nothing can be placed twice."""
    fake = _fake_mt5()
    sent = []

    def order_send(*args, **kwargs):
        if args:                                        # the newer build: positional refused
            fake.last_error = lambda: (-2, "Unnamed arguments not allowed")
            return None
        sent.append(kwargs["request"])
        return collections.namedtuple("OrderSendResult", ["retcode", "order"])(10009, 88)
    fake.order_send = order_send
    server = _load_server(fake)
    req = {"action": 1, "symbol": "Boom 900 Index", "volume": 0.2}
    status, value = server.handle(("call", "order_send", (req,), {}))
    assert status == "ok" and value["values"] == [10009, 88]
    assert sent == [req]                                # exactly one order reached the broker


def test_a_real_order_failure_is_not_retried():
    fake = _fake_mt5()
    calls = []
    fake.order_send = lambda *a, **k: calls.append((a, k)) or None
    fake.last_error = lambda: (-10004, "No IPC connection")
    server = _load_server(fake)
    status, value = server.handle(("call", "order_send", ({"action": 1},), {}))
    assert status == "ok" and value is None and len(calls) == 1
