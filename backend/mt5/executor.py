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
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# One worker, process-wide. Every MT5 call in the backend goes through this.
mt5_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")


async def run_mt5(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Await `fn(*args, **kwargs)` on the single MT5 thread."""
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(mt5_executor, lambda: fn(*args, **kwargs))
    return await loop.run_in_executor(mt5_executor, fn, *args)


def run_mt5_sync(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Blocking variant, for code that is not async.

    Still goes through the one MT5 thread, so a synchronous caller cannot bypass
    the serialisation and race an async one.
    """
    return mt5_executor.submit(fn, *args, **kwargs).result()
