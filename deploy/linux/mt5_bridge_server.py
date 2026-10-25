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
        return ("ok", encode(fn(*args, **(kwargs or {}))))
    return ("err", f"unknown request {kind!r}")


def serve(host: str, port: int, key: bytes) -> None:
    listener = Listener((host, port), authkey=key)
    log.info("listening on %s:%s (MetaTrader5 %s)", host, port, getattr(mt5, "__version__", "?"))
    while True:
        try:
            conn = listener.accept()
        except Exception as exc:                        # noqa: BLE001 - a bad handshake must not stop the server
            log.warning("rejected a connection: %s", exc)
            continue
        log.info("client connected")
        try:
            while True:
                try:
                    msg = conn.recv()
                except (EOFError, OSError):
                    break
                try:
                    reply = handle(msg)
                except Exception as exc:                # noqa: BLE001 - reported to the caller
                    reply = ("err", f"{type(exc).__name__}: {exc}")
                conn.send(reply)
        finally:
            conn.close()
            log.info("client disconnected")


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
