"""Late posts and recaps (26 Nov 2026): real messages that were turned into limit orders."""
from backend.copytrade.parser import parse_message
from backend.copytrade.rules import build_open_plan, merge_rules

GOLD = {"point": 0.01, "tick_size": 0.01, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01,
        "volume_max": 100, "digits": 2, "stops_level": 0}
VIP_SIG = "XAUUSD SELL 3612\n\nTP 3609\nTP 3606\nTP 3603\nTP 3600\nTP 3597\nTP 3594\nTP 3591\nTP 3588\n\nSL. 3630"
QAS = ("QAS VIP SIGNAL\n\nVolatility 75 (1s) Index - M15\n\nBUY\n\nENTRY ZONE: 3830.00 - 3838.00\n\n"
       "TP1: 3867.48\nTP2: 3904.24\nTP3: 3941.00\n\nSTOP LOSS: 3790.00")
PROFIT = "#XAUUSD BUY NOW 3544-40\n\nTP 3548\nTP 3552\nTP 3556\nTP 3560\nTP 3564 OPEN\n\nSL 3530"
SUNNY = "Buy Gold 4487 - 4495Stop Loss 4483TP1 4497TP2 4499TP3 4501TP4 Open (4504/4507/4512/4518)"


def plan(text, bid, ask, **over):
    return build_open_plan(parse_message(text).to_dict(), {"bid": bid, "ask": ask}, GOLD, 10_000,
                           merge_rules(over), "XAUUSD")


def test_market_call_on_time_is_market():
    p = plan(VIP_SIG, 3612.2, 3612.4)
    assert p.ok and p.order_type == "MARKET"


def test_market_call_slightly_late_is_market_not_a_limit():
    p = plan(VIP_SIG, 3611.6, 3611.8)                 # 0.4 of the 3.0 to TP1 = 13%
    assert p.ok and p.order_type == "MARKET"
    assert all(l.get("kind", "MARKET") == "MARKET" for l in p.legs)


def test_market_call_too_late_is_skipped_not_waited_for():
    p = plan(VIP_SIG, 3610.6, 3610.8, late_skip_frac=0.35)   # 1.4 of 3.0 = 47% of the way to TP1
    assert not p.ok and "too late" in p.reason
    p = plan(VIP_SIG, 3608.8, 3609.0)                 # default 1.0: price already past TP1 3609 -> a recap
    assert not p.ok and "too late" in p.reason


def test_explicit_limit_still_waits():
    p = plan("XAUUSD SELL LIMIT 3612\nTP 3609\nTP 3606\nSL 3630", 3605.0, 3605.2)
    assert p.ok and p.order_type == "LIMIT" and p.entry == 3612


def test_old_channel_setting_limit_no_longer_catches_late_posts():
    p = plan(VIP_SIG, 3608.8, 3609.0, chase_rule="limit")
    assert not p.ok and "too late" in p.reason


def test_buy_now_zone_after_it_ran_is_skipped():
    assert parse_message(PROFIT).now
    p = plan(PROFIT, 3548.3, 3548.6)                  # past TP1 3548
    assert not p.ok and "too late" in p.reason
    assert plan(PROFIT, 3542.0, 3542.3).ok             # inside the zone: taken


def test_recap_zone_skipped_where_the_channel_turns_it_on():
    assert plan(QAS, 3860.0, 3860.5).ok                # default: a zone may be posted ahead on purpose
    p = plan(QAS, 3868.0, 3868.5, late_skip_zones=True)
    assert not p.ok and "too late" in p.reason
    assert plan(QAS, 3835.0, 3835.5, late_skip_zones=True).ok


def test_zones_posted_ahead_still_layer_by_default():
    p = plan(SUNNY, 4505.0, 4505.3)
    assert p.ok and p.order_type == "LAYER"
    assert not parse_message(SUNNY).now



def test_scalping_channels_small_miss_is_taken_by_default():
    # 26 Nov: XAUUSD VIP SIGNAL 38%, Xauusd Trader 40%, Free Volatility 37% of a $3-$10 TP1 were skipped at 0.35
    p = plan(VIP_SIG, 3610.6, 3610.8)                 # 47% of the way: still a market entry at the 0.8 default
    assert p.ok and p.order_type == "MARKET"



def test_default_skips_only_past_tp1():
    assert plan(VIP_SIG, 3609.3, 3609.5).ok               # 90% of the way: still taken
    assert not plan(VIP_SIG, 3608.9, 3609.0).ok           # past TP1 3609: skipped


def test_last_leg_gets_the_farthest_target():
    p = plan(PROFIT, 3542.0, 3542.3)                     # TPs 3548..3564 + "OPEN"
    assert p.ok and all(l["tp"] is not None for l in p.legs) and p.legs[-1]["tp"] == 3564.0
    p = plan(VIP_SIG, 3612.2, 3612.4, max_legs=3)         # 8 TPs, 3 legs: last leg the farthest, 3588
    assert [l["tp"] for l in p.legs] == [3609.0, 3606.0, 3588.0]
    p = plan("XAUUSD SELL 3612\nTP OPEN\nSL 3630", 3612.2, 3612.4)   # no target number: R-multiple target
    assert p.ok and p.legs[-1]["tp"] is not None


def test_breakeven_locks_at_least_one_dollar():
    from backend.copytrade.rules import be_lock, merge_rules
    spec = {"tick_size": 0.01, "tick_value": 1.0, "digits": 2}      # gold: 0.01 lot: $1 per 1.00 move
    r = merge_rules({})
    assert r["be_min_profit_usd"] == 1.0
    assert be_lock(3564.0, "BUY", r, 0.01, spec) == 3565.0
    assert be_lock(3564.0, "SELL", r, 0.02, spec) == 3563.5
    assert be_lock(3564.0, "BUY", r, 0.03, spec) == 3564.34          # rounded outward: never under $1
    assert be_lock(3564.0, "BUY", merge_rules({"be_min_profit_usd": 0}), 0.01, spec) == 3564.0
    assert be_lock(3564.0, "BUY", r, 0.01, None) == 3564.0           # no spec: plain breakeven


def test_tp_with_filler_dots_and_comma_decimals():
    from backend.copytrade.parser import parse_message
    p = parse_message("🏆 Crash 300   index - SELL 🏆\n✅ Entry: . 2251,894\n⛔ Stop Loss: 2272,843\n"
                      "🎯 TP1: .. 2230,843\n🎯 TP2: ...")
    assert (p.side, p.entry, p.sl, p.tps) == ("SELL", 2251.894, 2272.843, [2230.843])
