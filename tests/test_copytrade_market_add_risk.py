"""on_first_fill = market_add must not let a signal lose more than its risk budget.

On the demo account (10-19 Nov 2026) six zone signals lost 1.4-2.0x the risk the
plan reported, because each waiting limit leg was also taken at market when the
first leg filled, and both copies then hit the stop.
"""
from backend.copytrade.parser import parse_message
from backend.copytrade.rules import build_open_plan, loss_per_lot, merge_rules

GOLD = {"point": 0.01, "tick_size": 0.01, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01,
        "volume_max": 100, "digits": 2, "stops_level": 0, "trade_mode": 4}
SIG = parse_message("Gold buy now 4059.6 - 4055 SL: 4051 TP: 4062 TP: 4064 TP: 4066").to_dict()
QUOTE = {"bid": 4061.0, "ask": 4061.3}          # above the zone: every leg is a limit


def worst_case(plan) -> float:
    """Each limit leg filled at its level, plus a market copy of legs 2..n at
    leg 1's price, all stopped out."""
    legs = plan.legs
    e1 = legs[0]["entry"]
    loss = sum(l["volume"] * loss_per_lot(l["entry"], plan.sl, GOLD) for l in legs)
    loss += sum(l["volume"] * loss_per_lot(e1, plan.sl, GOLD) for l in legs[1:] if l["kind"] == "LIMIT")
    return loss


def test_market_add_worst_case_stays_within_budget():
    r = merge_rules({"risk_pct": 2.0, "on_first_fill": "market_add", "min_lot_floor": False})
    p = build_open_plan(SIG, QUOTE, GOLD, 10_000, r, "XAUUSD")
    assert p.ok and p.order_type == "LAYER"
    assert worst_case(p) <= 200.0 * 1.001
    assert any("worst case" in n for n in p.notes)


def test_keep_mode_unchanged():
    r = merge_rules({"risk_pct": 2.0, "on_first_fill": "keep", "min_lot_floor": False})
    p = build_open_plan(SIG, QUOTE, GOLD, 10_000, r, "XAUUSD")
    assert p.ok and p.risk_dollars <= 200.0 * 1.001
    assert not any("worst case" in n for n in p.notes)


def test_market_add_old_sizing_would_have_exceeded_budget():
    # The same plan sized the old way (keep) and then market-added: over budget.
    keep = build_open_plan(SIG, QUOTE, GOLD, 10_000,
                           merge_rules({"risk_pct": 2.0, "on_first_fill": "keep", "min_lot_floor": False}), "XAUUSD")
    assert worst_case(keep) > 200.0 * 1.2
