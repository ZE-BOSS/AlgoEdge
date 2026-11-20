"""The single thread every MetaTrader5 call must run on.

WHY THIS EXISTS
---------------
The MetaTrader5 Python package is a thin wrapper over the terminal's IPC channel
and it is **not thread-safe**. Its connection state is bound to the thread that
called `initialize()`, and concurrent or cross-thread calls corrupt it. The
failure is not a clean exception — a C function returns a value while an
exception is still set on the thread state, and CPython reports:

    <built-in function history_deals_get> returned a result with an exception set

which is what the backend was logging on every dashboard poll.

Before this module the backend called MT5 from **five** different places:

    asyncio default executor (run_in_executor(None, ...))   many threads, 22 sites
    brokers/mt5_broker.py    ThreadPoolExecutor(max_workers=4)   20 sites
    mt5/order_manager.py     ThreadPoolExecutor(max_workers=4)   10 sites
    mt5/data_fetcher.py      ThreadPoolExecutor(max_workers=1)    8 sites
    data/orderflow.py        ThreadPoolExecutor(max_workers=1)

`initialize()` ran on a default-pool thread while `history_deals_get()` ran on an
order_manager thread, so the second call was made from a thread the terminal
connection did not belong to. The two 4-worker pools additionally raced against
themselves.

USE
---
    from backend.mt5.executor import mt5_executor, run_mt5

    deals = await run_mt5(mt5.history_deals_get, start, end)
    # or, where a lambda reads better:
    deals = await loop.run_in_executor(mt5_executor, lambda: mt5.history_deals_get(a, b))

`max_workers=1` is the whole point: it serialises every call onto one thread, so
the terminal only ever sees the thread it was initialised on. Do not raise it,
and do not add a second executor for MT5 work.
"""
from __future__ import annotations

import asyncio
import itertools
import queue
import threading
from concurrent.futures import Executor, Future
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# One worker, process-wide. Every MT5 call in the backend goes through this.
# ── priority ────────────────────────────────────────────────────────────────
# Still ONE thread (see above), but no longer first-come-first-served: a trade
# waiting behind a 5,000-bar history download, a backtest's tick download or a
# dashboard's deal history was the main source of order latency. Calls are
# served by class, oldest first within a class:
#   0  trading and the facts a trade decision needs right now
#   1  everything else
#   2  bulk history (bars, ticks, deal/order history, the full symbol list)
HIGH, NORMAL, LOW = 0, 1, 2
_HIGH = {"order_send", "order_check", "positions_get", "orders_get", "positions_total", "orders_total",
         "symbol_info_tick", "account_info", "order_calc_margin", "order_calc_profit"}
_LOW = {"copy_rates_from", "copy_rates_from_pos", "copy_rates_range", "copy_ticks_from", "copy_ticks_range",
        "history_deals_get", "history_orders_get", "history_deals_total", "history_orders_total", "symbols_get"}


def priority_of(fn: Callable[..., Any]) -> int:
    """HIGH / NORMAL / LOW from the MT5 function a callable reaches: its own name
    (bridged and real MT5 functions keep theirs), or the names a lambda uses
    (`lambda: mt5.history_deals_get(a, b)`)."""
    names = {getattr(fn, "__name__", "")}
    inner = getattr(fn, "func", None)                     # functools.partial
    if inner is not None:
        names.add(getattr(inner, "__name__", ""))
    code = getattr(fn, "__code__", None)
    if code is not None:
        names |= set(code.co_names)
    if names & _HIGH:
        return HIGH
    if names & _LOW:
        return LOW
    return NORMAL


class PriorityMT5Executor(Executor):
    """A single worker thread serving a priority queue. Drop-in for the old
    ThreadPoolExecutor(max_workers=1): `loop.run_in_executor(mt5_executor, ...)`
    keeps working and gets its priority from the function it runs."""

    def __init__(self, name: str = "mt5"):
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._name = name
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._shutdown = False
        self.served = [0, 0, 0]

    def submit(self, fn, /, *args, **kwargs) -> Future:
        return self.submit_with_priority(priority_of(fn), fn, *args, **kwargs)

    def submit_with_priority(self, prio: int, fn, /, *args, **kwargs) -> Future:
        if self._shutdown:
            raise RuntimeError("cannot schedule new futures after shutdown")
        f: Future = Future()
        self._q.put((int(prio), next(self._seq), f, fn, args, kwargs))
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name=f"{self._name}_0", daemon=True)
                self._thread.start()
        return f

    def _work(self) -> None:
        while True:
            prio, _, f, fn, args, kwargs = self._q.get()
            if fn is None:
                return
            if not f.set_running_or_notify_cancel():
                continue
            try:
                f.set_result(fn(*args, **kwargs))
            except BaseException as e:  # noqa: BLE001 - handed to the awaiting caller
                f.set_exception(e)
            self.served[min(prio, 2)] += 1

    def queued(self) -> int:
        return self._q.qsize()

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self._shutdown = True
        self._q.put((99, next(self._seq), Future(), None, (), {}))
        if wait and self._thread is not None:
            self._thread.join(timeout=10)


mt5_executor = PriorityMT5Executor("mt5")


async def run_mt5(fn: Callable[..., T], *args: Any, priority: int | None = None, **kwargs: Any) -> T:
    """Await `fn(*args, **kwargs)` on the single MT5 thread. The priority comes
    from `fn` unless given."""
    prio = priority_of(fn) if priority is None else priority
    return await asyncio.wrap_future(mt5_executor.submit_with_priority(prio, fn, *args, **kwargs))


# ── Which terminal this process belongs to ───────────────────────────────────
# Two backends on one machine (demo + live, each with its own MT5 terminal)
# must never cross over. A bare `mt5.initialize()` attaches to WHATEVER
# terminal the package finds first, so every reconnect - after the terminal
# drops its IPC link - used to be a coin flip between the demo account and the
# live one. Every (re)initialise goes through `reinitialize()`, which reuses
# the path this process connected with, else MT5_PATH / DERIV_MT5_PATH.
_terminal_path = ""


def remember_terminal(path: str | None) -> None:
    """Record the terminal path a successful initialize() used."""
    global _terminal_path
    if path:
        _terminal_path = str(path)


def terminal_path() -> str:
    import os
    return _terminal_path or os.getenv("MT5_PATH", "") or os.getenv("DERIV_MT5_PATH", "")


def reinitialize(mt5: Any) -> bool:
    """`mt5.initialize()` pinned to this process's terminal. Call it on the MT5
    thread, like any other MT5 call."""
    path = terminal_path()
    return bool(mt5.initialize(path=path) if path else mt5.initialize())


def run_mt5_sync(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Blocking variant, for code that is not async.

    Still goes through the one MT5 thread, so a synchronous caller cannot bypass
    the serialisation and race an async one.
    """
    return mt5_executor.submit(fn, *args, **kwargs).result()
