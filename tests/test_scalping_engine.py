"""Tick engine: fills at bid/ask, exits on the touching tick, account rules."""
import numpy as np

from backend.scalping import engine as E
from backend.scalping.ticks import Ticks, make_bars

T0 = 1_763_599_980_000          # on a 30 s boundary


def tk(mids, spread=0.2, step_ms=1000):
    m = np.asarray(mids, float)
    t = T0 + np.arange(len(m), dtype=np.int64) * step_ms
    return Ticks(t, m - spread / 2, m + spread / 2)


def test_buy_fills_at_ask_and_target_at_price():
    k = tk([100, 100, 101, 102, 103, 104, 105])
    f = E.simulate(E.Order(T0, +1, sl=98.0, tp=103.0), k, E.Costs(latency_ms=500))
    assert f.entry == 100.1                     # ask of the first tick after latency
    assert f.reason == "TP" and f.exit == 103.0
    assert round(f.r, 3) == round((103 - 100.1) / (100.1 - 98), 3)
    assert f.r < f.r_gross                       # spread paid


def test_stop_fills_at_the_bid_that_crossed_it():
    k = tk([100, 100, 99, 97.0, 96])
    f = E.simulate(E.Order(T0, +1, sl=98.0, tp=105.0), k, E.Costs(latency_ms=0))
    assert f.reason == "SL" and f.exit == 96.9    # gap through the stop: real slippage
    assert f.r < -1


def test_sell_mirrors():
    k = tk([100, 100, 99, 98, 97])
    f = E.simulate(E.Order(T0, -1, sl=101.0, tp=97.5), k, E.Costs(latency_ms=0))
    assert f.entry == 99.9 and f.reason == "TP" and f.exit == 97.5


def test_breakeven_then_back_to_entry():
    k = tk([100, 100, 101.5, 102.1, 101, 100.0, 99])
    o = E.Order(T0, +1, sl=98.0, tp=110.0, be_at=1.5, be_offset=0.0)
    f = E.simulate(o, k, E.Costs(latency_ms=0))
    assert f.reason == "breakeven" and abs(f.r) < 0.1


def test_trail_locks_profit():
    k = tk([100, 100, 101, 102, 103, 104, 103.4, 102.5, 100])
    o = E.Order(T0, +1, sl=98.0, trail_start=1.0, trail_dist=1.0)
    f = E.simulate(o, k, E.Costs(latency_ms=0))
    # best bid 103.9 -> trail stop 102.9; the next bid that crosses it is 102.4 (a gap): filled there
    assert f.reason == "trail" and f.exit == 102.4 and f.r > 1


def test_limit_fills_only_when_touched_and_expires():
    k = tk([100, 100.5, 101, 99.4, 99.0, 101])
    f = E.simulate(E.Order(T0, +1, sl=98.0, tp=101.0, kind="LIMIT", price=99.5, expire_s=60), k, E.Costs(latency_ms=0))
    assert f.entry == 99.5 and f.t_entry == T0 + 3000
    assert E.simulate(E.Order(T0, +1, sl=98.0, kind="LIMIT", price=99.5, expire_s=2), k, E.Costs(latency_ms=0)) is None


def test_market_aborted_when_price_ran_away():
    k = tk([100, 103, 103])
    o = E.Order(T0, +1, sl=98.0, price=100.0, max_slip=1.0)
    assert E.simulate(o, k, E.Costs(latency_ms=500)) is None


def test_time_stop():
    k = tk([100] * 30 + [100.05] * 30)
    o = E.Order(T0, +1, sl=99.0, tp=103.0, time_stop_s=20, time_stop_min_r=0.3)
    f = E.simulate(o, k, E.Costs(latency_ms=0))
    assert f.reason == "time stop" and 19_000 <= f.t_exit - f.t_entry <= 21_000


def test_one_position_at_a_time_and_daily_stop():
    k = tk([100] * 5 + [97] * 5 + [100] * 5 + [97] * 5 + [100] * 40)
    orders = [E.Order(T0 + i * 1000, +1, sl=98.0, tp=103.0, max_hold_s=30) for i in range(0, 40, 2)]
    fills = E.run(orders, k, E.Costs(latency_ms=0), E.Rules(daily_loss_r=1.0))
    assert len(fills) == 1                       # first trade loses ~1R -> no more today
    fills = E.run(orders, k, E.Costs(latency_ms=0), E.Rules())
    assert all(b.t_entry >= a.t_exit for a, b in zip(fills, fills[1:]))


def test_basket_counts_as_one_trade():
    k = tk([100, 100, 99.5, 98.8, 101, 103])     # ask reaches 98.9 <= the 99.0 limit
    legs = [E.Order(T0, +1, sl=98.0, tp=102.0, group=1, weight=0.5),
            E.Order(T0, +1, sl=98.0, tp=102.0, kind="LIMIT", price=99.0, expire_s=60, group=1, weight=0.5)]
    fills = E.run(legs, k, E.Costs(latency_ms=0))
    s = E.stats(fills)
    assert len(fills) == 2 and s["trades"] == 1


def test_bars_from_ticks():
    k = tk(list(range(100, 160)), step_ms=1000)
    b = make_bars(k, 30)
    assert len(b) == 2 and b.n.tolist() == [30, 30] and b.o[0] == 99.9 and b.c[0] == 128.9
