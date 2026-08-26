"""
backend/strategies/core/markings.py

[V1 / Phase 13 §C.6] Chart-marking vocabulary.

Why this exists: `utils/trade_grouper.py:316` has always read
`signal.metadata["markings"]` to build `smc_data.boxes` / `smc_data.markers` for
the trade chart — and no strategy has ever written that key. A grep for
"markings" across `backend/` returns three hits, all inside trade_grouper
itself. Every backtest chart has therefore rendered entry/SL/TP lines and
nothing else: no FVG boxes, no key levels, no structure markers, no confluences.

This module gives every strategy one vocabulary for saying "here is the thing I
actually looked at, and here is the value I actually measured", at the moment it
looked at it.

The discipline that makes this worth trusting (Visualization plan §2,
"Visualization is read-only"): a Marking is emitted by the same code path that
made the decision, carrying the numbers that decision used. Nothing here
recomputes geometry at render time. If a marking disagrees with the trade that
followed it, that is a strategy bug being made visible — which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Kinds ────────────────────────────────────────────────────────────────
# Box-shaped (need top + bottom). trade_grouper routes these to smc_data.boxes.
KIND_FVG = "FVG"            # fair value gap / imbalance
KIND_OB = "OB"              # order block
KIND_ZONE = "ZONE"          # generic zone: OTE/premium-discount/retest area
KIND_RANGE = "RANGE"        # CRT range, ORB box, session box

# Line-shaped (need price). Rendered as horizontal levels.
KIND_LEVEL = "LEVEL"        # key level, neckline, session open, VWAP band
KIND_LIQUIDITY = "LIQUIDITY"  # buy/sell-side liquidity pool (a level with intent)

# Point-shaped (need time, price optional). Rendered as bar markers.
KIND_STRUCTURE = "STRUCTURE"  # BOS / CHoCH / sweep / displacement candle
KIND_NOTE = "NOTE"            # anything else worth annotating on the bar

BOX_KINDS = frozenset({KIND_FVG, KIND_OB, KIND_ZONE, KIND_RANGE})
LINE_KINDS = frozenset({KIND_LEVEL, KIND_LIQUIDITY})
POINT_KINDS = frozenset({KIND_STRUCTURE, KIND_NOTE})

# ── Roles ────────────────────────────────────────────────────────────────
# What part this marking played in the decision. Drives colour/weight in the UI
# and, more usefully, lets you filter a chart down to "show me only what was
# REQUIRED for this entry".
ROLE_TRIGGER = "trigger"            # the condition that fired the entry
ROLE_CONFLUENCE = "confluence"      # a supporting condition that was required
ROLE_CONTEXT = "context"            # informational; did not gate the entry
ROLE_INVALIDATION = "invalidation"  # the level that would kill this setup

VALID_ROLES = frozenset({ROLE_TRIGGER, ROLE_CONFLUENCE, ROLE_CONTEXT, ROLE_INVALIDATION})

# Default fill colours per kind, as rgba strings the chart primitive consumes
# directly. A strategy can override per-marking when it needs to distinguish
# (e.g. bullish vs. bearish FVG).
_DEFAULT_COLORS = {
    KIND_FVG: "rgba(59, 130, 246, 0.16)",       # blue
    KIND_OB: "rgba(168, 85, 247, 0.16)",        # purple
    KIND_ZONE: "rgba(234, 179, 8, 0.15)",       # amber
    KIND_RANGE: "rgba(148, 163, 184, 0.12)",    # slate
    KIND_LEVEL: "rgba(148, 163, 184, 0.85)",
    KIND_LIQUIDITY: "rgba(239, 68, 68, 0.85)",  # red — liquidity is a target
    KIND_STRUCTURE: "rgba(16, 185, 129, 0.95)",  # green
    KIND_NOTE: "rgba(148, 163, 184, 0.75)",
}


@dataclass
class Marking:
    """
    One thing a strategy looked at, in chart coordinates.

    Geometry is kind-dependent and validated in `to_dict`:
      - BOX_KINDS   need `top` + `bottom` + `start_time`
      - LINE_KINDS  need `price`
      - POINT_KINDS need `start_time`

    `detail` carries the actual measured values behind the marking — the Fib
    level, the ATR multiple, the measured displacement in pips. It is what the
    chart shows on hover, and it is the difference between "there was an FVG
    here" and "there was a 4.2-pip H4 FVG here, 1.8x the ATR gate of 2.3".
    """

    kind: str
    label: str
    timeframe: str
    start_time: int
    role: str = ROLE_CONTEXT
    end_time: int | None = None      # None = extend to the chart's right edge
    top: float | None = None
    bottom: float | None = None
    price: float | None = None
    color: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialise for `signal.metadata["markings"]`.

        Keys `type`, `top`, `bottom`, `start_time`, `end_time`, `timeframe` and
        `color` are named to match what `trade_grouper.py` and
        `TradeChart.jsx` already consume, so existing consumers keep working
        without a translation layer. `text` is the marker caption
        TradeChart reads for STRUCTURE markers.
        """
        d: dict[str, Any] = {
            # `type` (not `kind`) — trade_grouper filters on m["type"].
            "type": self.kind,
            "kind": self.kind,
            "label": self.label,
            "text": self.label,
            "timeframe": self.timeframe,
            "start_time": int(self.start_time),
            "end_time": int(self.end_time) if self.end_time is not None else None,
            "role": self.role if self.role in VALID_ROLES else ROLE_CONTEXT,
            "color": self.color or _DEFAULT_COLORS.get(self.kind, "rgba(148,163,184,0.2)"),
            "detail": _jsonable(self.detail),
        }
        if self.top is not None:
            d["top"] = float(self.top)
        if self.bottom is not None:
            d["bottom"] = float(self.bottom)
        if self.price is not None:
            d["price"] = float(self.price)
            # A line marking is also a degenerate box, so a renderer that only
            # knows how to draw boxes still places it correctly.
            d.setdefault("top", float(self.price))
            d.setdefault("bottom", float(self.price))
        # `time` is what marker renderers key on for point kinds.
        d["time"] = int(self.start_time)
        return d

    def is_valid(self) -> bool:
        """Geometry completeness check — an incomplete marking is dropped, not drawn."""
        if self.kind in BOX_KINDS:
            return self.top is not None and self.bottom is not None
        if self.kind in LINE_KINDS:
            return self.price is not None
        return True


def _jsonable(value: Any) -> Any:
    """
    Coerce numpy/pandas scalars to plain Python so the whole marking survives
    `json.dumps` in runner.py / backtest.py without a custom encoder. Signals
    routinely carry numpy floats straight out of a DataFrame slice.
    """
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    # numpy scalar / pandas Timestamp / anything else
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return str(value)


class MarkingCollector:
    """
    Accumulator a strategy fills while it evaluates a bar.

    Usage inside `on_bar`:

        mk = MarkingCollector(timeframe)
        mk.level("Asia High", asia_high, t, role=ROLE_CONFLUENCE,
                 detail={"session": "ASIA", "bars": n})
        ...
        return TradeSignal(..., metadata={..., **mk.as_metadata()})

    Deliberately forgiving: a strategy that bails out mid-evaluation just never
    calls `as_metadata()`, and the collector is discarded. Collecting is cheap
    (a list append), so it is safe to collect before knowing whether a signal
    will fire — which matters, because the interesting markings are the ones
    evaluated *before* the entry decision.
    """

    __slots__ = ("timeframe", "_items", "_cap")

    def __init__(self, timeframe: str, cap: int = 64):
        self.timeframe = timeframe
        self._items: list[Marking] = []
        # Hard cap so a pathological loop can't inflate a signal's metadata to
        # the point where it bloats the saved run. 64 is far above what any
        # current strategy emits (the richest, APA, emits ~9).
        self._cap = cap

    # ── generic ──
    def add(self, marking: Marking) -> Marking:
        if len(self._items) < self._cap and marking.is_valid():
            self._items.append(marking)
        return marking

    # ── convenience constructors, one per shape ──
    def box(
        self,
        kind: str,
        label: str,
        top: float,
        bottom: float,
        start_time: int,
        end_time: int | None = None,
        role: str = ROLE_CONFLUENCE,
        timeframe: str | None = None,
        color: str | None = None,
        **detail: Any,
    ) -> Marking:
        # Normalise so top is always the higher price — renderers assume it, and
        # strategies pass them in whichever order the pattern produced.
        hi, lo = (top, bottom) if top >= bottom else (bottom, top)
        return self.add(Marking(
            kind=kind, label=label, timeframe=timeframe or self.timeframe,
            start_time=int(start_time), end_time=end_time,
            top=hi, bottom=lo, role=role, color=color, detail=detail,
        ))

    def fvg(self, label: str, top: float, bottom: float, start_time: int, **kw) -> Marking:
        return self.box(KIND_FVG, label, top, bottom, start_time, **kw)

    def order_block(self, label: str, top: float, bottom: float, start_time: int, **kw) -> Marking:
        return self.box(KIND_OB, label, top, bottom, start_time, **kw)

    def zone(self, label: str, top: float, bottom: float, start_time: int, **kw) -> Marking:
        return self.box(KIND_ZONE, label, top, bottom, start_time, **kw)

    def range_box(self, label: str, top: float, bottom: float, start_time: int, **kw) -> Marking:
        return self.box(KIND_RANGE, label, top, bottom, start_time, **kw)

    def level(
        self,
        label: str,
        price: float,
        start_time: int,
        role: str = ROLE_CONFLUENCE,
        kind: str = KIND_LEVEL,
        timeframe: str | None = None,
        end_time: int | None = None,
        color: str | None = None,
        **detail: Any,
    ) -> Marking:
        return self.add(Marking(
            kind=kind, label=label, timeframe=timeframe or self.timeframe,
            start_time=int(start_time), end_time=end_time, price=price,
            role=role, color=color, detail=detail,
        ))

    def liquidity(self, label: str, price: float, start_time: int, **kw) -> Marking:
        kw.setdefault("role", ROLE_CONTEXT)
        return self.level(label, price, start_time, kind=KIND_LIQUIDITY, **kw)

    def structure(
        self,
        label: str,
        start_time: int,
        price: float | None = None,
        role: str = ROLE_TRIGGER,
        timeframe: str | None = None,
        color: str | None = None,
        **detail: Any,
    ) -> Marking:
        return self.add(Marking(
            kind=KIND_STRUCTURE, label=label, timeframe=timeframe or self.timeframe,
            start_time=int(start_time), price=price, role=role, color=color,
            detail=detail,
        ))

    def note(self, label: str, start_time: int, **kw) -> Marking:
        kw.setdefault("role", ROLE_CONTEXT)
        return self.add(Marking(
            kind=KIND_NOTE, label=label, timeframe=kw.pop("timeframe", None) or self.timeframe,
            start_time=int(start_time), price=kw.pop("price", None),
            role=kw.pop("role"), color=kw.pop("color", None), detail=kw,
        ))

    # ── output ──
    def __len__(self) -> int:
        return len(self._items)

    def to_list(self) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self._items]

    def as_metadata(self) -> dict[str, Any]:
        """
        Spread into a TradeSignal's metadata dict.

        Also emits `confluence_summary` — a compact role-keyed list of labels
        that the trade row can show without the caller having to walk the full
        geometry. This is what makes "which confluences did this entry need"
        answerable in a table, not just on a chart.
        """
        items = self.to_list()
        summary: dict[str, list[str]] = {}
        for m in items:
            summary.setdefault(m["role"], []).append(m["label"])
        return {"markings": items, "confluence_summary": summary}


def ts(value: Any) -> int:
    """
    Coerce whatever a strategy has at hand into epoch seconds.

    Strategies variously hold: a pandas Timestamp (the DataFrame index), a
    numpy datetime64, an epoch float from `row["time"]`, or a datetime. All four
    show up in the existing engines, so the collector accepts all four rather
    than making each call site remember which it has.
    """
    if value is None:
        return 0
    # pandas Timestamp / datetime
    tstamp = getattr(value, "timestamp", None)
    if callable(tstamp):
        try:
            return int(tstamp())
        except Exception:
            pass
    # numpy datetime64
    astype = getattr(value, "astype", None)
    if callable(astype):
        try:
            return int(astype("datetime64[s]").astype(int))
        except Exception:
            pass
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0
    # Milliseconds vs. seconds: anything past ~year 2286 in seconds is really ms.
    return int(num / 1000) if num > 1e11 else int(num)
