"""
tests/test_rl_datasets.py

The ingest, and the in-sample check that depends on it.

THE THREE SILENT FAILURES THIS GUARDS
-------------------------------------
1. **A close-stamped file.** The whole system indexes bars by their OPEN. A
   file whose "time" is the bar close shifts every feature one bar into the
   future - and nothing downstream can detect it, because the feature is still
   prefix-stable, just over the wrong prefix.

2. **A quietly resampled file.** Accepting intervals that do not match the
   declared timeframe and papering over them moves every rolling window. So a
   mismatch is REFUSED with the reason rather than repaired.

3. **An in-sample backtest.** Backtesting a trained agent on bars it trained
   on is the most convincing wrong number this system can produce. A full
   overlap must DISABLE Run, not warn - a warning you can click past is a
   warning that gets clicked past.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.rl import datasets as D

N = 600
START = 1735689600


def m5(n=N, start=START, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(start, start + n * 300, 300, dtype="int64")
    close = 1.08 + np.cumsum(rng.normal(0, 0.0002, n))
    return pd.DataFrame({"time": t, "open": close - 1e-5, "high": close + 2e-4,
                         "low": close - 2e-4, "close": close,
                         "volume": rng.integers(10, 100, n).astype(float)})


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ── headers ────────────────────────────────────────────────────────────────

def test_metatrader_style_headers_are_understood(tmpdir):
    df = m5()
    mt = pd.DataFrame({"<DATE>": df["time"], "<OPEN>": df["open"], "<HIGH>": df["high"],
                       "<LOW>": df["low"], "<CLOSE>": df["close"],
                       "<TICKVOL>": df["volume"]})
    path = tmpdir / "mt.csv"
    mt.to_csv(path, index=False)
    ds = D.load_csv(path, "EURUSD", "M5")
    assert ds.rows == N and ds.start == START


def test_a_tab_separated_export_is_sniffed(tmpdir):
    path = tmpdir / "tabs.csv"
    m5().to_csv(path, index=False, sep="\t")
    assert D.load_csv(path, "EURUSD", "M5").rows == N


def test_an_unrecognised_header_is_named_along_with_what_was_found(tmpdir):
    """One look at the error must say what to add. Guessing silently is the
    alternative and it is worse."""
    path = tmpdir / "odd.csv"
    m5().rename(columns={"close": "settlement"}).to_csv(path, index=False)
    with pytest.raises(D.DatasetError) as exc:
        D.load_csv(path, "EURUSD", "M5")
    assert "missing ['close']" in str(exc.value)
    assert "settlement" in str(exc.value)
    assert "COLUMN_ALIASES" in str(exc.value)


def test_json_records_and_a_wrapper_key_both_load(tmpdir):
    rows = m5(50).to_dict(orient="records")
    plain, wrapped = tmpdir / "a.json", tmpdir / "b.json"
    plain.write_text(json.dumps(rows), encoding="utf-8")
    wrapped.write_text(json.dumps({"candles": rows}), encoding="utf-8")
    assert D.load_json(plain, "EURUSD", "M5").rows == 50
    assert D.load_json(wrapped, "EURUSD", "M5").rows == 50


def test_an_npz_archive_loads(tmpdir):
    df = m5(100)
    path = tmpdir / "bars.npz"
    np.savez(path, **{c: df[c].to_numpy() for c in df.columns})
    assert D.load_npz(path, "EURUSD", "M5").rows == 100


def test_an_unsupported_file_type_is_refused(tmpdir):
    path = tmpdir / "bars.xlsx"
    path.write_bytes(b"not really")
    with pytest.raises(D.DatasetError, match="unsupported file type"):
        D.load_file(path, "EURUSD", "M5")


# ── time ───────────────────────────────────────────────────────────────────

def test_a_close_stamped_file_is_shifted_back_by_exactly_one_bar():
    ds_open = D.normalise(m5(), "EURUSD", "M5", time_is="open")
    ds_close = D.normalise(m5(), "EURUSD", "M5", time_is="close")
    assert ds_open.start - ds_close.start == 300


def test_a_close_stamped_file_needs_a_known_timeframe():
    with pytest.raises(D.DatasetError, match="without a known timeframe"):
        D.normalise(m5(), "EURUSD", "M7", time_is="close")


def test_milliseconds_are_recognised():
    df = m5(50)
    df["time"] = df["time"] * 1000
    assert D.normalise(df, "EURUSD", "M5").start == START


def test_a_text_timestamp_in_another_zone_is_converted_to_utc():
    df = m5(50)
    df["time"] = pd.to_datetime(df["time"], unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
    utc = D.normalise(df.copy(), "EURUSD", "M5", tz="UTC")
    ny = D.normalise(df.copy(), "EURUSD", "M5", tz="America/New_York")
    assert ny.start - utc.start == 5 * 3600


def test_epoch_seconds_supplied_as_text_are_read_as_numbers():
    """A CSV round-trip produces exactly this. Measured: reading them as
    datetimes coerced all 50 rows to NaT and refused a perfectly good file."""
    df = m5(50)
    df["time"] = df["time"].astype(str)
    assert D.normalise(df, "EURUSD", "M5").start == START


def test_an_unparseable_timestamp_is_counted_and_refused():
    df = m5(50)
    df["time"] = pd.to_datetime(df["time"], unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
    df.loc[3, "time"] = "not a date"
    with pytest.raises(D.DatasetError, match="1 unparseable"):
        D.normalise(df, "EURUSD", "M5")


# ── spacing and integrity ──────────────────────────────────────────────────

def test_intervals_that_do_not_match_the_timeframe_are_refused():
    """Resampling this quietly would move every rolling window."""
    df = m5(100)
    df.loc[50, "time"] = int(df.loc[50, "time"]) + 37
    with pytest.raises(D.DatasetError, match="not a multiple of 5 minutes"):
        D.normalise(df, "EURUSD", "M5")


def test_a_weekend_gap_is_a_note_and_not_an_error():
    """Whole-interval gaps are what weekends and holidays look like."""
    df = m5(100)
    df.loc[50:, "time"] = df.loc[50:, "time"] + 2 * 86400
    ds = D.normalise(df, "EURUSD", "M5")
    assert ds.gaps == 1 and any("gap" in n for n in ds.notes)


def test_a_bar_whose_high_does_not_contain_its_close_is_refused():
    """A broken export, not a market event."""
    df = m5(100)
    df.loc[20, "high"] = float(df.loc[20, "low"]) - 1.0
    with pytest.raises(D.DatasetError, match="high/low do not contain"):
        D.normalise(df, "EURUSD", "M5")


def test_a_duplicate_timestamp_keeps_the_last_row():
    """In an MT5 export the later row is the corrected one; keeping the first
    is how a stale bar survives a re-export."""
    df = m5(100)
    dup = df.iloc[[40]].copy()
    dup["volume"] = 9999.0          # a field that cannot break high/low containment
    out = D.normalise(pd.concat([df, dup], ignore_index=True), "EURUSD", "M5")
    assert out.rows == 100
    assert float(out.frame.loc[out.frame["time"] == int(df.loc[40, "time"]),
                               "volume"].iloc[0]) == 9999.0
    assert any("duplicate" in n for n in out.notes)


def test_unsorted_rows_are_sorted():
    df = m5(100).sample(frac=1.0, random_state=3)
    out = D.normalise(df, "EURUSD", "M5")
    assert out.frame["time"].is_monotonic_increasing


def test_an_empty_file_is_refused():
    with pytest.raises(D.DatasetError, match="no rows"):
        D.normalise(pd.DataFrame(), "EURUSD", "M5")


# ── the content hash ───────────────────────────────────────────────────────

def test_the_hash_is_of_the_bars_and_not_the_file():
    """Two exports of the same bars with different headers or column order are
    the same data - otherwise the overlap check is defeated by re-exporting."""
    a = D.normalise(m5(), "EURUSD", "M5")
    renamed = m5().rename(columns={"close": "CLOSE", "open": "<OPEN>"})
    b = D.normalise(renamed[["<OPEN>", "high", "low", "CLOSE", "time", "volume"]],
                    "EURUSD", "M5")
    assert a.content_hash == b.content_hash


def test_different_bars_hash_differently():
    a = D.normalise(m5(seed=1), "EURUSD", "M5")
    b = D.normalise(m5(seed=2), "EURUSD", "M5")
    assert a.content_hash != b.content_hash


# ── higher timeframes ──────────────────────────────────────────────────────

def test_resampling_drops_the_bar_that_is_still_forming():
    """An H1 bar built from the M5 bars available at 10:20 is not the 10:00 H1
    bar, and feeding it to a strategy is up to 55 minutes of lookahead."""
    df = m5(125)                       # 10 complete hours plus 5 extra M5 bars
    h1 = D.resample(df, 60)
    assert len(h1) == 10
    assert int(h1["time"].iloc[-1]) == START + 9 * 3600


def test_a_resampled_bar_aggregates_correctly():
    df = m5(24)
    h1 = D.resample(df, 60)
    first = df.iloc[:12]
    assert h1["open"].iloc[0] == pytest.approx(first["open"].iloc[0])
    assert h1["close"].iloc[0] == pytest.approx(first["close"].iloc[-1])
    assert h1["high"].iloc[0] == pytest.approx(first["high"].max())
    assert h1["low"].iloc[0] == pytest.approx(first["low"].min())
    assert h1["volume"].iloc[0] == pytest.approx(first["volume"].sum())


def test_alignment_passes_on_a_properly_resampled_frame():
    df = m5(600)
    D.assert_htf_alignment(df, D.resample(df, 60), 60)


def test_alignment_fails_on_a_close_stamped_htf_frame():
    """The check this function exists for, and the one the first version of it
    could not make: an as-of search guarantees its own postcondition, so
    asserting that postcondition passed a deliberately close-stamped frame."""
    from backend.rl.features import HTFAlignmentError

    df = m5(600)
    h1 = D.resample(df, 60)
    h1["time"] = h1["time"] + 3600
    with pytest.raises(HTFAlignmentError, match="stamped by close"):
        D.assert_htf_alignment(df, h1, 60)


def test_alignment_fails_on_overlapping_htf_windows():
    """Bars closer together than one period are a rolling window dressed up as
    bars, and every one of them would carry part of the next."""
    from backend.rl.features import HTFAlignmentError

    df = m5(600)
    h1 = D.resample(df, 60)
    rolling = h1.copy()
    rolling["time"] = rolling["time"].to_numpy() - np.arange(len(rolling)) * 300
    with pytest.raises(HTFAlignmentError, match="closer together"):
        D.assert_htf_alignment(df, rolling, 60)


# ── overlap ────────────────────────────────────────────────────────────────

def coverage(start=START, end=START + N * 300, symbol="EURUSD", tf="M5"):
    return [{"symbol": symbol, "timeframe": tf, "start": start, "end": end, "hash": "h"}]


def test_a_window_entirely_inside_the_training_data_is_not_runnable():
    o = D.overlap(coverage(), "EURUSD", "M5", START, START + N * 300)
    assert o.state == "in_sample" and o.runnable is False
    assert "pick a window after" in o.message


def test_a_window_after_the_training_data_is_clean():
    end = START + N * 300
    o = D.overlap(coverage(), "EURUSD", "M5", end + 86400, end + 2 * 86400)
    assert o.state == "clean" and o.runnable is True


def test_a_partial_overlap_names_the_dates_and_stays_runnable():
    end = START + N * 300
    o = D.overlap(coverage(), "EURUSD", "M5", START + N * 150, end + N * 300)
    assert o.state == "partial" and o.runnable is True
    assert 0.3 < o.fraction < 0.4
    assert "used in training" in o.message and o.ranges


def test_overlap_is_matched_by_symbol_and_timeframe():
    """An agent trained on EURUSD M5 has seen nothing of GBPJPY, and nothing of
    EURUSD H1."""
    assert D.overlap(coverage(), "GBPJPY", "M5", START, START + 1000).state == "clean"
    assert D.overlap(coverage(), "EURUSD", "H1", START, START + 1000).state == "clean"


def test_several_training_ranges_are_merged_before_the_fraction_is_taken():
    cov = coverage(START, START + 1000) + coverage(START + 500, START + 2000)
    o = D.overlap(cov, "EURUSD", "M5", START, START + 4000)
    assert o.fraction == pytest.approx(0.5)
    assert len(o.ranges) == 1, "overlapping ranges must be merged, not counted twice"


def test_an_inverted_window_is_clean_rather_than_an_error():
    assert D.overlap(coverage(), "EURUSD", "M5", START + 1000, START).state == "clean"


# -- the training window, against a frame that has been through normalise --


def _normalised_frame(rows: int = 3000):
    """A frame in the shape everything upstream actually produces.

    This matters more than it looks. `clip_to_window`'s first test built a
    frame with a DatetimeIndex by hand and passed; a normalised frame carries
    `time` as an int64 COLUMN of microseconds with a plain RangeIndex, and
    the function raised TypeError on every one of them. `load_frames` caught
    it per symbol, skipped them all, and the training run died with "no bars
    for any pre-registered symbol" - which sent the admin off changing dates
    for an hour. A fixture that lies about its input is worse than no test.
    """
    import numpy as np
    import pandas as pd

    from backend.rl.datasets import normalise

    t0 = 1_700_000_000
    t = np.arange(rows, dtype=np.int64) * 300 + t0
    px = 100.0 + np.cumsum(np.full(rows, 0.001))
    df = pd.DataFrame({
        "time": pd.to_datetime(t, unit="s"),
        "open": px, "high": px + 0.05, "low": px - 0.05, "close": px,
        "tick_volume": np.full(rows, 100.0),
    })
    return normalise(df, "TEST", "M5", source="test")


def test_clip_to_window_works_on_a_normalised_frame():
    import pandas as pd

    from backend.rl.datasets import clip_to_window, normalised_times

    ds = _normalised_frame()
    ts = normalised_times(ds.frame)
    assert not isinstance(ds.frame.index, pd.DatetimeIndex), (
        "if this ever becomes a DatetimeIndex the bug this guards is gone, "
        "but so is the reason the test is written this way")

    mid = ts.iloc[len(ts) // 2].strftime("%Y-%m-%d")
    whole = clip_to_window(ds.frame, "", "")
    assert len(whole) == len(ds.frame), "no window must change nothing"

    later = clip_to_window(ds.frame, mid, "")
    assert 0 < len(later) < len(ds.frame)
    assert normalised_times(later).min() >= pd.Timestamp(mid)


def test_a_bare_end_date_includes_that_whole_day():
    import pandas as pd

    from backend.rl.datasets import clip_to_window, normalised_times

    ds = _normalised_frame()
    ts = normalised_times(ds.frame)
    day = ts.iloc[len(ts) // 2].strftime("%Y-%m-%d")
    out = normalised_times(clip_to_window(ds.frame, "", day))
    assert out.max() >= pd.Timestamp(day) + pd.Timedelta(hours=23), (
        "an end date of 2026-01-31 has to mean the end of the 31st, not "
        "midnight at its start - otherwise a whole day vanishes silently")


def test_an_empty_window_says_what_the_source_actually_covers():
    import pytest as _pytest

    from backend.rl.datasets import DatasetError, clip_to_window

    ds = _normalised_frame()
    with _pytest.raises(DatasetError) as e:
        clip_to_window(ds.frame, "2099-01-01", "")
    assert "the source covers" in str(e.value), (
        "the admin needs the range that EXISTS, or they change the dates at "
        "random, which is exactly what happened")


def test_both_mt5_paths_select_the_symbol_first():
    """`(-2, Invalid params)` on an unselected symbol reads like a date bug.

    `get_historical_data` selected the symbol and `get_data_range` did not,
    so recent-bar fetches worked and every dated fetch failed on every
    symbol and every fallback. One shared helper now, and this fails if
    either path stops using it.
    """
    import inspect

    from backend.mt5 import data_fetcher as df

    assert "ensure_symbol" in inspect.getsource(df.DataFetcher.get_data_range)
    assert "ensure_symbol" in inspect.getsource(df._fetch_rates_frame)


# -- the epoch unit, pinned to the calendar rather than to itself ------------
# Every test above derived its window date from `normalised_times`, so when
# that function read epoch SECONDS as MICROSECONDS they all still passed while
# every real training window clipped its dataset to nothing. These pin literal
# dates, which is the only thing that can catch a unit error.
#
# `_normalised_frame` starts at t0 = 1,700,000,000, which is
# 2023-11-14 22:13:20 UTC. Read as microseconds it is 1970-01-01 00:28:20.

def test_normalised_times_reads_the_unit_that_normalise_writes():
    import pandas as pd

    from backend.rl.datasets import normalised_times

    first = normalised_times(_normalised_frame().frame).iloc[0]
    assert first == pd.Timestamp("2023-11-14 22:13:20"), (
        f"got {first}. If this says 1970 the unit is wrong again, and every "
        f"training window with a FROM date will clip its dataset to nothing.")


def test_a_from_date_keeps_the_bars_inside_it():
    """The failure the admin saw: a FROM date, and then no bars, whatever
    dates were tried. Asserted against raw epoch ints so this test cannot
    cancel out against the function it is testing."""
    from backend.rl.datasets import clip_to_window

    ds = _normalised_frame()                    # 2023-11-14 22:13 -> 2023-11-25
    out = clip_to_window(ds.frame, "2023-11-20", "")

    assert len(out) > 0, "a window well inside the data must not clip it to nothing"
    assert len(out) < len(ds.frame), "and it must actually clip something"
    assert int(out["time"].min()) >= 1_700_438_400      # 2023-11-20 00:00 UTC
    assert int(out["time"].max()) == int(ds.frame["time"].max())


def test_a_window_that_misses_the_data_names_the_real_coverage():
    """Not "no bars": the dates the source does hold, in the right century."""
    import pytest as _pytest

    from backend.rl.datasets import DatasetError, clip_to_window

    with _pytest.raises(DatasetError) as e:
        clip_to_window(_normalised_frame().frame, "2099-01-01", "")
    assert "2023-11" in str(e.value), str(e.value)
