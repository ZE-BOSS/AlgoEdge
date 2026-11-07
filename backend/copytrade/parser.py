"""
backend/copytrade/parser.py

Turns a Telegram signal-channel message into trading instructions.

Channels write signals in a few dozen house styles; the shapes that recur:

    XAUUSD BUY NOW @ 2350-2347        GOLD SELL LIMIT 2365          #US100 buy
    SL 2340                           SL: 2372                      Entry 27000
    TP1 2355  TP2 2360 ... TP10 2400  TP 2350 / 2340 / 2330         Stop 26900 / Target 27150

and follow-ups, usually as a REPLY to the signal:

    "move SL to entry" / "SL to BE" / "secure the trade"     -> BREAKEVEN
    "move SL to 2352" / "new SL 2352" / "SL 2352"            -> MOVE_SL
    "close now" / "close all" / "exit" / "close half / 50%"  -> CLOSE (all / partial)
    "cancel" / "delete the buy limit" / "remove pending"     -> CANCEL
    "TP1 hit" / "TP2 done 50 pips"                           -> INFO (no action)
    "change TP3 to 2390"                                     -> MOVE_TP

This module is deterministic and has no I/O: the same text always gives the
same result, and every number it returns came out of the message. It does not
guess a stop that is not written - a signal without one is returned as
incomplete and the executor applies the channel's rule for that (skip, or use a
default distance) instead.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Common names -> canonical symbol. The executor maps canonical -> the broker's
# own name per channel (e.g. NAS100 -> "US Tech 100" on Deriv).
SYMBOL_ALIASES: dict[str, str] = {
    "XAUUSD": "XAUUSD", "GOLD": "XAUUSD", "XAU": "XAUUSD", "XAU/USD": "XAUUSD",
    "XAGUSD": "XAGUSD", "SILVER": "XAGUSD",
    "NAS100": "NAS100", "NAS 100": "NAS100", "US100": "NAS100", "US 100": "NAS100", "USTEC": "NAS100",
    "NASDAQ": "NAS100", "NDX": "NAS100", "NDX100": "NAS100", "US TECH 100": "NAS100", "USTECH": "NAS100",
    "US30": "US30", "DOW": "US30", "DJ30": "US30", "WALLSTREET": "US30", "WALL STREET": "US30",
    "US500": "US500", "SPX500": "US500", "SPX": "US500", "S&P500": "US500", "S&P 500": "US500",
    "GER40": "GER40", "DAX": "GER40", "DE40": "GER40", "UK100": "UK100",
    "BTCUSD": "BTCUSD", "BTC": "BTCUSD", "BITCOIN": "BTCUSD", "BTC/USD": "BTCUSD",
    "ETHUSD": "ETHUSD", "ETH": "ETHUSD",
    "USOIL": "USOIL", "WTI": "USOIL", "OIL": "USOIL", "UKOIL": "UKOIL", "BRENT": "UKOIL",
}
_FX = ("EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
for a in _FX:
    for b in _FX:
        if a != b:
            SYMBOL_ALIASES[a + b] = a + b
            SYMBOL_ALIASES[f"{a}/{b}"] = a + b

# Deriv synthetic indices, written many ways ("Boom 1000", "BOOM1000", "V75", "Vol 75")
_SYNTH = [
    (r"BOOM\s*(\d{2,4})", "Boom {} Index"), (r"CRASH\s*(\d{2,4})", "Crash {} Index"),
    (r"(?:VOLATILITY|VOL|V)\s*(\d{1,3})\s*\(?1S\)?", "Volatility {} (1s) Index"),
    (r"(?:VOLATILITY|VOL|V)\s*(\d{1,3})(?!\d)", "Volatility {} Index"),
    (r"JUMP\s*(\d{2,3})", "Jump {} Index"), (r"STEP\s*(?:INDEX)?\s*(\d{3})?", "Step Index {}"),
]

NUM = r"(\d{1,7}(?:[.,]\d{1,6})?)"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


@dataclass
class ParsedSignal:
    """What a message says. `kind` is one of OPEN, BREAKEVEN, MOVE_SL, MOVE_TP,
    CLOSE, CANCEL, INFO, NONE."""
    kind: str = "NONE"
    symbol: str | None = None
    side: str | None = None                  # BUY / SELL
    order_type: str = "MARKET"               # MARKET, LIMIT, STOP
    entry: float | None = None               # single price, or the zone's edge nearest the market
    entry_low: float | None = None
    entry_high: float | None = None
    sl: float | None = None
    tps: list[float] = field(default_factory=list)
    tp_open: bool = False                    # "TP: open" - one leg left without a target
    close_fraction: float | None = None      # CLOSE: 1.0 = all, 0.5 = half
    tp_index: int | None = None              # MOVE_TP / INFO "TP2 hit"
    new_price: float | None = None           # MOVE_SL / MOVE_TP target
    applies_to: str = "reply_or_last"        # reply_or_last / symbol / all
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def complete(self) -> bool:
        """An OPEN with everything needed to place it."""
        return (self.kind == "OPEN" and bool(self.symbol) and self.side in ("BUY", "SELL")
                and self.sl is not None and (self.order_type == "MARKET" or self.entry is not None))


def find_symbol(text: str) -> str | None:
    up = text.upper()
    for pat, fmt in _SYNTH:
        m = re.search(r"(?<![A-Z])" + pat, up)
        if m:
            g = m.group(1) if m.lastindex else None
            if fmt.startswith("Step"):
                return "Step Index" if not g else f"Step Index {g}"
            return fmt.format(g)
    # longest alias first so "US TECH 100" beats "US100", "XAU/USD" beats "XAU"
    cleaned = re.sub(r"[#$]", " ", up)
    for alias in sorted(SYMBOL_ALIASES, key=len, reverse=True):
        if re.search(r"(?<![A-Z0-9])" + re.escape(alias) + r"(?![A-Z0-9])", cleaned):
            return SYMBOL_ALIASES[alias]
    return None


def _side_and_type(up: str) -> tuple[str | None, str]:
    m = re.search(r"\b(BUY|SELL|LONG|SHORT)\s*(LIMIT|STOP)?\b", up)
    if not m:
        return None, "MARKET"
    side = "BUY" if m.group(1) in ("BUY", "LONG") else "SELL"
    return side, (m.group(2) or "MARKET")


def _entry(up: str, side: str | None) -> tuple[float | None, float | None, float | None]:
    zone = re.search(r"(?:ENTRY|ENTER|@|AT|PRICE|ZONE|BUY|SELL|NOW|LIMIT|STOP)\s*[:=]?\s*" + NUM + r"\s*(?:-|–|/|TO|~)\s*" + NUM, up)
    if zone:
        a, b = _num(zone.group(1)), _num(zone.group(2))
        lo, hi = min(a, b), max(a, b)
        if hi / max(lo, 1e-9) < 1.05:
            # a buy fills at the top of its zone first, a sell at the bottom
            return (hi if side == "BUY" else lo), lo, hi
    single = re.search(r"(?:ENTRY|ENTER|@|AT|PRICE|OPEN)\s*[:=]?\s*" + NUM, up)
    if not single:
        single = re.search(r"\b(?:BUY|SELL|LONG|SHORT)\s*(?:LIMIT|STOP|NOW)?\s*[:@=]?\s*" + NUM, up)
    if single:
        v = _num(single.group(1))
        return v, v, v
    return None, None, None


def _sl(up: str) -> float | None:
    m = re.search(r"\b(?:SL|S/L|STOP\s*LOSS|STOPLOSS|STOP)\s*[:=@\-]?\s*" + NUM, up)
    return _num(m.group(1)) if m else None


def _tps(up: str) -> tuple[list[float], bool]:
    found: dict[int, float] = {}
    # the index must be followed by a separator or a space before the price:
    # "TP1 2355", "TP 2: 2360", "TP3=2370" - never "TP 14900" read as TP14 = 900
    for m in re.finditer(r"\b(?:TP|T/P|TAKE\s*PROFIT|TARGET)\s*(\d{1,2})(?:\s*[:=@\-\).]\s*|\s+)" + NUM, up):
        k = int(m.group(1))
        if 1 <= k <= 20:
            found.setdefault(k, _num(m.group(2)))
    tps = [found[k] for k in sorted(found)]
    if not tps:
        m = re.search(r"\b(?:TP|T/P|TAKE\s*PROFITS?|TARGETS?)\s*[:=@]?\s*((?:" + NUM + r"\s*(?:/|,|-|\||&|AND)?\s*)+)", up)
        if m:
            tps = [_num(x) for x in re.findall(NUM, m.group(1))]
    tp_open = bool(re.search(r"\bTP\s*\d*\s*[:=]?\s*OPEN\b", up))
    return tps[:10], tp_open


def _management(up: str) -> ParsedSignal | None:
    p = ParsedSignal()
    if re.search(r"\b(CANCEL|CANCELL?ED|DELETE|REMOVE)\b.*\b(ORDER|LIMIT|STOP|PENDING|SIGNAL|ENTRY|TRADE|IT)\b"
                 r"|\b(CANCEL|DELETE)\b\s*$|\bPENDING\b.*\b(CANCEL|DELETE|REMOVE)", up):
        p.kind = "CANCEL"
        return p
    if re.search(r"\b(SL|STOP\s*LOSS|STOP)\b.{0,25}\b(ENTRY|BE|B/E|BREAK\s*-?\s*EVEN|BREAKEVEN|OPEN(?:ING)?\s*PRICE)\b"
                 r"|\bBREAK\s*-?\s*EVEN\b|\bSECURE\b.{0,20}\b(TRADE|PROFIT|POSITION|ENTRY)|\bRISK\s*FREE\b|\bSET\s*BE\b", up):
        p.kind = "BREAKEVEN"
        return p
    m = re.search(r"\b(?:MOVE|SET|NEW|ADJUST|CHANGE|UPDATE|PUT|TRAIL)\b.{0,15}\b(?:SL|STOP\s*LOSS|STOP)\b.{0,12}?" + NUM, up) \
        or re.search(r"\b(?:SL|STOP\s*LOSS)\s*(?:MOVED|NOW|UPDATED|CHANGED)?\s*(?:TO|AT|:|=)\s*" + NUM, up)
    if m:
        p.kind, p.new_price = "MOVE_SL", _num(m.group(1))
        return p
    m = re.search(r"\b(?:MOVE|SET|NEW|ADJUST|CHANGE|UPDATE)\b.{0,15}\bTP\s*(\d{1,2})?\b.{0,12}?" + NUM, up)
    if m:
        p.kind, p.new_price = "MOVE_TP", _num(m.group(2))
        p.tp_index = int(m.group(1)) if m.group(1) else None
        return p
    if re.search(r"\b(CLOSE|EXIT|TAKE\s*(?:SOME\s*)?PROFITS?|BOOK\s*PROFITS?|SECURE\s*PROFITS?|CLOSING)\b", up) \
            and not re.search(r"\b(TP\s*\d*\s*(HIT|DONE|REACHED|SMASHED))\b", up):
        p.kind = "CLOSE"
        if re.search(r"\b(HALF|50\s*%|PARTIAL(?:S|LY)?|SOME)\b", up):
            m = re.search(r"(\d{1,3})\s*%", up)
            p.close_fraction = (int(m.group(1)) / 100.0) if m else 0.5
        else:
            p.close_fraction = 1.0
        if re.search(r"\bALL\b.{0,10}\b(TRADES|POSITIONS|ORDERS)\b", up):
            p.applies_to = "all"
        return p
    m = re.search(r"\bTP\s*(\d{1,2})\s*(?:HIT|DONE|REACHED|SMASHED|ACHIEVED|✅)", up)
    if m or re.search(r"\b(SL|STOP\s*LOSS)\s*(?:HIT|TAKEN|TRIGGERED)\b", up):
        p.kind = "INFO"
        p.tp_index = int(m.group(1)) if m else None
        return p
    return None


def parse_message(text: str) -> ParsedSignal:
    if not text or not text.strip():
        return ParsedSignal()
    up = " ".join(text.upper().replace("—", "-").replace("–", "-").split())
    up = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", up) if re.search(r"\d \d{3}\b", up) else up
    symbol = find_symbol(up)
    side, otype = _side_and_type(up)
    sl = _sl(up)
    tps, tp_open = _tps(up)
    # An OPEN names a side and at least a stop or a target; otherwise it is a
    # follow-up (or chat).
    if side and (sl is not None or tps) and not re.search(r"\bCLOSE\b|\bCANCEL", up):
        p = ParsedSignal(kind="OPEN", symbol=symbol, side=side, order_type=otype, sl=sl, tps=tps, tp_open=tp_open)
        p.entry, p.entry_low, p.entry_high = _entry(up, side)
        if otype == "MARKET" and p.entry is None:
            p.notes.append("market entry, no price written")
        p.confidence = 0.4 + 0.2 * bool(symbol) + 0.2 * (sl is not None) + 0.2 * bool(tps)
        _sanity(p)
        return p
    m = _management(up)
    if m:
        m.symbol = symbol
        m.side = side
        m.confidence = 0.8
        return m
    return ParsedSignal()


def _sanity(p: ParsedSignal) -> None:
    """Drop numbers that cannot be what the message means: a stop on the wrong
    side of the entry, targets on the wrong side, and order the targets nearest
    first."""
    ref = p.entry
    if ref is None:
        return
    s = 1 if p.side == "BUY" else -1
    if p.sl is not None and (p.sl - ref) * s >= 0:
        p.notes.append(f"stop {p.sl} is on the wrong side of entry {ref} - ignored")
        p.sl = None
    good = [t for t in p.tps if (t - ref) * s > 0]
    if len(good) != len(p.tps):
        p.notes.append("dropped targets on the wrong side of the entry")
    p.tps = sorted(good, key=lambda t: (t - ref) * s)
    if p.order_type == "LIMIT" and p.entry is not None:
        pass
