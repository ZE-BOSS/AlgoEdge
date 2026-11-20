"""The single MT5 thread serves trading calls before bulk history downloads."""
import asyncio
import threading
import time

from backend.mt5 import executor as X


class FakeMT5:
    def __init__(self):
        self.order = []

    def _f(self, name, delay=0.0):
        def fn(*a, **k):
            time.sleep(delay)
            self.order.append(name)
            return name
        fn.__name__ = name
        return fn


def test_priority_classes():
    m = FakeMT5()
    assert X.priority_of(m._f("order_send")) == X.HIGH
    assert X.priority_of(m._f("copy_rates_range")) == X.LOW
    assert X.priority_of(m._f("symbol_info")) == X.NORMAL

    class M:
        def history_deals_get(self, *a):
            return a
    mt5 = M()
    assert X.priority_of(lambda: mt5.history_deals_get(1, 2)) == X.LOW       # names a lambda uses


def test_order_send_overtakes_queued_downloads():
    ex = X.PriorityMT5Executor("t")
    m = FakeMT5()
    gate = threading.Event()
    ex.submit(lambda: gate.wait(2))                       # occupy the worker
    futs = [ex.submit(m._f(f"copy_ticks_range", 0.01)) for _ in range(5)]
    hi = ex.submit(m._f("order_send"))
    gate.set()
    hi.result(2)
    for f in futs:
        f.result(2)
    assert m.order[0] == "order_send"
    ex.shutdown()


def test_same_thread_for_every_call_and_errors_propagate():
    ex = X.PriorityMT5Executor("t2")
    names = {ex.submit(lambda: threading.current_thread().name).result(2) for _ in range(5)}
    assert len(names) == 1

    def boom():
        raise ValueError("x")
    try:
        ex.submit(boom).result(2)
        raise AssertionError("no error")
    except ValueError:
        pass
    ex.shutdown()


def test_run_in_executor_and_run_mt5_still_work():
    async def go():
        loop = asyncio.get_running_loop()
        a = await loop.run_in_executor(X.mt5_executor, lambda: 41 + 1)
        b = await X.run_mt5(lambda x, y=0: x + y, 40, y=2)
        return a, b
    assert asyncio.run(go()) == (42, 42)
