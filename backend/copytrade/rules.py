"""
backend/copytrade/rules.py

The pure part of copy trading: given what a message says, the market price, the
broker's symbol spec and the account balance, decide exactly what to place.
No I/O - every function here is deterministic and unit-tested.

    merge_rules(user_rules, channel_rules)  -> the rules that apply
    resolve_symbol(canonical, available, rules) -> the broker's symbol name
    split_volume(total_lots, weights, spec) -> lots per TP leg
    build_open_plan(sig, quote, spec, balance, rules) -> OpenPlan
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

# Every rule, its default, and what it does. The UI renders this list.
DEFAULT_RULES: dict[str, Any] = {
    # sizing
    "risk_mode": "percent",       # percent: risk_pct of balance to the stop; fixed_lots: fixed_lots in total
    "risk_pct": 1.0,              # total risk per signal, % of balance, split across the TP legs
    "fixed_lots": 0.10,
    "split_weights": [],          # weight per TP leg, e.g. [40, 30, 20, 10]; empty = equal
    "max_legs": 10,               # use at most this many TPs (the nearest ones)
    "allow_min_lot": True,        # if the risk budget is below the broker's min lot, trade the min lot...
    "min_lot_max_risk_pct": 2.0,  # ...but only if that risk is at most this % of balance
    # entries
    "zone_entry": "layer",        # an entry ZONE: layer = limit orders spread through it (first fill -> TP1,
                                  # deepest -> last TP; already-reached levels fill at market); market = one entry now
    "market_tolerance_pct": 0.15, # a market signal is taken at market while price is within this % of the written entry
    "chase_rule": "limit",        # price ran past the entry: limit = wait at the entry with a limit order, skip, market
    "skip_if_tp1_passed": True,   # do not enter if price is already beyond TP1
    "pending_expiry_hours": 24,   # cancel limit/stop orders that have not filled after this long (0 = never)
    # missing numbers
    "missing_sl": "skip",         # skip, or default = place a stop default_sl_pct away
    "default_sl_pct": 0.5,
    "missing_tp_rr": 2.0,         # no TP written: one leg with TP at this R multiple (0 = no TP)
    # follow-ups
    "follow_breakeven": True,
    "follow_move_sl": True,
    "allow_widen_sl": True,       # follow an instruction that moves the stop further away
    "follow_move_tp": True,
    "follow_close": True,
    "follow_partial_close": True,
    "follow_cancel": True,
    "be_offset_pct": 0.0,         # breakeven = entry +/- this % (to cover spread/commission)
    "cancel_pending_on_be": True, # "risk free" means no risk left: a breakeven instruction also cancels the signal's unfilled orders
    "cancel_pending_after_tp": 1, # once TPn fills, cancel the signal's unfilled zone orders (0 = keep them)
    "be_after_tp": 0,             # 0 = off; 1 = move the remaining legs to breakeven once TP1 fills; 2 = after TP2 ...
    "trail_ladder": False,        # trail every open leg as the signal's targets are reached: TP1 -> breakeven,
                                  # TP2 -> TP1, TP3 -> TP2 ... (stops only ever tighten)
    "on_first_fill": "keep",      # zone legs when the first one fills: keep (wait at their levels) /
                                  # market (cancel them, take the same volume at market at once) /
                                  # market_add (take the same volume at market AND keep the limits until TPn)
    "edit_updates": True,         # an edited signal updates SL/TP of the trade it opened
    "close_on_opposite": False,   # a new signal in the other direction closes this channel's open trades on that symbol
    "max_sl_move_pct": 3.0,       # ignore a "move SL to X" more than this % from the entry (a typo: 4113 for 4413)
    # filters
    "only_admins": True,          # in a GROUP, act only on messages from its admins (members chat there too)
    "allowed_senders": [],        # or only these senders (@username or user id) - overrides only_admins
    "default_symbol": "",         # symbol for signals that don't name one ("Take Entry SELL 4430 ...")
    "allowed_symbols": [],        # canonical names; empty = everything
    "max_open_signals": 5,        # open signals at once (per channel)
    "min_confidence": 0.8,
    # broker
    "symbol_map": {},             # canonical -> broker name, e.g. {"NAS100": "US Tech 100"}
    "symbol_suffix": "",          # appended when the broker uses suffixes, e.g. ".m"
    "magic": 7701,                # outside the bot's 1001-1101 range: the strategies' trade manager leaves these alone
}

# The broker names Deriv (and many others) use for the common canonical symbols.
BROKER_CANDIDATES: dict[str, list[str]] = {
    "NAS100": ["NAS100", "US Tech 100", "USTEC", "US100", "NDX100", "NAS100.cash"],
    "US30": ["US30", "Wall Street 30", "DJ30", "US30.cash"],
    "US500": ["US500", "US SP 500", "SPX500", "US500.cash"],
    "GER40": ["GER40", "Germany 40", "DE40", "GER40.cash"],
    "UK100": ["UK100", "UK 100", "UK100.cash"],
    "USOIL": ["USOIL", "US Oil", "WTI", "XTIUSD"],
    "UKOIL": ["UKOIL", "UK Brent Oil", "BRENT", "XBRUSD"],
    "XAUUSD": ["XAUUSD", "GOLD"],
    "XAGUSD": ["XAGUSD", "SILVER"],
    "BTCUSD": ["BTCUSD", "BTCUSDT"],
    "ETHUSD": ["ETHUSD", "ETHUSDT"],
}


def merge_rules(user_rules: dict | None, channel_rules: dict | None = None) -> dict[str, Any]:
    out = dict(DEFAULT_RULES)
    for src in (user_rules or {}, channel_rules or {}):
        for k, v in src.items():
            if k in DEFAULT_RULES and v is not None and v != "":
                out[k] = v
    return out


def resolve_symbol(canonical: str | None, available: list[str], rules: dict) -> str | None:
    """The broker's name for `canonical`, or None if the account has nothing that matches."""
    if not canonical:
        return None
    by_upper = {a.upper(): a for a in available}
    mapped = (rules.get("symbol_map") or {}).get(canonical)
    suffix = rules.get("symbol_suffix") or ""
    cands = ([mapped] if mapped else []) + BROKER_CANDIDATES.get(canonical, []) + [canonical]
    for c in cands:
        for name in (c + suffix, c):
            if name.upper() in by_upper:
                return by_upper[name.upper()]
    # last resort: the only symbol that starts with the canonical name ("XAUUSD.m", "EURUSDm")
    starts = [a for a in available if a.upper().startswith(canonical.upper())]
    return starts[0] if len(starts) == 1 else None


def floor_step(v: float, step: float) -> float:
    if step <= 0:
        return round(v, 8)
    return round(math.floor(v / step + 1e-9) * step, 8)


def loss_per_lot(entry: float, sl: float, spec: dict) -> float:
    """Account-currency loss of 1 lot from entry to sl."""
    tick_size = spec.get("tick_size") or spec.get("point") or 0.0
    tick_value = spec.get("tick_value") or 0.0
    if tick_size <= 0 or tick_value <= 0:
        return 0.0
    return abs(entry - sl) / tick_size * tick_value


def split_volume(total: float, weights: list[float], spec: dict) -> list[float]:
    """Split `total` lots over len(weights) legs in proportion to the weights,
    floored to the lot step. Legs that would fall below the broker minimum are
    dropped from the end (the furthest TPs) and their share goes to the others.
    The floor remainder is added to leg 1. Returns [] if even one min lot does
    not fit."""
    vmin = spec.get("volume_min") or 0.01
    step = spec.get("volume_step") or vmin
    n = len(weights)
    while n > 0:
        w = [max(float(x), 0.0) for x in weights[:n]]
        if sum(w) <= 0:
            w = [1.0] * n
        s = sum(w)
        legs = [floor_step(total * x / s, step) for x in w]
        if all(v >= vmin - 1e-12 for v in legs):
            # the floor remainder, one step at a time from leg 1 on
            k = int(round(floor_step(total - sum(legs), step) / step)) if step > 0 else 0
            for j in range(k):
                legs[j % n] = round(legs[j % n] + step, 8)
            return legs
        n -= 1
    return []


@dataclass
class OpenPlan:
    ok: bool = False
    reason: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = "MARKET"        # MARKET / LIMIT / STOP - what is SENT, after the chase rule
    entry: float = 0.0                # market: the current price; pending: the order price
    sl: float = 0.0
    legs: list[dict] = field(default_factory=list)   # [{"leg": 1, "volume": .., "tp": ..}]
    risk_dollars: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _weights_for(n: int, rules: dict) -> list[float]:
    w = [float(x) for x in (rules.get("split_weights") or []) if x is not None]
    if len(w) >= n:
        return w[:n]
    # fewer weights than legs: the missing legs get the average of the given ones (or 1)
    fill = (sum(w) / len(w)) if w else 1.0
    return w + [fill] * (n - len(w))


def build_open_plan(sig: dict, quote: dict, spec: dict, balance: float, rules: dict,
                    broker_symbol: str) -> OpenPlan:
    """Decide what to place for an OPEN signal.

    sig: ParsedSignal.to_dict(); quote: {"bid", "ask"}; spec: symbol spec
    (point, tick_size, tick_value, volume_min, volume_step, volume_max, digits,
    stops_level)."""
    p = OpenPlan(symbol=broker_symbol, side=sig.get("side") or "")
    buy = p.side == "BUY"
    if p.side not in ("BUY", "SELL"):
        p.reason = "no direction in the message"
        return p
    bid, ask = float(quote.get("bid") or 0), float(quote.get("ask") or 0)
    if bid <= 0 or ask <= 0:
        p.reason = f"no price for {broker_symbol}"
        return p
    mkt = ask if buy else bid
    s = 1 if buy else -1
    want = (sig.get("order_type") or "MARKET").upper()
    entry = sig.get("entry")
    lo, hi = sig.get("entry_low"), sig.get("entry_high")
    tol = float(rules.get("market_tolerance_pct") or 0) / 100.0

    if (rules.get("zone_entry", "layer") == "layer" and want in ("MARKET", "LIMIT")
            and lo is not None and hi is not None and float(hi) > float(lo)):
        return _layer_plan(p, sig, mkt, s, float(lo), float(hi), spec, balance, rules)

    # ── where and how to enter ──
    if want == "MARKET":
        if entry is None:
            p.order_type, p.entry = "MARKET", mkt
        else:
            # the worst acceptable price: the far edge of the zone plus the tolerance
            edge = (hi if buy else lo) if (hi is not None and lo is not None) else entry
            limit_price = edge * (1 + s * tol)
            if (mkt - limit_price) * s <= 0:
                p.order_type, p.entry = "MARKET", mkt
            else:
                rule = rules.get("chase_rule", "limit")
                if rule == "skip":
                    p.reason = f"price {mkt} ran past the entry {edge} by more than {tol*100:.2f}%"
                    return p
                if rule == "market":
                    p.order_type, p.entry = "MARKET", mkt
                    p.notes.append(f"entered at market {mkt}, past the written entry {edge}")
                else:
                    p.order_type, p.entry = "LIMIT", float(entry)
                    p.notes.append(f"price {mkt} is past the entry; waiting at {entry} with a limit order")
    else:
        if entry is None:
            p.reason = f"{want.lower()} order without a price"
            return p
        entry = float(entry)
        if want == "LIMIT":
            # buy limit must be below the ask; if price is already at/under it, the fill is now and better
            if (mkt - entry) * s <= 0:
                p.order_type, p.entry = "MARKET", mkt
                p.notes.append(f"limit {entry} already reached - filled at market {mkt}")
            else:
                p.order_type, p.entry = "LIMIT", entry
        else:  # STOP
            if (entry - mkt) * s <= 0:
                if abs(mkt - entry) / entry <= tol:
                    p.order_type, p.entry = "MARKET", mkt
                    p.notes.append(f"stop {entry} already triggered - filled at market {mkt}")
                else:
                    p.reason = f"stop entry {entry} was passed long ago (price {mkt})"
                    return p
            else:
                p.order_type, p.entry = "STOP", entry

    # ── stop loss ──
    sl = sig.get("sl")
    if sl is None:
        if rules.get("missing_sl") == "default":
            sl = p.entry * (1 - s * float(rules.get("default_sl_pct") or 0.5) / 100.0)
            p.notes.append(f"no stop in the message - default stop {sl:.5g}")
        else:
            p.reason = "no stop loss in the message"
            return p
    sl = float(sl)
    if (sl - p.entry) * s >= 0:
        p.reason = f"stop {sl} is already beyond the entry {p.entry} - signal is stale"
        return p
    p.sl = sl

    # ── targets ──
    tps = [float(t) for t in (sig.get("tps") or []) if (float(t) - p.entry) * s > 0]
    passed = [float(t) for t in (sig.get("tps") or []) if (float(t) - p.entry) * s <= 0]
    if passed and rules.get("skip_if_tp1_passed", True) and p.order_type == "MARKET":
        p.reason = f"price {p.entry} is already beyond TP1 {passed[0]}"
        return p
    if passed:
        p.notes.append(f"dropped targets already passed: {passed}")
    tps = tps[: int(rules.get("max_legs") or 10)]
    targets: list[float | None] = list(tps)
    if sig.get("tp_open") and len(targets) < int(rules.get("max_legs") or 10):
        targets.append(None)
    if not targets:
        rr = float(rules.get("missing_tp_rr") or 0)
        targets = [p.entry + s * rr * abs(p.entry - sl)] if rr > 0 else [None]
        if rr > 0:
            p.notes.append(f"no target in the message - TP at {rr}R")

    # ── size ──
    vmin = spec.get("volume_min") or 0.01
    lpl = loss_per_lot(p.entry, sl, spec)
    if rules.get("risk_mode") == "fixed_lots":
        total = float(rules.get("fixed_lots") or vmin)
    else:
        if lpl <= 0:
            p.reason = f"cannot size: no tick value for {broker_symbol}"
            return p
        budget = balance * float(rules.get("risk_pct") or 0) / 100.0
        total = budget / lpl
        if total < vmin:
            cap = balance * float(rules.get("min_lot_max_risk_pct") or 0) / 100.0
            if rules.get("allow_min_lot", True) and vmin * lpl <= cap:
                p.notes.append(f"risk budget ${budget:.2f} is below one min lot (${vmin*lpl:.2f}) - trading the min lot")
                total = vmin
            else:
                p.reason = (f"risk budget ${budget:.2f} buys {total:.4f} lots, below the minimum {vmin} "
                            f"(min lot risks ${vmin*lpl:.2f})")
                return p
    vmax = spec.get("volume_max") or 0
    if vmax and total > vmax * len(targets):
        total = vmax * len(targets)
    vols = split_volume(total, _weights_for(len(targets), rules), spec)
    if not vols:
        p.reason = f"{total:.4f} lots is below the minimum {vmin}"
        return p
    if len(vols) < len(targets):
        p.notes.append(f"volume covers {len(vols)} of {len(targets)} targets at the min lot - kept the nearest")
    p.legs = [{"leg": i + 1, "volume": v, "tp": targets[i]} for i, v in enumerate(vols)]
    p.risk_dollars = round(sum(vols) * lpl, 2)
    p.ok = True
    return p


def _size_legs(p: OpenPlan, entries: list[float], targets: list, sl: float, spec: dict,
               balance: float, rules: dict) -> bool:
    """Volume per leg in proportion to the weights, total sized so the summed
    risk to the stop is the budget. Drops the furthest legs if the minimum lot
    does not fit them all. Fills p.legs / p.risk_dollars; False + p.reason if
    nothing fits."""
    vmin = spec.get("volume_min") or 0.01
    n = len(targets)
    while n > 0:
        w = _weights_for(n, rules)
        sw = sum(w) or 1.0
        lpls = [loss_per_lot(entries[i], sl, spec) for i in range(n)]
        if any(x <= 0 for x in lpls):
            p.reason = f"cannot size: no tick value for {p.symbol}"
            return False
        avg_lpl = sum(w[i] / sw * lpls[i] for i in range(n))
        if rules.get("risk_mode") == "fixed_lots":
            total = float(rules.get("fixed_lots") or vmin)
        else:
            budget = balance * float(rules.get("risk_pct") or 0) / 100.0
            total = budget / avg_lpl
            if total < vmin * n and n == 1 and rules.get("allow_min_lot", True) \
                    and vmin * lpls[0] <= balance * float(rules.get("min_lot_max_risk_pct") or 0) / 100.0:
                p.notes.append(f"risk budget below one min lot - trading the min lot")
                total = vmin
        vols = split_volume(total, w, spec)
        if len(vols) == n and rules.get("risk_mode") != "fixed_lots":
            # never above the budget: take a step off the legs that risk most per lot
            step = spec.get("volume_step") or vmin
            budget = balance * float(rules.get("risk_pct") or 0) / 100.0
            while sum(vols[i] * lpls[i] for i in range(n)) > budget * 1.001:
                cands = [i for i in range(n) if vols[i] - step >= vmin - 1e-12]
                if not cands:
                    break
                i = max(cands, key=lambda j: lpls[j])
                vols[i] = round(vols[i] - step, 8)
        if len(vols) == n:
            p.legs = [{"leg": i + 1, "volume": vols[i], "tp": targets[i], "entry": round(entries[i], int(spec.get("digits") or 5)),
                       "kind": p.legs_kind[i] if hasattr(p, "legs_kind") else p.order_type} for i in range(n)]
            p.risk_dollars = round(sum(vols[i] * lpls[i] for i in range(n)), 2)
            if n < len(targets):
                p.notes.append(f"volume covers {n} of {len(targets)} targets at the min lot - kept the nearest")
            return True
        n -= 1
    p.reason = f"risk budget is below the minimum lot {vmin}"
    return False


def _layer_plan(p: OpenPlan, sig: dict, mkt: float, s: int, lo: float, hi: float, spec: dict,
                balance: float, rules: dict) -> OpenPlan:
    """Limit orders spread through the entry zone. Leg 1 (TP1) gets the level
    price reaches first (the zone's near edge), the last leg the deepest - the
    "worst entries take the first targets, best entries run" way signal
    providers who post zones manage them. Levels price has already reached
    fill at market now (at a better price than written)."""
    buy = s == 1
    near, far = (hi, lo) if buy else (lo, hi)
    sl = sig.get("sl")
    if sl is None:
        if rules.get("missing_sl") == "default":
            sl = far * (1 - s * float(rules.get("default_sl_pct") or 0.5) / 100.0)
            p.notes.append(f"no stop in the message - default stop {sl:.5g}")
        else:
            p.reason = "no stop loss in the message"
            return p
    sl = float(sl)
    if (sl - far) * s >= 0:
        p.reason = f"stop {sl} is not beyond the zone {lo}-{hi}"
        return p
    if (sl - mkt) * s >= 0:
        p.reason = f"price {mkt} is already beyond the stop {sl} - signal is stale"
        return p
    p.sl = sl
    tps = [float(t) for t in (sig.get("tps") or []) if (float(t) - near) * s > 0]
    max_legs = int(rules.get("max_legs") or 10)
    targets: list = tps[:max_legs]
    if sig.get("tp_open") and len(targets) < max_legs:
        targets.append(None)
    if not targets:
        rr = float(rules.get("missing_tp_rr") or 0)
        targets = [near + s * rr * abs(near - sl)] if rr > 0 else [None]
    n = len(targets)
    levels = [near + (far - near) * (i / (n - 1) if n > 1 else 0.0) for i in range(n)]
    kinds, entries = [], []
    for lv in levels:
        if (mkt - lv) * s <= 0:            # price is at/through this level: take it now, at the better price
            kinds.append("MARKET")
            entries.append(mkt)
        else:
            kinds.append("LIMIT")
            entries.append(lv)
    # a market leg whose target is already behind the price would close at a loss of the spread: make it a limit
    for i, t in enumerate(targets):
        if kinds[i] == "MARKET" and t is not None and (t - mkt) * s <= 0:
            kinds[i], entries[i] = "LIMIT", levels[i]
    p.legs_kind = kinds  # type: ignore[attr-defined]
    p.order_type = "LAYER" if len(set(kinds)) > 1 or kinds[0] == "LIMIT" else "MARKET"
    p.entry = entries[0]
    if not _size_legs(p, entries, targets, sl, spec, balance, rules):
        return p
    filled_now = sum(1 for k in kinds[: len(p.legs)] if k == "MARKET")
    p.notes.append(f"zone {lo:g}-{hi:g}: {len(p.legs)} levels, {filled_now} at market now, "
                   f"{len(p.legs) - filled_now} limit orders")
    p.ok = True
    return p


MAX_BE_OFFSET_PCT = 0.1


def breakeven_price(entry: float, side: str, rules: dict) -> float:
    # capped: the offset only has to cover spread + commission. A value like 2
    # (meant as pips or $) would put a gold "breakeven" stop $80 past the entry,
    # on the wrong side of price, and the broker would refuse every one.
    off = min(max(float(rules.get("be_offset_pct") or 0), 0.0), MAX_BE_OFFSET_PCT) / 100.0
    return entry * (1 + off) if side == "BUY" else entry * (1 - off)


def trail_target(level: int, entry: float, side: str, tps: list, rules: dict, ladder: bool) -> float | None:
    """Where the stop of a leg opened at `entry` belongs once the signal's TP`level`
    has been reached: breakeven at TP1 (or at be_after_tp without the ladder),
    then the previous target with the ladder. None = nothing to do yet."""
    s = 1 if side == "BUY" else -1
    be_after = int(rules.get("be_after_tp") or 0)
    if ladder:
        if level < 1:
            return None
        if level == 1 or not tps:
            return breakeven_price(entry, side, rules)
        t = float(tps[min(level - 2, len(tps) - 1)])
        return t if (t - entry) * s > 0 else breakeven_price(entry, side, rules)
    if be_after and level >= be_after:
        return breakeven_price(entry, side, rules)
    return None


def stop_fits(sl: float, side: str, quote: dict, spec: dict | None) -> bool:
    """A protective stop must sit on the losing side of the price it closes at,
    at least the broker's stops level away (MT5 refuses it otherwise)."""
    s = 1 if side == "BUY" else -1
    px = float(quote["bid"] if side == "BUY" else quote["ask"])
    gap = float((spec or {}).get("stops_level") or 0) * float((spec or {}).get("point") or 0)
    return (px - sl) * s > gap


def legs_to_close(legs: list[dict], fraction: float) -> list[tuple[int, float]]:
    """Which legs (and how much of each) to close to take `fraction` of the open
    volume off, nearest TP first. legs: [{"ticket", "volume", "leg"}] (open ones).
    Returns [(ticket, volume)]; a volume equal to the leg's closes it whole."""
    total = sum(l["volume"] for l in legs)
    want = total * max(0.0, min(1.0, fraction))
    out: list[tuple[int, float]] = []
    for l in sorted(legs, key=lambda x: x.get("leg", 0)):
        if want <= 1e-9:
            break
        take = min(l["volume"], want)
        out.append((l["ticket"], round(take, 8)))
        want -= take
    return out
