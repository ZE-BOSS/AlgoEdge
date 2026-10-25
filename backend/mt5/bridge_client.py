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

    # -- the MetaTrader5 surface ---------------------------------------------------
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
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
