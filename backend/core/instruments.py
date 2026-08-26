"""
backend/core/instruments.py

Canonical instrument identity, and the per-broker symbol map that resolves it.

The problem this exists to solve (Phase 14 Part C, task 14.9): `SYMBOL_ALIASES`
maps *spellings* — `DAX`, `DE40`, `GER30` all fold to `GER40` — on the implicit
assumption that there is one broker. There is not. **GER40 on FundedNext and
GER30 on Deriv are the same instrument under two brokers**, and 22 of the
profiled symbols are simply not listed by Deriv at all.

Spelling normalisation only ever runs one way: broker symbol -> canonical. The
missing direction is the one configs actually need:

    config names the canonical instrument
        -> the ACTIVE broker's symbol map
            -> the tradeable symbol to send

so a strategy config written against FundedNext runs unchanged on Deriv.

Nothing here duplicates the instrument library. `INSTRUMENT_PROFILES` and
`SYMBOL_ALIASES` in `backend/risk/compounding.py` stay the single source of
truth for canonical names and their specs; this module indexes them and adds
the per-broker layer on top.

Discovery is automatic rather than hand-maintained (Part C.2). Hand-maintaining
a table per broker repeats exactly the mistake the live-profile overlay just
fixed, so on connect we enumerate `mt5.symbols_get()` and match each listing
against the known instruments by normalised name, then confirm with the
contract spec. **Ambiguities are logged and left unresolved rather than
guessed** — a wrong symbol map silently trades the wrong instrument, which is
worse than refusing to resolve.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from backend.utils.logger import get_logger

logger = get_logger(__name__)


# Broker-added decorations on an otherwise standard symbol: `XAUUSD.m`,
# `EURUSDc`, `US30_i`, `NAS100#`, `GER40.a`, `BTCUSDx`, `EUR/USD`.
_SUFFIX_RE = re.compile(r"(\.m|c|_i|#|\.a|x)$", re.IGNORECASE)
# Everything that is decoration rather than identity, for loose matching.
_LOOSE_RE = re.compile(r"[^A-Z0-9]")


def _strip_suffix(symbol: str) -> str:
    return _SUFFIX_RE.sub("", symbol).replace("/", "")


def _loose(symbol: str) -> str:
    """`US SP 500` -> `USSP500`; `Volatility 75 (1s) Index` -> `VOLATILITY751SINDEX`."""
    return _LOOSE_RE.sub("", symbol.upper())


@dataclass
class Instrument:
    """
    One tradeable thing, independent of who lists it.

    `canonical` is the id configs and strategies use, and is exactly the key
    used by `INSTRUMENT_PROFILES` so the two never drift.
    """

    canonical: str
    asset_class: str
    aliases: set[str] = field(default_factory=set)
    #: {broker_id: that broker's symbol}. Populated by discovery, not by hand.
    broker_symbols: dict[str, str] = field(default_factory=dict)

    def symbol_for(self, broker_id: str | None) -> str | None:
        """This broker's symbol, or None if the broker does not list it."""
        if broker_id is None:
            return self.canonical
        return self.broker_symbols.get(broker_id)


@dataclass
class Resolution:
    """One discovery outcome, kept so the UI can explain itself."""

    canonical: str
    broker_symbol: str | None
    available: bool
    reason: str
    #: Set when more than one broker symbol matched and none was chosen.
    ambiguous_with: list[str] = field(default_factory=list)


_registry: dict[str, Instrument] = {}
_alias_index: dict[str, str] = {}      # loose alias -> canonical
_registry_lock = threading.Lock()

# {broker_id: {canonical: Resolution}}
_broker_maps: dict[str, dict[str, Resolution]] = {}
_broker_lock = threading.Lock()


def _build_registry() -> None:
    """
    Index the existing instrument library into canonical Instruments.

    Imported lazily and inside the lock: `compounding` imports a good deal of
    the risk stack, and this module is imported by `data_fetcher`, which it in
    turn imports.
    """
    from backend.risk.compounding import INSTRUMENT_PROFILES, SYMBOL_ALIASES

    reg: dict[str, Instrument] = {}
    for canonical, profile in INSTRUMENT_PROFILES.items():
        reg[canonical] = Instrument(
            canonical=canonical,
            asset_class=profile.instrument_type,
            aliases={canonical},
        )

    for spelling, canonical in SYMBOL_ALIASES.items():
        inst = reg.get(canonical)
        if inst is None:
            # An alias pointing at a name with no profile. Real (the alias table
            # is broader than the profile table); record it so resolution still
            # works, with the asset class unknown.
            inst = reg[canonical] = Instrument(
                canonical=canonical, asset_class="UNKNOWN", aliases={canonical}
            )
        inst.aliases.add(spelling)

    idx: dict[str, str] = {}
    for inst in reg.values():
        for a in inst.aliases:
            key = _loose(a)
            prior = idx.get(key)
            if prior and prior != inst.canonical:
                # Two canonical instruments claiming one spelling. Refuse to
                # pick; log loudly rather than resolve at random.
                logger.warning(
                    f"[INSTRUMENTS] alias '{a}' claimed by both '{prior}' and "
                    f"'{inst.canonical}' — left unresolved"
                )
                continue
            idx[key] = inst.canonical

    _registry.clear()
    _registry.update(reg)
    _alias_index.clear()
    _alias_index.update(idx)
    logger.info(
        f"[INSTRUMENTS] registry built: {len(reg)} instruments, {len(idx)} spellings"
    )


def _ensure_registry() -> None:
    if not _registry:
        with _registry_lock:
            if not _registry:
                _build_registry()


def resolve_canonical(symbol: str) -> str:
    """
    Broker symbol (any spelling, any broker) -> canonical instrument id.

    Returns the input unchanged when nothing matches, so this is safe to apply
    to symbols the library has never heard of.
    """
    _ensure_registry()
    for candidate in (symbol, _strip_suffix(symbol)):
        hit = _alias_index.get(_loose(candidate))
        if hit:
            return hit
    return symbol


def get_instrument(symbol: str) -> Instrument | None:
    """Look up the Instrument for any spelling of it."""
    _ensure_registry()
    return _registry.get(resolve_canonical(symbol))


def broker_id_from_account(company: str | None, server: str | None) -> str:
    """
    A stable id for one broker. Company plus server, because the same company
    lists different symbol sets on demo and live.
    """
    return f"{(company or 'unknown').strip()}|{(server or 'unknown').strip()}"


def _spec_note(canonical: str, info) -> str | None:
    """
    Report — but do not act on — a contract spec that disagrees with the static
    profile.

    Part C.2 suggests confirming a name match against the contract spec. An
    earlier version of this REFUSED matches whose contract size differed by more
    than 10x, and measured against the live Deriv terminal that was wrong in a
    way worth recording: Deriv lists every CFD with `trade_contract_size = 1`,
    so the guard rejected `US Oil`, `UK Brent Oil` and `XCUUSD` — three
    instruments the broker genuinely offers — purely for following a different
    house convention than the static table.

    Refusing is also the more dangerous of the two errors. A false accept is
    corrected downstream, because `get_instrument_profile`'s live overlay
    replaces contract size with the broker's own value whenever MT5 is
    connected; a false reject makes a listed instrument permanently untradeable
    with "not listed" as the only explanation.

    So the discrepancy travels with the resolution as a note instead, and shows
    up in the UI's resolution strip.
    """
    from backend.risk.compounding import INSTRUMENT_PROFILES

    profile = INSTRUMENT_PROFILES.get(canonical)
    if profile is None:
        return None

    broker_cs = getattr(info, "trade_contract_size", None)
    if not broker_cs or not profile.contract_size:
        return None

    ratio = broker_cs / profile.contract_size
    if ratio > 10.0 or ratio < 0.1:
        return (
            f"broker contract size {broker_cs:g} vs profile "
            f"{profile.contract_size:g} ({ratio:.3g}x) — live overlay governs sizing"
        )
    return None


def discover_broker_symbols(broker_id: str, symbols) -> dict[str, Resolution]:
    """
    Match one broker's listings against the canonical registry.

    `symbols` is whatever `mt5.symbols_get()` returned — each element needs
    `.name`, and `.trade_contract_size` / `.path` are used when present.

    Every canonical instrument gets a Resolution, including the ones this broker
    does not list: "not listed by Deriv-Demo" is the answer the pickers need, and
    an absent key would read as "unknown" instead.
    """
    _ensure_registry()

    matches: dict[str, list[str]] = {}
    notes: dict[str, str] = {}
    for info in symbols or []:
        name = getattr(info, "name", None) or str(info)
        canonical = resolve_canonical(name)
        if canonical not in _registry:
            continue
        note = _spec_note(canonical, info)
        if note:
            logger.info(f"[INSTRUMENTS] {broker_id}: '{name}' -> {canonical}: {note}")
            notes[canonical] = note
        matches.setdefault(canonical, []).append(name)

    resolved: dict[str, Resolution] = {}
    for canonical, inst in _registry.items():
        found = matches.get(canonical, [])
        if not found:
            resolved[canonical] = Resolution(
                canonical=canonical,
                broker_symbol=None,
                available=False,
                reason=f"not listed by {broker_id.split('|')[0]}",
            )
            continue
        if len(found) > 1:
            # Prefer an exact-name listing; that is a decision the data supports.
            exact = [n for n in found if _loose(n) == _loose(canonical)]
            if len(exact) == 1:
                chosen, rest = exact[0], [n for n in found if n != exact[0]]
                resolved[canonical] = Resolution(
                    canonical, chosen, True,
                    f"exact name match; also saw {', '.join(rest)}", rest,
                )
                inst.broker_symbols[broker_id] = chosen
                continue
            logger.warning(
                f"[INSTRUMENTS] {broker_id}: {canonical} matched {len(found)} "
                f"listings ({', '.join(found)}) — left unresolved"
            )
            resolved[canonical] = Resolution(
                canonical, None, False,
                f"ambiguous: {len(found)} listings match", list(found),
            )
            continue

        chosen = found[0]
        inst.broker_symbols[broker_id] = chosen
        reason = "exact" if _loose(chosen) == _loose(canonical) else f"listed as {chosen}"
        if canonical in notes:
            reason = f"{reason}; {notes[canonical]}"
        resolved[canonical] = Resolution(canonical, chosen, True, reason)

    with _broker_lock:
        _broker_maps[broker_id] = resolved

    avail = sum(1 for r in resolved.values() if r.available)
    ambig = sum(1 for r in resolved.values() if r.ambiguous_with and not r.available)
    logger.info(
        f"[INSTRUMENTS] {broker_id}: {avail}/{len(resolved)} instruments available"
        + (f", {ambig} ambiguous" if ambig else "")
    )
    return resolved


def resolve_broker_symbol(canonical: str, broker_id: str | None = None) -> str | None:
    """
    Canonical instrument -> the symbol to send to this broker.

    Returns None when the broker has been discovered and does not list it — the
    caller should surface that rather than attempt the trade. When no discovery
    has run for `broker_id`, falls back to the canonical name, which is the
    pre-14.9 behaviour and keeps single-broker setups working untouched.
    """
    canonical = resolve_canonical(canonical)
    if broker_id is None:
        return canonical
    with _broker_lock:
        mapping = _broker_maps.get(broker_id)
    if mapping is None:
        return canonical
    res = mapping.get(canonical)
    if res is None:
        return canonical
    return res.broker_symbol


def get_broker_map(broker_id: str) -> dict[str, Resolution] | None:
    """The full resolution table for one broker, or None if never discovered."""
    with _broker_lock:
        mapping = _broker_maps.get(broker_id)
    return dict(mapping) if mapping is not None else None


def known_broker_ids() -> list[str]:
    with _broker_lock:
        return sorted(_broker_maps)


def reset_broker_maps() -> None:
    """Drop every discovered map. For tests and for reconnecting to a new broker."""
    with _broker_lock:
        _broker_maps.clear()
    for inst in _registry.values():
        inst.broker_symbols.clear()
