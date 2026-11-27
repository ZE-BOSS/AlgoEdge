"""
backend/marketdata/instruments.py

Which CME future stands behind each broker symbol, and how its prices turn into
the broker's.

Candles for a mapped symbol come from Databento (GLBX.MDP3, continuous front
contract `<root>.v.0`); orders, fills and position sizing stay on MT5. A broker
quotes the same market as the future plus a basis (cash vs futures, plus the
broker's own mark-up), so bars are translated with that basis before any
strategy sees them (see source.py). Three FX futures are quoted the other way
round (6J = USD per yen, broker USDJPY = yen per USD): `inverse` flips them.

A broker symbol is matched after normalising: upper case, letters and digits
only, and a trailing account-type suffix ("m", "micro", ".r", "_i") removed,
so "XAUUSDm", "XAUUSD.r" and "XAUUSD" are the same market.

Environment
    MARKETDATA_SOURCE   "databento" (default) or "mt5" (Databento off everywhere)
    MARKETDATA_EXCLUDE  comma list of broker symbols to keep on MT5 data
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    key: str            # canonical name
    future: str         # Databento continuous symbol
    tick: float         # futures tick size (profile binning)
    big: int            # contracts that count as a "big trade"
    inverse: bool = False
    name: str = ""


INSTRUMENTS: dict[str, Instrument] = {i.key: i for i in (
    Instrument("XAUUSD", "GC.v.0", 0.1, 20, name="COMEX gold (GC)"),
    Instrument("XAGUSD", "SI.v.0", 0.005, 10, name="COMEX silver (SI)"),
    Instrument("NAS100", "NQ.v.0", 0.25, 15, name="CME Nasdaq 100 (NQ)"),
    Instrument("SPX500", "ES.v.0", 0.25, 50, name="CME S&P 500 (ES)"),
    Instrument("US30", "YM.v.0", 1.0, 10, name="CBOT Dow (YM)"),
    Instrument("USOIL", "CL.v.0", 0.01, 20, name="NYMEX WTI crude (CL)"),
    Instrument("JP225", "NKD.v.0", 5.0, 5, name="CME Nikkei 225 USD (NKD)"),
    Instrument("EURUSD", "6E.v.0", 0.00005, 50, name="CME euro (6E)"),
    Instrument("GBPUSD", "6B.v.0", 0.0001, 30, name="CME pound (6B)"),
    Instrument("AUDUSD", "6A.v.0", 0.00005, 30, name="CME Australian dollar (6A)"),
    Instrument("USDJPY", "6J.v.0", 0.0000005, 30, inverse=True, name="CME yen (6J, inverted)"),
    Instrument("USDCAD", "6C.v.0", 0.00005, 30, inverse=True, name="CME Canadian dollar (6C, inverted)"),
    Instrument("USDCHF", "6S.v.0", 0.00005, 30, inverse=True, name="CME Swiss franc (6S, inverted)"),
)}

ALIASES: dict[str, str] = {
    "GOLD": "XAUUSD", "SILVER": "XAGUSD",
    "USTECH100": "NAS100", "USTEC": "NAS100", "NAS100": "NAS100", "NDX100": "NAS100", "NASDAQ": "NAS100",
    "NASDAQ100": "NAS100", "US100": "NAS100", "USTECH": "NAS100",
    "USSP500": "SPX500", "US500": "SPX500", "SPX500": "SPX500", "SP500": "SPX500", "SPX": "SPX500",
    "WALLSTREET30": "US30", "WALLSTREET": "US30", "US30": "US30", "DJ30": "US30", "DOW": "US30", "DJI30": "US30",
    "USOIL": "USOIL", "OILUSD": "USOIL", "WTI": "USOIL", "XTIUSD": "USOIL", "USCRUDE": "USOIL", "CRUDEOIL": "USOIL",
    "JAPAN225": "JP225", "JP225": "JP225", "JPN225": "JP225", "NIKKEI": "JP225", "NIKKEI225": "JP225",
}
_SUFFIX = re.compile(r"(MICRO|PRO|ECN|STD|M|R|I|C)$")


def _clean(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())


def lookup(symbol: str) -> Instrument | None:
    """The future behind a broker symbol, or None (synthetic indices, crosses...)."""
    s = _clean(symbol)
    if not s:
        return None
    for cand in (s, _SUFFIX.sub("", s)):
        key = ALIASES.get(cand, cand)
        if key in INSTRUMENTS:
            return INSTRUMENTS[key]
    raw = str(symbol or "").strip()
    for sep in (".", "_"):                         # "XAUUSD.r", "XAUUSD_i"
        if sep in raw:
            return lookup(raw.split(sep)[0])
    return None


def enabled() -> bool:
    return os.getenv("MARKETDATA_SOURCE", "databento").strip().lower() == "databento"


def excluded(symbol: str) -> bool:
    raw = os.getenv("MARKETDATA_EXCLUDE", "")
    return _clean(symbol) in {_clean(x) for x in raw.split(",") if x.strip()}


def mapped(symbol: str) -> Instrument | None:
    """lookup() when Databento data is on for this symbol."""
    if not enabled() or excluded(symbol):
        return None
    return lookup(symbol)
