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
    also_breakeven: bool = False             # CLOSE that also says "and set breakeven" ("closed half & set BE")
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
    zone = re.search(r"(?:ENTRY|ENTER|@|AT|PRICE|ZONE|BUY|SELL|NOW|LIMIT)\b(?:(?!\b(?:SL|S/L|STOP|TP\d*|T/P|TARGETS?|TAKE)\b)[^\d]){0,24}?" + NUM + r"\s*(?:-|–|/|TO|~)\s*" + NUM, up)
    if zone:
        a, b = _num(zone.group(1)), _num(zone.group(2))
        lo, hi = min(a, b), max(a, b)
        if hi / max(lo, 1e-9) < 1.05:
            # a buy fills at the top of its zone first, a sell at the bottom
            return (hi if side == "BUY" else lo), lo, hi
    single = re.search(r"(?:ENTRY|ENTER|@|AT|PRICE|OPEN)\s*[:=]?\s*" + NUM, up)
    if not single:
        single = re.search(r"\b(?:BUY|SELL|LONG|SHORT)\b(?:(?!\b(?:SL|S/L|STOP|TP\d*|T/P|TARGETS?|TAKE)\b)[^\d]){0,24}?" + NUM, up)
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
    # "TP4 Open (4504/4507/4512/4518)": the bracketed prices are TP4, TP5, ...
    mo = re.search(r"\bTP\s*(\d{1,2})?\s*[:=]?\s*OPEN\s*\(([^)]*)\)", up)
    if mo:
        k0 = int(mo.group(1)) if mo.group(1) else (max(found) + 1 if found else 1)
        for j, x in enumerate(re.findall(NUM, mo.group(2))):
            found.setdefault(k0 + j, _num(x))
    tps = [found[k] for k in sorted(found)]
    if not tps:
        # "TP: 4021 TP: 4019 TP. 4017 TP: open" - one unindexed TP per target
        rep = [_num(x) for x in re.findall(r"\bT\.?P\s*[:.=]\s*" + NUM, up)]
        if len(rep) >= 2:
            tps = rep
    if not tps:
        # "TP 2350 / 2340 / 2330", "Targets: 2350, 2340" - prices of 3+ digits, not "TP7 ✅"
        m = re.search(r"\b(?:TP|T/P|TAKE\s*PROFITS?|TARGETS?)(?!\s*\d{1,2}\b)\s*[:=@]?\s*((?:\d{3,7}(?:[.,]\d{1,6})?\s*(?:/|,|-|\||&|AND)?\s*)+)", up)
        if m:
            tps = [_num(x) for x in re.findall(NUM, m.group(1))]
    tp_open = bool(re.search(r"\bTP\s*\d*\s*[:.=]?\s*OPEN\b", up)) and not mo
    return tps[:10], tp_open


_FUTURE = re.compile(r"\b(I'LL|I WILL|WILL|WE'LL|GOING TO|GONNA)\b")
_CONDITIONAL = re.compile(r"^\W*(IF|WHEN|ONCE|AS SOON AS|AS PRICE GOES|AFTER|AT TP\s*\d|AT TAKE ?PROFIT|UNTIL)\b|\bTP\s*\d\s+TO\s+(CLOSE|TAKE|SET)\b|\b(IF|ONCE|AS SOON AS)\b.{0,40}\b(HIT|HITS|REACH|BREAK|GOES|PUSH)")
# "I'm going to close my entries" is what he is doing now - unless the clause
# also says when ("I'm going to close half at TP3", "...if we break 3300").
_IMMINENT = re.compile(r"\b(I'?M|I AM|WE'?RE|WE ARE)\s+(GOING TO|GONNA)\b")
_DEFER = re.compile(r"\b(AT|IF|ONCE|WHEN|AFTER|BEFORE|LATER|TOMORROW|TONIGHT|NEXT|SOON|UNTIL|MONDAY|TUESDAY|"
                    r"WEDNESDAY|THURSDAY|FRIDAY|WEEK)\b|\bTP\s*\d|\bIN \d+\s*(MIN|HOUR)|\bWILL\b|\bI'LL\b")
_NEGATED = re.compile(r"\b(NOT|DON'?T|DONT|DO NOT|NEVER|NO NEED TO|WON'?T|WOULDN'?T|I'?M NOT)\b\W*(\w+\W+){0,2}?(CLOSE|CLOSING|ADJUST\w*|MOVE|MOVING|CANCEL\w*|EXIT|SET)")
BE_WORD = r"(?:B/?E|BRE?A?K\s*-?\s*E?VEN\w*|BREKAEVEN\w*|BREAKEVE\w*)"


def _sentences(up: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<!\d)\.|\.(?!\d)|[!?\n;]|💡|📌|🎯|🤝|💯|🏆|👇|🤑|🚀|💰|🙏🏽|👍🏽|💪🏽|👊🏽|💦|🔥", up) if x.strip()]


def _clauses(sentence: str) -> list[str]:
    return [x.strip() for x in re.split(r",|&|\bAND\b|\s-\s|\bTHEN\b|\bBUT\b", sentence) if x.strip()]


def _acting_now(up: str) -> str:
    """The parts of a message that describe something to do NOW. A sentence
    that opens with a condition ("once TP1 hits...", "at TP3 I'll...") is
    dropped whole; inside the rest, a clause in the future tense ("will
    update", "I'll close") is dropped unless it also says NOW ("I'll close
    all now", "will set breakeven now")."""
    keep = []
    for sent in _sentences(up):
        if _CONDITIONAL.search(sent):
            continue
        for cl in _clauses(sent):
            if _FUTURE.search(cl) and not re.search(r"\bNOW+\b|\bRIGHT AWAY\b|\bIMMEDIATELY\b", cl) \
                    and not (_IMMINENT.search(cl) and not _DEFER.search(cl)):
                continue
            keep.append(cl)
    return " | ".join(keep)


def _management(up: str) -> ParsedSignal | None:
    """Follow-ups, in priority order. Only what is being done NOW acts (see
    _acting_now); results, progress reports and "breakeven got hit" are
    information; "not close", "I'm not adjusting" never act."""
    p = ParsedSignal()
    now = _acting_now(up)

    # trade-tracking bots (Cornix etc.) and results recaps: never an instruction
    if re.search(r"#\w+/USDT|CLOSED AT STOPLOSS|CLOSED DUE TO OPPOSITE|AVERAGE ENTRY PRICE|ALL TARGETS ACHIEVED|"
                 r"ALL ENTRIES ACHIEVED|TAKE-PROFIT TARGET \d", up):
        p.kind = "INFO"
        return p
    if re.match(r"^\s*(OVER\s*\$|\$\s*\d)", up) or re.search(
            r"PROFIT TARGETS HIT|TAKE PROFITS HIT|TP TARGETS HIT|PIPS\s*(SMASHED|SECURED)|\bTHIS (MONTH|WEEK) SO FAR", up):
        p.kind = "INFO"
        m = re.search(r"\bTP\s*(\d{1,2})\b", up)
        p.tp_index = int(m.group(1)) if m else None
        return p

    # something that already happened to the positions: "breakeven (got) hit", "some entries hit breakeven",
    # "SL hit", "tapped me out", "out on breakeven" - the broker already did it
    already = re.search(BE_WORD + r"\s*(GOT\s*|HAS\s*|WAS\s*|JUST\s*)?(HIT|TAKEN|TRIGGERED)|"
                        r"\b(HIT|HITS|HITTING|ABOUT TO HIT|WILL HIT|OUT ON|OUT AT|WICKED ME OUT ON)\s+(MY\s+|THE\s+|ON\s+)?" + BE_WORD + r"|"
                        r"\b(SL|STOP\s*LOSS|STOP)\s*(GOT\s*|WAS\s*)?(HIT|TAKEN|TRIGGERED)\b|"
                        r"\b(REACHED|HIT|TAPPED ME OUT)\b[^|]{0,20}\b(SL|STOP)\b|CLOSED THE TRADES ITSELVES", up)
    instruct_be = re.search(r"\b(SET|SETTING|PUT|MOVE|MOVING)\b[^|]{0,20}" + BE_WORD, now)
    if already and not instruct_be:
        p.kind = "INFO"
        return p

    m = re.search(r"\bTP\s*(\d{1,2})\s*(?:HIT|DONE|REACHED|SMASHED|ACHIEVED|CHECK\w*|✅)", up) or \
        re.search(r"\bTAKE\s*PROFIT\s*(\d{1,2})\s*(?:HIT\w*|DONE|REACHED|SMASH\w*|CHECK\w*|✅)", up)
    tp_info = int(m.group(1)) if m else None

    # pending orders: cancelling / closing / adjusting / removing LIMIT or STOP ORDERS
    # touches only unfilled orders - "closing buy limit orders" must never close a position
    if re.search(r"\b(CANCEL\w*|CLOS\w*|ADJUST\w*|REMOV\w*|DELET\w*|PULL\w*)\b[^|]{0,20}?\b(BUY|SELL)?\s*(LIMIT|STOP|PENDING)\s*ORDERS?\b", now) \
            or re.search(r"\b(CANCEL|DELETE|REMOVE)\b.*\b(ORDER|LIMIT|PENDING|SIGNAL|ENTRY|SETUP|IT)\b|\b(CANCEL|DELETE)\b\s*$", now):
        p.kind = "CANCEL"
        return p
    if re.search(r"\bSTILL HOLDING\b|\bHOLDING (MY )?(BUY|SELL) LIMIT|LIMIT ORDERS? READY|\bI'M LIVE\b|REACHED MY ZONE", up):
        p.kind = "INFO"
        return p

    # a new stop level stated wins over everything below ("moving SL to 4148 ok cause I closed many entries")
    if not re.search(r"\b(NOT|NO|WON'?T|WILL NOT)\b[^|]{0,15}\b(ADJUST\w*|MOVE|MOVING)\b", up):
        m = re.search(r"\b(?:MOVE|MOVING|SET|SETTING|NEW|ADJUST\w*|CHANGE|CHANGING|UPDATE|PUT|TRAIL)\b[^\d|]{0,45}?\b(?:SL|S/L|STOP\s*LOSS|STOPS?)\b[^\d]{0,60}?" + NUM, up) \
            or re.search(r"\b(?:SL|STOP\s*LOSS)\s*(?:MOVED|NOW|UPDATED|CHANGED)?\s*(?:TO|AT|:|=)\s*" + NUM, up) \
            or re.search(r"\bLAST\s+(?:SL\s+)?ADJUST\w*\b[^\d]{0,30}?" + NUM, up)
        if m and 100 <= _num(m.group(1)):
            p.kind, p.new_price = "MOVE_SL", _num(m.group(1))
            return p

    # closing positions, all or part. Explicit amounts only: "close all", "closing all entries", "I'm out of
    # this trade", "close/closed half", "take half profits", "take 60%", "close worst / top entries",
    # "closed most". "Take some profits" names no amount: advice (the TP legs already take profit).
    # Past-tense reports with no amount ("$12,000 closed already", "closed nice profit") are not orders.
    if not _NEGATED.search(up):
        frac = 0.0
        if re.search(r"\bI'?M OUT (OF )?(THIS|THE|MY|ALL) (SETUP|TRADE|TRADES|POSITION|POSITIONS|ENTRIES)", now) or \
                re.search(r"\b(CLOS(E|ING)|EXIT\w*)\b\s*(ALL|EVERYTHING|THE TRADE|THIS TRADE|ALL (MY |THE )?(ENTRIES|POSITIONS|TRADES)|(MY|OUR) (ENTRIES|POSITIONS|TRADES))\b(?!\s+(TOP|WORST|BOTTOM|BEST|BAD)\b)", now) or \
                re.search(r"\b(CLOSE|CLOSING)\b\s*(IT|NOW|EVERYTHING)?\s*(NOW)?\s*$", now):
            frac = 1.0
        elif (m2 := re.search(r"\b(CLOS\w*|TAKE|TAKING|TOOK|SECURE\w*|BOOK\w*)\b[^|]{0,25}?\b(HALF|(\d{2})\s*%)", now)):
            frac = 0.5 if m2.group(2) == "HALF" else int(m2.group(3)) / 100.0
        elif re.search(r"\b(CLOS\w*)\b[^|]{0,15}\b(WORST|TOP|BOTTOM|BAD)\s+(ENTR\w*|POSITIONS?)", now):
            frac = 0.5
        elif re.search(r"\bCLOS\w*\b[^|]{0,10}\bMOST\b", now):
            frac = 0.75
        if frac > 0:
            p.kind, p.close_fraction = "CLOSE", frac
            p.also_breakeven = frac < 1.0 and bool(re.search(BE_WORD + r"|ZERO RISK|RISK[\s-]*FREE|NO (MORE )?RISK", now))
            if re.search(r"\bALL\b.{0,10}\b(TRADES|POSITIONS|ORDERS)\b", now):
                p.applies_to = "all"
            return p

    # breakeven / zero risk: "set breakeven", "breakeven set", "SET BREAKEVENNNN", "zero risk", "risk free",
    # "reduce risk to zero", "move your SL above your entries", "SL to entry"
    be = re.search(
        r"\b(SL|SLS|STOP|STOPS|STOP\s*LOSS(ES)?|POSITIONS?|ALL|ENTRIES|TRADES?|SET|SETTING|PUT|MOVE|MOVING|MOVED|FULLY|FULL)\b[^|]{0,25}?"
        r"(" + BE_WORD + r"|\bENTRY\b|\bOPEN(?:ING)?\s*PRICE\b)"
        r"|" + BE_WORD + r"\s*(IS\s*|FULLY\s*|NOW\s*)?(SET|ON|ACTIVE|DONE)\b"
        r"|\b(SET|SETTING)\s+" + BE_WORD +
        r"|" + BE_WORD + r"\s+ON\s+ALL\b|\bRISK[\s-]*FREE\b|\bZERO+\s*RISK\w*|\bREDUCE\s*(THE\s*)?RISK\b[^|]{0,15}\bZERO\b"
        r"|\bNO (MORE )?RISK\b|\bSL\b[^|]{0,15}\b(ABOVE|BELOW)\b[^|]{0,15}\bENTR\w*"
        r"|\bMAKE SURE\b[^|]{0,30}" + BE_WORD + r"|^\W*" + BE_WORD + r"\W*(NOW+)?\W*$"
        r"|\b(ON|AT|RUNNING|ABOVE|BELOW)\s+" + BE_WORD, now)
    if be and not _NEGATED.search(now):
        p.kind = "BREAKEVEN"
        return p

    m = re.search(r"\b(?:MOVE|SET|NEW|ADJUST|ADJUSTING|CHANGE|UPDATE)\b[^|]{0,15}\bTP\s*(\d{1,2})?\b[^|]{0,12}?" + NUM, now)
    if m:
        p.kind, p.new_price = "MOVE_TP", _num(m.group(2))
        p.tp_index = int(m.group(1)) if m.group(1) else None
        return p

    if tp_info is not None:
        p.kind, p.tp_index = "INFO", tp_info
        return p
    if already:
        p.kind = "INFO"
        return p
    return None


def parse_message(text: str) -> ParsedSignal:
    if not text or not text.strip():
        return ParsedSignal()
    up = text.upper().replace("—", "-").replace("–", "-").replace("’", "'").replace("‘", "'")
    # "4495Stop Loss 4483TP1 4497TP2" (line breaks lost when copied): split number|word
    up = re.sub(r"(\d)([A-Z])", r"\1 \2", up)
    up = " ".join(up.split())
    up = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", up) if re.search(r"\d \d{3}\b", up) else up
    symbol = find_symbol(up)
    side, otype = _side_and_type(up)
    sl = _sl(up)
    tps, tp_open = _tps(up)
    # An OPEN names a side and at least a stop or a target; otherwise it is a
    # follow-up (or chat).
    if side and (sl is not None or tps) and not re.search(
            r"\b(CLOS\w*|CANCEL\w*|ADJUST\w* (BUY|SELL)|HIT|SMASHED)\b|^\s*OVER\s*\$", up):
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
