"""
backend/mt5/bridge_client.py

`MetaTrader5`, over a bridge - for running the backend on LINUX.

The MetaTrader5 Python package exists only for Windows. On a Linux server the
MT5 terminal runs under Wine next to a small server
(deploy/linux/mt5_bridge_server.py) that executes MetaTrader5 calls in a
Windows Python. This module is the other end: a stand-in for the package that
forwards every function call to that server and rebuilds the results - named
tuples with the same fields, numpy arrays with the same dtype - so the rest of
the backend runs unchanged.

It is installed as `sys.modules["MetaTrader5"]` by backend/__init__.py when
ALGOEDGE_MT5_BRIDGE=host:port is set, BEFORE anything imports MetaTrader5. The
43 modules that `import MetaTrader5 as mt5` then get this object.

Failure behaviour copies the real package's: when the bridge is unreachable a
call returns None (initialize/login/symbol_select return False) and
`last_error()` explains, so the backend's existing "terminal not connected"
handling applies. `order_send` is NEVER retried after it has been sent: if the
reply is lost the order may have executed, and a resend could double it.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import sys
import threading
import time
import types
from multiprocessing.connection import Client
from pathlib import Path
from typing import Any

logger = logging.getLogger("backend.mt5.bridge")

BRIDGE_ERROR = -10_003          # outside MT5's own codes (which start at -10_000)
_CONSTANTS_FILE = Path(__file__).with_name("mt5_constants.json")
_FALSE_ON_FAILURE = {"initialize", "login", "symbol_select", "market_book_add", "market_book_release", "shutdown"}
_NEVER_RESEND = {"order_send"}
_NT_CACHE: dict[tuple, type] = {}

# Brokers name the same instrument differently: Exness "XAUUSDm", others
# "XAUUSD.m", "XAUUSD.pro", "XAUUSDc". The backend asks for "XAUUSD" in many
# places (dashboard chart, ticks, the bot's orders). When the name it asks for
# does not exist at this broker, the call is sent with the broker's name.
_SYMBOL_ARG0 = {"symbol_info", "symbol_info_tick", "symbol_select", "copy_rates_from", "copy_rates_from_pos",
                "copy_rates_range", "copy_ticks_from", "copy_ticks_range", "market_book_add", "market_book_get",
                "market_book_release"}
_SYMBOL_ARG1 = {"order_calc_margin", "order_calc_profit"}            # (action, symbol, ...)
_SYMBOL_REQUEST = {"order_send", "order_check"}                       # request["symbol"]
_SYMBOL_KW = {"positions_get", "orders_get", "positions_total", "orders_total"}
_RESET_SYMBOLS = {"initialize", "login", "shutdown"}
_SUFFIX_RE = None


def _suffix_ok(rest: str) -> bool:
    """What a broker adds to a name: "m", ".m", ".pro", "_i", "c", "-ecn"."""
    global _SUFFIX_RE
    if _SUFFIX_RE is None:
        import re
        _SUFFIX_RE = re.compile(r"^[._#-]?[A-Za-z0-9]{1,4}$")
    return bool(_SUFFIX_RE.match(rest))


def pick_broker_symbol(wanted: str, names: list[str], suffix: str = "") -> str | None:
    """The broker's name for `wanted` among `names` (it is known not to exist as
    spelled), or None. A configured suffix wins; otherwise the one name that is
    `wanted` plus a short suffix, preferring the shortest."""
    if not wanted:
        return None
    up = wanted.upper()
    by_up = {n.upper(): n for n in names}
    if suffix and (up + suffix.upper()) in by_up:
        return by_up[up + suffix.upper()]
    hits = [n for n in names if n.upper().startswith(up) and n.upper() != up and _suffix_ok(n[len(wanted):])]
    if not hits:
        return None
    hits.sort(key=lambda n: (len(n), n))
    if len(hits) > 1 and len(hits[0]) == len(hits[1]):
        logger.warning("[MT5 bridge] %s is ambiguous at this broker (%s) - using %s", wanted, ", ".join(hits[:4]), hits[0])
    return hits[0]


def _decode(v: Any) -> Any:
    if isinstance(v, dict):
        if "__nt__" in v and "fields" in v:
            key = (v["__nt__"], tuple(v["fields"]))
            cls = _NT_CACHE.get(key)
            if cls is None:
                cls = _NT_CACHE[key] = collections.namedtuple(v["__nt__"], v["fields"], rename=True)
            return cls(*(_decode(x) for x in v["values"]))
        if "__np__" in v and "data" in v:
            import numpy as np
            descr = v["__np__"]
            dtype = np.dtype([tuple(d) for d in descr] if isinstance(descr, list) else descr)
            return np.frombuffer(v["data"], dtype=dtype).reshape(v["shape"]).copy()
        return {k: _decode(x) for k, x in v.items()}
    if isinstance(v, tuple):
        return tuple(_decode(x) for x in v)
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


class BridgedMT5(types.ModuleType):
    """A module object: constants are attributes, functions are forwarded."""

    def __init__(self, address: tuple[str, int], key: bytes, timeout_s: float = 300.0):
        super().__init__("MetaTrader5", "MetaTrader5 over the AlgoEdge Wine bridge")
        self.__dict__["_address"] = address
        self.__dict__["_key"] = key
        self.__dict__["_timeout"] = float(timeout_s)
        self.__dict__["_conn"] = None
        self.__dict__["_lock"] = threading.Lock()
        self.__dict__["_last_bridge_error"] = None
        self.__dict__["_next_attempt"] = 0.0
        self.__dict__["_sym_map"] = {}          # name asked for -> broker's name (only when they differ)
        self.__dict__["_sym_ok"] = set()        # names that exist as spelled
        self.__dict__["_sym_names"] = (0.0, [])
        data = json.loads(_CONSTANTS_FILE.read_text(encoding="utf-8"))
        self.__dict__.update(data.get("constants", {}))
        self.__dict__["__version__"] = data.get("version", "") + "+bridge"
        self.__dict__["__file__"] = __file__

    # -- connection ------------------------------------------------------------
    def _connect(self):
        conn = self.__dict__["_conn"]
        if conn is not None:
            return conn
        now = time.monotonic()
        if now < self.__dict__["_next_attempt"]:       # don't hammer a dead bridge on every call
            raise ConnectionError(self.__dict__["_last_bridge_error"] or "MT5 bridge unavailable")
        try:
            conn = Client(self.__dict__["_address"], authkey=self.__dict__["_key"])
        except Exception as exc:
            self.__dict__["_next_attempt"] = now + 2.0
            raise ConnectionError(f"MT5 bridge {self.__dict__['_address']} unreachable: {exc}") from exc
        self.__dict__["_conn"] = conn
        self.__dict__["_last_bridge_error"] = None
        return conn

    def _drop(self, why: str) -> None:
        conn = self.__dict__["_conn"]
        self.__dict__["_conn"] = None
        self.__dict__["_last_bridge_error"] = why
        if conn is not None:
            try:
                conn.close()
            except Exception:                           # noqa: BLE001
                pass

    def _request(self, msg: tuple, *, resend: bool = True):
        with self.__dict__["_lock"]:
            for attempt in (1, 2):
                sent = False
                try:
                    conn = self._connect()
                    conn.send(msg)
                    sent = True
                    if not conn.poll(self.__dict__["_timeout"]):
                        raise TimeoutError(f"no reply in {self.__dict__['_timeout']:.0f} s")
                    status, value = conn.recv()
                except Exception as exc:                # noqa: BLE001 - every transport failure lands here
                    # A ConnectionError from _connect already says what failed;
                    # wrapping it again nested the message on every call.
                    self._drop(str(exc) if isinstance(exc, ConnectionError) and str(exc).startswith("MT5 bridge")
                               else f"MT5 bridge error: {type(exc).__name__}: {exc}")
                    if attempt == 2 or (sent and not resend):
                        raise ConnectionError(self.__dict__["_last_bridge_error"]) from exc
                    continue
                if status == "ok":
                    return _decode(value)
                raise RuntimeError(value)
        raise ConnectionError(self.__dict__["_last_bridge_error"] or "MT5 bridge unavailable")

    # -- broker symbol names --------------------------------------------------------
    def _raw(self, name: str, *args, **kwargs):
        try:
            return self._request(("call", name, args, kwargs))
        except (ConnectionError, RuntimeError):
            return None

    def broker_symbol(self, wanted: str) -> str:
        """`wanted` as this broker spells it ("XAUUSD" -> "XAUUSDm" on Exness).
        Unchanged when it exists as spelled or nothing matches."""
        if not isinstance(wanted, str) or not wanted:
            return wanted
        m = self.__dict__["_sym_map"]
        if wanted in m:
            return m[wanted]
        if wanted in self.__dict__["_sym_ok"]:
            return wanted
        if self._raw("symbol_info", wanted) is not None:
            self.__dict__["_sym_ok"].add(wanted)
            return wanted
        at, names = self.__dict__["_sym_names"]
        if not names or time.monotonic() - at > 600:
            res = self._raw("symbols_get")
            if not res:
                return wanted                       # terminal not answering - try again next call
            names = [getattr(s, "name", "") for s in res]
            self.__dict__["_sym_names"] = (time.monotonic(), names)
        got = pick_broker_symbol(wanted, names, os.environ.get("MT5_SYMBOL_SUFFIX", ""))
        if got:
            logger.info("[MT5 bridge] %s is called %s at this broker - using that", wanted, got)
            m[wanted] = got
            return got
        return wanted

    def _with_broker_symbols(self, name: str, args: tuple, kwargs: dict):
        try:
            if name in _SYMBOL_ARG0 and args:
                args = (self.broker_symbol(args[0]),) + tuple(args[1:])
            elif name in _SYMBOL_ARG1 and len(args) > 1:
                args = (args[0], self.broker_symbol(args[1])) + tuple(args[2:])
            elif name in _SYMBOL_REQUEST and args and isinstance(args[0], dict) and args[0].get("symbol"):
                req = dict(args[0])
                req["symbol"] = self.broker_symbol(req["symbol"])
                args = (req,) + tuple(args[1:])
            if name in _SYMBOL_KW and kwargs.get("symbol"):
                kwargs = {**kwargs, "symbol": self.broker_symbol(kwargs["symbol"])}
            if name in _SYMBOL_ARG0 and kwargs.get("symbol"):
                kwargs = {**kwargs, "symbol": self.broker_symbol(kwargs["symbol"])}
        except Exception as exc:                        # noqa: BLE001 - never block the call itself
            logger.warning("[MT5 bridge] symbol name lookup failed for %s(): %s", name, exc)
        return args, kwargs

    # -- the MetaTrader5 surface ---------------------------------------------------
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            if name in _RESET_SYMBOLS:                  # another account / broker: names may differ
                self.__dict__["_sym_map"].clear()
                self.__dict__["_sym_ok"].clear()
                self.__dict__["_sym_names"] = (0.0, [])
            else:
                args, kwargs = self._with_broker_symbols(name, args, kwargs)
            try:
                return self._request(("call", name, args, kwargs), resend=name not in _NEVER_RESEND)
            except ConnectionError as exc:
                logger.warning("[MT5 bridge] %s(): %s", name, exc)
                return False if name in _FALSE_ON_FAILURE else None
            except RuntimeError as exc:                 # the server ran it and it raised
                logger.warning("[MT5 bridge] %s() raised on the Windows side: %s", name, exc)
                return False if name in _FALSE_ON_FAILURE else None

        call.__name__ = name
        if name == "last_error":
            def last_error():
                err = self.__dict__["_last_bridge_error"]
                if err and self.__dict__["_conn"] is None:
                    return (BRIDGE_ERROR, err)
                res = call()
                return res if res is not None else (BRIDGE_ERROR, self.__dict__["_last_bridge_error"] or "bridge")
            return last_error
        return call

    def bridge_ping(self) -> bool:
        try:
            return self._request(("ping",)) == "pong"
        except Exception:                               # noqa: BLE001
            return False


def install(spec: str | None = None) -> BridgedMT5:
    """Install the bridge as `MetaTrader5`. `spec` is host:port; the key comes
    from ALGOEDGE_MT5_BRIDGE_KEY or the file named by ALGOEDGE_MT5_BRIDGE_KEY_FILE."""
    spec = spec or os.environ.get("ALGOEDGE_MT5_BRIDGE", "")
    host, _, port = spec.rpartition(":")
    key = os.environ.get("ALGOEDGE_MT5_BRIDGE_KEY", "").encode()
    kf = os.environ.get("ALGOEDGE_MT5_BRIDGE_KEY_FILE", "")
    if not key and kf:
        key = Path(kf).read_bytes().strip()
    if not key:
        raise RuntimeError("ALGOEDGE_MT5_BRIDGE is set but no bridge key "
                           "(ALGOEDGE_MT5_BRIDGE_KEY or ALGOEDGE_MT5_BRIDGE_KEY_FILE)")
    mod = BridgedMT5((host or "127.0.0.1", int(port)), key,
                     float(os.environ.get("ALGOEDGE_MT5_BRIDGE_TIMEOUT", "300")))
    sys.modules["MetaTrader5"] = mod
    return mod
