"""Databento market-data layer: instrument map, store, resampling, basis, fallback."""
import asyncio
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from backend.marketdata import instruments, source, store
from backend.marketdata.live import MinuteBook


@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETDATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARKETDATA_SOURCE", "databento")
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    store._mem.clear()
    return tmp_path


def _minutes(start="2026-03-02 00:00", n=1440 * 3, px=100.0, flow=True, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1min")
    c = px + np.cumsum(rng.normal(0, 0.1, n))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05  # noqa: E741
    v = rng.integers(10, 100, n).astype(float)
    buy = np.floor(v * 0.6)
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v,
                       "buy": buy if flow else np.nan, "sell": (v - buy) if flow else np.nan,
                       "big_buy": np.where(v > 90, buy, 0.0) if flow else np.nan,
                       "big_sell": 0.0 if flow else np.nan, "pv": c * v if flow else np.nan}, index=idx)
    return df


@pytest.mark.parametrize("sym,fut", [("XAUUSD", "GC.v.0"), ("XAUUSDm", "GC.v.0"), ("US Tech 100", "NQ.v.0"),
                                     ("USTECm", "NQ.v.0"), ("US SP 500", "ES.v.0"), ("Wall Street 30", "YM.v.0"),
                                     ("US Oil", "CL.v.0"), ("Japan 225", "NKD.v.0"), ("USDJPY", "6J.v.0"),
                                     ("GBPUSD.r", "6B.v.0"), ("XAGUSD", "SI.v.0")])
def test_lookup_maps_broker_names(sym, fut):
    assert instruments.lookup(sym).future == fut


@pytest.mark.parametrize("sym", ["Volatility 75 Index", "Boom 500 Index", "GBPJPY", "Germany 40", ""])
def test_unmapped_symbols_stay_on_mt5(sym):
    assert instruments.lookup(sym) is None


def test_switch_and_exclude(monkeypatch):
    monkeypatch.setenv("MARKETDATA_SOURCE", "mt5")
    assert instruments.mapped("XAUUSD") is None
    monkeypatch.setenv("MARKETDATA_SOURCE", "databento")
    monkeypatch.setenv("MARKETDATA_EXCLUDE", "XAUUSDm, EURUSD")
    assert instruments.mapped("XAUUSDm") is None and instruments.mapped("US Tech 100") is not None


def test_resample_flow_columns():
    m = _minutes(n=30)
    b = source.resample(m, "M15")
    assert len(b) == 2
    first = m.iloc[:15]
    assert b["open"].iloc[0] == first["open"].iloc[0] and b["close"].iloc[0] == first["close"].iloc[-1]
    assert b["high"].iloc[0] == first["high"].max() and b["low"].iloc[0] == first["low"].min()
    assert b["delta"].iloc[0] == pytest.approx((first["buy"] - first["sell"]).sum())
    h2 = first.iloc[7:]                                   # minutes 7..14: the research's len // 2
    assert b["delta_h2"].iloc[0] == pytest.approx((h2["buy"] - h2["sell"]).sum())
    assert b["vwap"].iloc[0] == pytest.approx(first["pv"].sum() / first["volume"].sum())
    assert b["time"].iloc[1] - b["time"].iloc[0] == 900


def test_resample_without_flow_gives_nan_delta():
    b = source.resample(_minutes(n=30, flow=False), "M5")
    assert b["delta"].isna().all() and b["big_buy"].isna().all() and b["vwap"].notna().all()


def test_invert_swaps_prices_and_sides():
    m = _minutes(n=5)
    m[["open", "high", "low", "close"]] = m[["open", "high", "low", "close"]] * 0.000064
    m["pv"] = m["close"] * m["volume"]
    inv = source.invert(m)
    assert inv["high"].iloc[0] == pytest.approx(1 / m["low"].iloc[0])
    assert inv["low"].iloc[0] == pytest.approx(1 / m["high"].iloc[0])
    assert (inv["buy"] == m["sell"]).all() and (inv["big_sell"] == m["big_buy"]).all()
    assert 150 < inv["close"].iloc[0] < 160


def test_basis_per_day_and_latest():
    fut = source.resample(_minutes(n=1440 * 2), "M15").reset_index(drop=True)
    mt5 = fut[["time", "close"]].copy()
    day = pd.to_datetime(mt5["time"], unit="s").dt.normalize()
    mt5["close"] = mt5["close"] + np.where(day == day.min(), 2.0, 3.0)
    b, how = source.basis_series(fut, mt5)
    assert how.startswith("matched")
    assert b.iloc[0] == pytest.approx(2.0) and b.iloc[-1] == pytest.approx(3.0)
    assert source.basis_series(fut, None)[0] is None


def test_store_prefers_flow_and_day_files(tmp_store):
    m = _minutes(start="2026-03-02", n=1440)
    store.write_day("GC.v.0", date(2026, 3, 2), m)
    lv = m.iloc[-60:].copy()
    lv[["buy", "sell", "big_buy", "big_sell", "pv"]] = np.nan
    lv["close"] = lv["close"] + 50                         # a streamed minute without flow
    extra = _minutes(start="2026-03-03", n=10)
    store.write_live("GC.v.0", pd.concat([lv, extra]))
    got = store.read_range("GC.v.0", datetime(2026, 3, 2), datetime(2026, 3, 3, 0, 10))
    assert len(got) == 1440 + 10
    assert got.loc[m.index[-1], "close"] == pytest.approx(m["close"].iloc[-1])   # the day file wins
    assert got.loc[extra.index[0], "close"] == pytest.approx(extra["close"].iloc[0])


def test_minute_book_builds_flow():
    b = MinuteBook(big=5)
    t0 = int(pd.Timestamp("2026-03-02 10:00:10").value)
    b.add(100.0, 2, "B", t0)
    b.add(101.0, 6, "A", t0 + 20_000_000_000)
    b.add(99.0, 1, "B", t0 + 70_000_000_000)                # next minute
    f = b.frame()
    r = f.iloc[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (100.0, 101.0, 100.0, 101.0)
    assert (r["volume"], r["buy"], r["sell"], r["big_sell"], r["big_buy"]) == (8, 2, 6, 6, 0)
    b.drop_from(int(pd.Timestamp("2026-03-02 10:01").timestamp()))
    assert len(b.frame()) == 1


def _run(coro):
    return asyncio.run(coro)


def test_range_bars_translated_with_basis(tmp_store, monkeypatch):
    m = _minutes(start="2026-03-02", n=1440 * 2)
    for d in (date(2026, 3, 2), date(2026, 3, 3)):
        store.write_day("GC.v.0", d, m)
    fut = source.resample(m, "M15").reset_index(drop=True)
    mt = fut[["time", "close"]].copy()
    mt["close"] += 5.0
    mt["spread"] = 20

    async def fake(symbol, tf, count=None, start=None, end=None):
        return mt
    monkeypatch.setattr(source, "_mt5_bars", fake)
    s, e = datetime(2026, 3, 2, tzinfo=timezone.utc), datetime(2026, 3, 3, 12, tzinfo=timezone.utc)
    df = _run(source.bars("XAUUSD", "M15", start=s, end=e))
    assert df is not None and df.attrs["source"] == "databento"
    assert df["time"].iloc[0] == int(s.timestamp()) and df["time"].iloc[-1] <= int(e.timestamp())
    ref = fut.set_index("time").loc[df["time"].iloc[3]]
    assert df["close"].iloc[3] == pytest.approx(ref["close"] + 5.0)
    assert df["delta"].iloc[3] == pytest.approx(ref["delta"])
    assert (df["spread"] == 20).all()
    assert list(df.columns[:8]) == ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


def test_live_request_falls_back_without_fresh_stream(tmp_store):
    assert _run(source.bars("XAUUSD", "M15", count=100)) is None          # no live file
    assert _run(source.bars("Volatility 75 Index", "M15", count=100)) is None


def test_live_request_needs_a_basis(tmp_store, monkeypatch):
    now = pd.Timestamp.now(tz="UTC").tz_localize(None).floor("1min")
    m = _minutes(start=now - pd.Timedelta(minutes=1440 * 3 - 1), n=1440 * 3)
    store.write_live("GC.v.0", m)

    async def none(*a, **k):
        return None
    monkeypatch.setattr(source, "_mt5_bars", none)
    assert _run(source.bars("XAUUSD", "M15", count=100)) is None          # orders would be off by the basis

    async def some(symbol, tf, count=None, start=None, end=None):
        f = source.resample(m, tf).reset_index(drop=True).iloc[-count:][["time", "close"]].copy()
        f["close"] += 1.5
        return f
    monkeypatch.setattr(source, "_mt5_bars", some)
    df = _run(source.bars("XAUUSD", "M15", count=100))
    assert df is not None and len(df) == 100 and df.attrs["basis"] == pytest.approx(1.5)


def test_datafetcher_uses_databento_first(tmp_store, monkeypatch):
    from backend.mt5 import data_fetcher as dfm

    async def fake(symbol, tf, count=None, start=None, end=None):
        return pd.DataFrame({"time": [1, 2], "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
                             "close": [1.0, 2.0], "tick_volume": [1, 1], "spread": [0, 0], "real_volume": [1, 1]})
    monkeypatch.setattr(source, "bars", fake)
    dfm.DataFetcher._cache.clear()
    got = _run(dfm.DataFetcher.get_historical_data("XAUUSD", "M15", 2))
    assert list(got["close"]) == [1.0, 2.0]
    got = _run(dfm.DataFetcher.get_data_range("XAUUSD", "M15", datetime(2026, 1, 1), datetime(2026, 1, 2)))
    assert len(got) == 2


def test_second_half_delta_and_prior_day_nodes_follow_the_research():
    from backend.marketdata.source import prior_day_nodes, resample
    idx = pd.date_range("2026-03-02", periods=2 * 1440, freq="1min")
    n = len(idx)
    k = np.arange(n)
    # two price clusters a day (100 and 104), each spread over a few bins
    px = np.where(k % 1440 < 720, 100.0, 104.0) + 0.2 * np.sin(k / 7.0)
    m = pd.DataFrame({"open": px, "high": px + 0.01, "low": px - 0.01, "close": px, "volume": 10.0,
                      "buy": np.arange(n) % 15 * 1.0, "sell": 0.0, "big_buy": 0.0, "big_sell": 0.0,
                      "pv": px * 10.0}, index=idx)
    b = resample(m, "M15")
    # minutes 7..14 of each 15-minute bar: 7+8+...+14 = 84
    assert b["delta_h2"].iloc[0] == 84.0
    nodes = prior_day_nodes(m)
    assert pd.Timestamp("2026-03-03") in nodes and pd.Timestamp("2026-03-04") in nodes
    assert pd.Timestamp("2026-03-02") not in nodes                 # nothing before the first day
    assert all(any(abs(x - c) <= 0.3 for x in nodes[pd.Timestamp("2026-03-03")]) for c in (100.0, 104.0))


def test_a_fresh_live_file_with_old_minutes_is_not_live(tmp_store, monkeypatch):
    """After a restart the flusher rewrites the live file every few seconds while it
    still holds only the seed history: that is not a live feed (27 Nov 2026)."""
    now = pd.Timestamp.now(tz="UTC").tz_localize(None).floor("1min")
    old = _minutes(start=now - pd.Timedelta(minutes=1440 * 3 + 30), n=1440 * 3)   # ends ~30 min ago
    store.write_live("GC.v.0", old)
    assert store.live_age("GC.v.0") < 5
    assert store.live_lag("GC.v.0") > 1500

    async def some(symbol, tf, count=None, start=None, end=None):
        f = source.resample(old, tf).reset_index(drop=True).iloc[-count:][["time", "close"]].copy()
        return f
    monkeypatch.setattr(source, "_mt5_bars", some)
    assert _run(source.bars("XAUUSD", "M15", count=100)) is None
    fresh = _minutes(start=now - pd.Timedelta(minutes=1440 * 3 - 1), n=1440 * 3)
    store.write_live("GC.v.0", fresh)
    assert store.live_lag("GC.v.0") < 120
    assert _run(source.bars("XAUUSD", "M15", count=100)) is not None


def test_the_stream_does_not_wait_for_the_seed(monkeypatch):
    import sys
    import types

    from backend.marketdata.live import live_bars
    from backend.orderflow import feed as fm
    started = []
    monkeypatch.setattr(live_bars, "seeded", False)
    monkeypatch.setattr(live_bars, "seed_history", lambda: started.append("seed") or __import__("time").sleep(5))

    class Boom(Exception):
        pass

    class FakeLive:
        def __init__(self, *a, **k):
            started.append("client")
            raise Boom()
    monkeypatch.setitem(sys.modules, "databento", types.SimpleNamespace(Live=FakeLive))
    monkeypatch.setattr(fm.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()))
    f = fm.OrderFlowFeed()
    with pytest.raises(KeyboardInterrupt):
        f._run()
    assert "client" in started                    # subscribed at once, the seed runs beside it


def test_live_frames_end_with_the_forming_bar():
    from backend.marketdata.source import resample, with_forming_bar
    idx = pd.date_range("2026-03-02 09:00", periods=15, freq="1min")       # 09:00-09:14: bars 09:00, 09:05, 09:10
    m = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": np.arange(15.0), "volume": 1.0, "buy": 1.0,
                      "sell": 0.0, "big_buy": 0.0, "big_sell": 0.0, "pv": 1.0}, index=idx)
    f = resample(m, "M5")
    now = datetime(2026, 3, 2, 9, 15, 3, tzinfo=timezone.utc)               # nothing traded in 09:15 yet
    g = with_forming_bar(f, now, 5)
    assert len(g) == len(f) + 1 and g.index[-1] == pd.Timestamp("2026-03-02 09:15")
    assert g["close"].iloc[-1] == 14.0 and g["of_vol"].iloc[-1] == 0 and g["time"].iloc[-1] == int(g.index[-1].timestamp())
    assert g.iloc[:-1].equals(f)                                             # the closed 09:10 bar survives the drop
    later = datetime(2026, 3, 2, 9, 31, tzinfo=timezone.utc)                # a gap: no invented bar
    assert len(with_forming_bar(f, later, 5)) == len(f)
    assert len(with_forming_bar(f, datetime(2026, 3, 2, 9, 12, tzinfo=timezone.utc), 5)) == len(f)


def test_a_range_the_store_only_partly_holds_falls_back_to_mt5(tmp_store, monkeypatch):
    """6A had only its last 30 days in the store: a January-November backtest must
    not run silently on one month of it."""
    from backend.marketdata import history
    monkeypatch.setattr(history, "key", lambda: "")
    m = _minutes(start=pd.Timestamp("2026-03-02"), n=1440 * 5)               # one week
    for day, part in m.groupby(m.index.normalize()):
        store.write_day("6E.v.0", day.date(), part)
    assert _run(source.bars("EURUSD", "M15", start=datetime(2026, 1, 5), end=datetime(2026, 3, 6))) is None
    assert source.coverage(m, pd.Timestamp("2026-03-02"), pd.Timestamp("2026-03-06")) == 1.0
