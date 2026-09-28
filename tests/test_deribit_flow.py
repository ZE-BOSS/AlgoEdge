"""The option-flow pipeline, and the sign convention that decides its answer.

A sign error here would not crash or look odd — it would invert every conclusion
the study ever reaches, quietly. The convention:

    `direction` on a Deribit trade is the TAKER's side. The dealer is the maker,
    on the other side of it.

      taker BUYS a call  -> dealer is SHORT a call -> hedges by BUYING  spot
      taker BUYS a put   -> dealer is SHORT a put  -> hedges by SELLING spot
      taker SELLS either -> mirrored

    and the dealer is SHORT gamma on whatever the taker bought, which is the
    amplifying regime.

This is also the one thing this pipeline does that public GEX cannot: the sign of
dealer positioning is OBSERVED from the aggressor side, not assumed from the
"customers buy, dealers sell" heuristic that every public number rests on.
"""

import math
from datetime import datetime, timezone

import pytest

from scripts.run_deribit_flow_study import (
    bucket, enrich, greeks, parse_instrument,
)


# ── the instrument name ─────────────────────────────────────────────────────
def test_instrument_names_parse_to_strike_expiry_and_kind():
    got = parse_instrument("BTC-27NOV26-99000-C")
    assert got is not None
    currency, expiry, strike, kind = got
    assert currency == "BTC" and strike == 99000.0 and kind == "C"
    when = datetime.fromtimestamp(expiry, timezone.utc)
    assert (when.year, when.month, when.day) == (2026, 11, 27)
    assert when.hour == 8, "Deribit options expire at 08:00 UTC"


def test_a_single_digit_day_and_a_put_also_parse():
    got = parse_instrument("ETH-3OCT25-4000-P")
    assert got is not None
    _, expiry, strike, kind = got
    assert strike == 4000.0 and kind == "P"
    assert datetime.fromtimestamp(expiry, timezone.utc).day == 3


@pytest.mark.parametrize("name", ["BTC-27NOV26-99000", "rubbish", "BTC-XXNOV26-1-C",
                                  "BTC-27ZZZ26-1-C", "BTC-PERPETUAL"])
def test_anything_unparseable_is_refused_rather_than_guessed(name):
    assert parse_instrument(name) is None


# ── Black-Scholes ───────────────────────────────────────────────────────────
def test_an_at_the_money_call_has_delta_near_a_half():
    delta, gamma = greeks(spot=100.0, strike=100.0, years=0.25, vol=0.5, kind="C")
    assert delta == pytest.approx(0.5, abs=0.06)
    assert gamma > 0


def test_put_call_parity_holds_on_delta():
    """delta_call - delta_put = 1 for the same contract. A sign slip in either
    branch breaks this and nothing else would notice."""
    for strike in (80.0, 100.0, 130.0):
        c, gc = greeks(100.0, strike, 0.25, 0.5, "C")
        p, gp = greeks(100.0, strike, 0.25, 0.5, "P")
        assert c - p == pytest.approx(1.0, abs=1e-9)
        assert gc == pytest.approx(gp, rel=1e-12), "gamma is the same for both"


def test_deep_in_and_out_of_the_money_deltas_saturate():
    assert greeks(100.0, 10.0, 0.05, 0.4, "C")[0] == pytest.approx(1.0, abs=1e-6)
    assert greeks(100.0, 1000.0, 0.05, 0.4, "C")[0] == pytest.approx(0.0, abs=1e-6)
    assert greeks(100.0, 1000.0, 0.05, 0.4, "P")[0] == pytest.approx(-1.0, abs=1e-6)


def test_gamma_is_largest_at_the_money():
    atm = greeks(100.0, 100.0, 0.25, 0.5, "C")[1]
    away = greeks(100.0, 160.0, 0.25, 0.5, "C")[1]
    assert atm > away * 3


@pytest.mark.parametrize("spot,strike,years,vol", [
    (0.0, 100.0, 0.25, 0.5), (100.0, 0.0, 0.25, 0.5),
    (100.0, 100.0, 0.0, 0.5), (100.0, 100.0, 0.25, 0.0),
    (100.0, 100.0, -0.1, 0.5),
])
def test_degenerate_inputs_return_none_rather_than_nan(spot, strike, years, vol):
    assert greeks(spot, strike, years, vol, "C") is None


# ── the sign convention ─────────────────────────────────────────────────────
def _trade(kind: str, direction: str, strike: float = 100_000.0, size: float = 1.0):
    expiry = datetime(2026, 12, 25, 8, tzinfo=timezone.utc).timestamp()
    return {"instrument_name": f"BTC-25DEC26-{int(strike)}-{kind}",
            "timestamp": int((expiry - 30 * 86400) * 1000),
            "direction": direction, "amount": size, "iv": 50.0,
            "index_price": 100_000.0, "trade_id": f"{kind}{direction}{strike}{size}"}


def test_a_taker_buying_a_call_makes_the_dealer_buy_spot():
    row = enrich([_trade("C", "buy")])[0]
    assert row["hedge"] > 0, "dealer is short the call and hedges by buying"
    assert row["dealer_gamma"] < 0, "and is short gamma, the amplifying side"


def test_a_taker_buying_a_put_makes_the_dealer_sell_spot():
    row = enrich([_trade("P", "buy")])[0]
    assert row["hedge"] < 0, "dealer is short the put and hedges by selling"
    assert row["dealer_gamma"] < 0, "short gamma either way when the taker bought"


def test_selling_mirrors_buying_exactly():
    for kind in ("C", "P"):
        bought = enrich([_trade(kind, "buy")])[0]
        sold = enrich([_trade(kind, "sell")])[0]
        assert bought["hedge"] == pytest.approx(-sold["hedge"], rel=1e-12)
        assert bought["dealer_gamma"] == pytest.approx(-sold["dealer_gamma"], rel=1e-12)
    assert enrich([_trade("C", "sell")])[0]["dealer_gamma"] > 0, \
        "a taker SELLING leaves the dealer long gamma — the dampening side"


def test_hedge_demand_scales_with_size():
    one = enrich([_trade("C", "buy", size=1.0)])[0]
    ten = enrich([_trade("C", "buy", size=10.0)])[0]
    assert ten["hedge"] == pytest.approx(one["hedge"] * 10, rel=1e-12)


def test_a_call_and_a_put_bought_together_cancel_on_delta_but_not_gamma():
    """A straddle bought from a dealer leaves almost no delta to hedge and a lot
    of gamma — which is the whole reason gamma and delta are tracked separately."""
    rows = enrich([_trade("C", "buy"), _trade("P", "buy")])
    assert abs(sum(r["hedge"] for r in rows)) < 0.15
    assert sum(r["dealer_gamma"] for r in rows) < 0


def test_unparseable_and_unpriceable_trades_are_dropped_not_zeroed():
    bad = [{"instrument_name": "BTC-PERPETUAL", "timestamp": 0, "direction": "buy",
            "amount": 1.0, "iv": 50.0, "index_price": 100.0, "trade_id": "x"},
           {**_trade("C", "buy"), "iv": 0.0}]
    assert enrich(bad) == [], "a trade that cannot be priced must not count as zero flow"


# ── bucketing ───────────────────────────────────────────────────────────────
def test_buckets_sum_flow_and_keep_the_last_spot():
    rows = [{"t": 10.0, "spot": 100.0, "hedge": 1.0, "dealer_gamma": -1.0, "size": 1.0},
            {"t": 20.0, "spot": 105.0, "hedge": 2.0, "dealer_gamma": -2.0, "size": 3.0},
            {"t": 400.0, "spot": 110.0, "hedge": -5.0, "dealer_gamma": 4.0, "size": 2.0}]
    got = bucket(rows, 300)
    assert len(got) == 2
    assert got[0]["hedge"] == 3.0 and got[0]["size"] == 4.0 and got[0]["n"] == 2
    assert got[0]["spot"] == 105.0, "the bucket carries the spot at its END"
    assert got[1]["hedge"] == -5.0


def test_buckets_come_back_in_time_order_whatever_order_they_arrive():
    rows = [{"t": 900.0, "spot": 3.0, "hedge": 0.0, "dealer_gamma": 0.0, "size": 0.0},
            {"t": 10.0, "spot": 1.0, "hedge": 0.0, "dealer_gamma": 0.0, "size": 0.0},
            {"t": 400.0, "spot": 2.0, "hedge": 0.0, "dealer_gamma": 0.0, "size": 0.0}]
    assert [b["spot"] for b in bucket(rows, 300)] == [1.0, 2.0, 3.0]
