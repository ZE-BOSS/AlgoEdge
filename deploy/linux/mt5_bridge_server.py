"""
deploy/linux/mt5_bridge_server.py

The MetaTrader5 side of the Linux bridge. It runs under WINE with a Windows
Python next to the MT5 terminal, because the `MetaTrader5` package only exists
for Windows. The backend (normal Linux Python) talks to it through
`backend/mt5/bridge_client.py`, which installs itself as the `MetaTrader5`
module - so no backend code changes.

    wine python mt5_bridge_server.py --port 18812 --key-file C:\\algoedge\\bridge.key

Protocol (multiprocessing.connection: authenticated, length-prefixed pickle,
LOCALHOST only):
    ("call", name, args, kwargs) -> ("ok", value) | ("err", message)
    ("ping",)                    -> ("ok", "pong")
    ("constants",)               -> ("ok", {NAME: value})

Only plain data crosses the wire: MT5's named result tuples become
{"__nt__": type, "fields": [...], "values": [...]}, numpy arrays become
{"__np__": dtype descr, "shape": ..., "data": bytes}. Calls are executed one
at a time - the MetaTrader5 package is not thread-safe, and the backend
already serialises its calls on one thread.

Deliberately standalone: stdlib + MetaTrader5 + numpy only, so the Wine
Python needs nothing else installed.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from multiprocessing.connection import Listener

import MetaTrader5 as mt5
import numpy as np

log = logging.getLogger("mt5-bridge")


def encode(v):
    if isinstance(v, np.ndarray):
        a = np.ascontiguousarray(v)
        descr = a.dtype.descr if a.dtype.fields else a.dtype.str
        return {"__np__": descr, "shape": list(a.shape), "data": a.tobytes()}
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, tuple) and hasattr(v, "_asdict"):
        d = v._asdict()
        return {"__nt__": type(v).__name__, "fields": list(d.keys()), "values": [encode(x) for x in d.values()]}
    if isinstance(v, tuple):
        return tuple(encode(x) for x in v)
    if isinstance(v, list):
        return [encode(x) for x in v]
    if isinstance(v, dict):
        return {k: encode(x) for k, x in v.items()}
    return v


def constants() -> dict:
    return {k: getattr(mt5, k) for k in dir(mt5)
            if k.isupper() and isinstance(getattr(mt5, k), (int, float, str))}


# Functions whose newer MetaTrader5 builds (5.0.6231 here) refuse the request
# dict passed positionally - `order_send(request)`, the documented form and the
# one the backend uses - with last_error (-2, 'Unnamed arguments not allowed')
# and a None result. The refusal is argument parsing: nothing reached the
# broker, so retrying the same request by keyword cannot place an order twice.
_REQUEST_FUNCS = {"order_send", "order_check"}


def _unnamed_args_refused() -> bool:
    try:
        code, text = mt5.last_error()
    except Exception:                                   # noqa: BLE001
        return False
    return code == -2 and "unnamed" in str(text).lower()


TRADE_RETCODE_INVALID = 10013          # "Invalid request": rejected, nothing placed


def _not_placed(result) -> bool:
    """True only when this attempt certainly placed nothing: refused while
    parsing its arguments (None, last_error -2) or rejected as an invalid
    request (retcode 10013). Any other outcome is final - retrying it could
    place an order twice."""
    if result is None:
        try:
            return mt5.last_error()[0] == -2
        except Exception:                               # noqa: BLE001
            return False
    return getattr(result, "retcode", None) == TRADE_RETCODE_INVALID


# The documented parameter names of the READ functions the backend calls with
# positional arguments. The same builds refuse those positionally too - either
# None with (-2, 'Unnamed arguments not allowed') or, for history_deals_get,
# "SystemError: ... returned a result with an exception set" - which broke the
# closed-trade sync (journal P&L), the cash ledger and margin sizing. A read
# can be retried freely, so it is repeated by keyword; once a function has
# needed that, it is called by keyword from then on.
_PARAM_NAMES = {
    "history_deals_get": ("date_from", "date_to"),
    "history_orders_get": ("date_from", "date_to"),
    "history_deals_total": ("date_from", "date_to"),
    "history_orders_total": ("date_from", "date_to"),
    "order_calc_margin": ("action", "symbol", "volume", "price"),
    "order_calc_profit": ("action", "symbol", "volume", "price_open", "price_close"),
    "copy_rates_from": ("symbol", "timeframe", "date_from", "count"),
    "copy_rates_from_pos": ("symbol", "timeframe", "start_pos", "count"),
    "copy_rates_range": ("symbol", "timeframe", "date_from", "date_to"),
    "copy_ticks_from": ("symbol", "date_from", "count", "flags"),
    "copy_ticks_range": ("symbol", "date_from", "date_to", "flags"),
    "symbol_info": ("symbol",),
    "symbol_info_tick": ("symbol",),
    "symbol_select": ("symbol", "enable"),
    "market_book_add": ("symbol",),
    "market_book_get": ("symbol",),
    "market_book_release": ("symbol",),
}
_KEYWORD_ONLY: set[str] = set()


def _as_keywords(name, args, kwargs):
    names = _PARAM_NAMES[name]
    if len(args) > len(names):
        return None
    return {**dict(zip(names, args)), **kwargs}


def _call_read(name, fn, args, kwargs):
    if name in _KEYWORD_ONLY:
        named = _as_keywords(name, args, kwargs)
        if named is not None:
            return fn(**named)
    try:
        result = fn(*args, **kwargs)
        refused = result is None and _unnamed_args_refused()
    except (SystemError, TypeError) as exc:
        result, refused = None, exc
    if not refused:
        return result
    named = _as_keywords(name, args, kwargs)
    if named is None:
        if isinstance(refused, Exception):
            raise refused
        return result
    log.info("%s: positional arguments refused by this MetaTrader5 build (%s) - calling it by keyword from now on",
             name, refused if isinstance(refused, Exception) else mt5.last_error())
    _KEYWORD_ONLY.add(name)
    return fn(**named)


def _call(name, fn, args, kwargs):
    if name in _PARAM_NAMES and args:
        return _call_read(name, fn, args, kwargs)
    result = fn(*args, **kwargs)
    if not (result is None and name in _REQUEST_FUNCS and len(args) == 1 and not kwargs
            and isinstance(args[0], dict) and _unnamed_args_refused()):
        return result
    request = args[0]
    # This build wants the request's FIELDS as named arguments
    # (order_send(action=..., symbol=...)). `request=` is the other spelling a
    # build might take; it is tried second because on 5.0.6231 it is accepted
    # as one unknown field and the terminal answers "Invalid request".
    for label, attempt in (("fields as keywords", lambda: fn(**request)),
                           ("request=", lambda: fn(request=request))):
        log.info("%s: positional request refused by this MetaTrader5 build - retrying with %s", name, label)
        try:
            result = attempt()
        except TypeError as exc:                        # this build does not take that spelling at all
            log.info("%s with %s: %s", name, label, exc)
            continue
        if not _not_placed(result):
            return result
    return result


def handle(msg):
    kind = msg[0]
    if kind == "ping":
        return ("ok", "pong")
    if kind == "constants":
        return ("ok", constants())
    if kind == "call":
        _, name, args, kwargs = msg
        if name.startswith("_") or not hasattr(mt5, name):
            return ("err", f"MetaTrader5 has no function {name!r}")
        fn = getattr(mt5, name)
        return ("ok", encode(_call(name, fn, tuple(args or ()), dict(kwargs or {}))))
    return ("err", f"unknown request {kind!r}")


# One MetaTrader5 call at a time, whichever client it comes from: the package
# is not thread-safe, and a call and its last_error() must not interleave with
# another client's.
_MT5_LOCK = threading.Lock()


def _serve_client(conn, peer: int) -> None:
    log.info("client %d connected", peer)
    try:
        while True:
            try:
                msg = conn.recv()
            except (EOFError, OSError):
                break
            try:
                with _MT5_LOCK:
                    reply = handle(msg)
            except Exception as exc:                    # noqa: BLE001 - reported to the caller
                reply = ("err", f"{type(exc).__name__}: {exc}")
            conn.send(reply)
    finally:
        conn.close()
        log.info("client %d disconnected", peer)


def serve(host: str, port: int, key: bytes) -> None:
    """Every client on its own thread. It used to serve ONE connection until
    it closed - and the API keeps its connection open for good, so any second
    process (mt5_selftest, mt5_login, a worker) waited forever."""
    listener = Listener((host, port), authkey=key)
    log.info("listening on %s:%s (MetaTrader5 %s)", host, port, getattr(mt5, "__version__", "?"))
    peer = 0
    while True:
        try:
            conn = listener.accept()
        except Exception as exc:                        # noqa: BLE001 - a bad handshake must not stop the server
            log.warning("rejected a connection: %s", exc)
            continue
        peer += 1
        threading.Thread(target=_serve_client, args=(conn, peer), daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18812)
    ap.add_argument("--key-file", default=os.environ.get("ALGOEDGE_MT5_BRIDGE_KEY_FILE", ""))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    key = (open(a.key_file, "rb").read().strip() if a.key_file else
           os.environ.get("ALGOEDGE_MT5_BRIDGE_KEY", "").encode())
    if not key:
        raise SystemExit("no bridge key: pass --key-file or set ALGOEDGE_MT5_BRIDGE_KEY")
    # Attach to this prefix's terminal straight away. A bridge restarted on its
    # own (crash, update) was otherwise unattached until the backend happened to
    # call initialize(), and account_info() returned None meanwhile - which the
    # bot reads as "broker not connected" and refuses the trade.
    try:
        log.info("initialize(): %s %s", mt5.initialize(), mt5.last_error())
    except Exception as exc:                            # noqa: BLE001 - the backend still initialises on demand
        log.warning("initialize() at start failed: %s", exc)
    if a.host not in ("127.0.0.1", "localhost"):
        log.warning("listening on %s - the bridge is meant for localhost only", a.host)
    while True:
        try:
            serve(a.host, a.port, key)
        except Exception as exc:                        # noqa: BLE001 - e.g. port busy right after a restart
            log.error("server stopped: %s; restarting in 5 s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
