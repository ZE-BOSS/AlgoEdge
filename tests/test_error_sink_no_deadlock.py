"""The global error sink must never freeze the process.

Found 2026-10-13: a test hung for 1.7 hours. The stack dump showed the main
thread waiting on loguru's handler lock while loguru's WRITER thread sat
inside `error_sink`, blocked putting onto the queue it alone drains. The sink
was logging (via `bot_service._log_event`) from inside the logging pipeline,
and a burst of errors filled the pipe.

The same sink also sent Telegram alerts SYNCHRONOUSLY on that thread, because
the writer thread has no event loop and the fallback was `asyncio.run`.

WHY A SUBPROCESS
A deadlock cannot be asserted in-process: if it happens, the writer thread and
the handler lock stay stuck, and every later `logger.*` call in the test run
blocks too - so the suite hangs instead of failing. A child process with a
timeout turns "hangs forever" into a clean, reported failure.

The original hang needed only 40 errors. These send thousands.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every alert is captured rather than sent: these tests must never reach
# api.telegram.org, and a real send is irrelevant to whether the sink blocks.
_BURST = textwrap.dedent("""
    import sys
    sys.path.insert(0, {root!r})
    import backend.utils.global_error_handler as geh
    sent = []
    geh._queue_alert = lambda m: sent.append(m) or True
    geh.setup_global_error_handler()
    from loguru import logger
    for i in range({n}):
        logger.error(f"burst error {{i}} - not found in mt5")
    logger.complete()
    print("DONE", len(sent))
""")


def _run(code: str, timeout: float) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "INVESTOR_JOBS": "off"}
    return subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_a_burst_of_errors_does_not_deadlock_the_logger():
    try:
        out = _run(_BURST.format(root=str(ROOT), n=3000), timeout=180)
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "3,000 logger.error calls did not finish in 180s. That is the "
            "error-sink deadlock: the sink is logging from inside loguru's "
            "writer thread again, or blocking it on network I/O.") from None
    assert out.returncode == 0, out.stderr[-2000:]
    assert "DONE" in out.stdout, (out.stdout[-500:], out.stderr[-2000:])


def test_the_sink_does_not_log_the_error_a_second_time():
    """The sink fires BECAUSE the error was already logged. Logging it again
    from in there is both the deadlock and the reason the VPS log showed every
    MT5 error twice."""
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        import backend.utils.global_error_handler as geh
        geh._queue_alert = lambda m: True
        geh.setup_global_error_handler()
        from loguru import logger
        seen = []
        logger.add(lambda m: seen.append(m.record["message"]), level="ERROR")
        logger.error("one error")
        logger.complete()
        import time; time.sleep(1.0)
        logger.complete()
        print("COUNT", sum(1 for s in seen if "one error" in s))
    """)
    out = _run(code, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("COUNT")), "")
    assert line == "COUNT 1", (
        f"expected the error logged exactly once, got {line!r}. A second copy "
        f"means the sink re-logged it from inside the writer thread.")


def test_an_alert_is_handed_off_rather_than_sent_inline():
    """The sink must never touch the network itself. It queues; one daemon
    thread owns the send."""
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        import backend.utils.global_error_handler as geh
        queued = []
        geh._queue_alert = lambda m: queued.append(m) or True
        geh.setup_global_error_handler()
        from loguru import logger
        logger.error("a real failure")
        logger.complete()
        import time; time.sleep(1.0)
        print("QUEUED", len(queued))
    """)
    out = _run(code, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "QUEUED 1" in out.stdout, out.stdout[-500:]


def test_a_full_alert_queue_drops_rather_than_blocks():
    """Losing a notification is acceptable. Stalling every log call in a
    process that places live orders is not.

    In its own process: in the shared test process another file may already
    have started the real sender thread, which would consume the queue (racing
    this test) and push the filler through the REAL Telegram send."""
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        import backend.utils.global_error_handler as geh
        # No consumer: pretend the sender is up so none is spawned.
        geh._alert_sender_started = True
        for _ in range(geh._ALERT_QUEUE_MAX):
            assert geh._queue_alert("filler") is True
        import time
        t0 = time.perf_counter()
        ok = geh._queue_alert("overflow")
        took = time.perf_counter() - t0
        print("RESULT", ok, geh._alerts_dropped, round(took, 4))
    """)
    out = _run(code, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), "")
    _, ok, dropped, took = line.split()
    assert ok == "False", line
    assert dropped == "1", line
    assert float(took) < 0.5, f"a full queue BLOCKED for {took}s instead of dropping"
