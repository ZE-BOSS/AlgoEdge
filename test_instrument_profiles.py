"""
test_instrument_profiles.py

[Task 1.21] Profile-consistency test for backend.risk.compounding.INSTRUMENT_PROFILES.

Two different checks apply depending on whether the instrument's quote currency
is USD:

1. Same-currency instruments (quote currency == account currency == USD — every
   commodity/index/crypto/synthetic profile, plus USD-quoted FX majors like
   EURUSD/GBPUSD/AUDUSD/NZDUSD/USDJPY/USDCHF/USDCAD):
       point_value_per_lot / point_size == contract_size   (within 2%)
   because point_value_per_lot is already in the account currency.

2. Cross-currency FX pairs whose quote currency is NOT USD (GBPJPY, EURJPY,
   AUDJPY, CADJPY, USDCHF's *crosses* like GBPCHF, USDCAD's crosses like
   GBPCAD, EURGBP, EURAUD, GBPAUD, GBPNZD):
       point_value_per_lot ALREADY has a same-currency-instrument-style
       exchange rate baked in (see each profile's inline comment) to convert
       the quote-currency pip value into USD. `ratio == contract_size` is the
       WRONG invariant for these — verified by back-calculating the implied
       rate from each one (see implementation/MASTER-IMPLEMENTATION-PLAN.md
       Part 11 §C1, corrected 2026-08-22): every one of them decodes to a
       plausible real-world exchange rate (GBPJPY -> USDJPY ~149.25,
       USDCHF -> ~0.870, USDCAD -> ~1.361, EURGBP -> GBPUSD ~1.27,
       EURAUD/GBPAUD -> AUDUSD ~0.65, GBPNZD -> NZDUSD ~0.60). These are NOT
       arithmetic bugs. They ARE a static point-in-time snapshot with no live
       refresh — real but different defect (see resolve_cross_rate_point_value
       in position_sizer.py, which is the actual fix: prefer a live MT5 quote
       for the conversion pair, fall back to this snapshot).

   This test instead checks the IMPLIED rate is within a generous historical
   sanity band, to catch a genuine gross error (e.g. the ~100x decimal-place
   bug documented in the GBPJPY comment, which a PRIOR fix already corrected)
   without flagging a merely-stale-but-plausible rate as a failure.

GER40 is a documented, deliberate exception (EUR-denominated contract where
contract_size is used only for the margin-notional ESTIMATE, not for pip
value) that this test does not attempt to verify — flagged separately below
for manual confirmation against your broker's actual DAX/GER40 contract spec.

Run directly: python test_instrument_profiles.py
"""

from backend.risk.compounding import INSTRUMENT_PROFILES

# symbol -> (conversion pair, plausible-rate band, quote convention).
# FX quoting convention determines the formula, not a per-symbol guess:
#   - "indirect" currencies (JPY, CHF, CAD) are conventionally quoted as
#     USD/XXX (USDJPY = JPY per 1 USD), so converting an XXX pip value to USD
#     means DIVIDING by the rate: implied_rate = (contract_size * point_size)
#     / point_value_per_lot.
#   - "direct" currencies (GBP, AUD, NZD, EUR) are conventionally quoted as
#     XXX/USD (GBPUSD = USD per 1 GBP), so converting is a direct MULTIPLY,
#     and the implied rate reads straight off the ratio:
#     implied_rate = point_value_per_lot / (contract_size * point_size).
# This also applies to the base pairs themselves (USDJPY/USDCHF/USDCAD) —
# their quote currency is JPY/CHF/CAD, not USD, so they are "indirect" too and
# do NOT belong in the strict same-currency bucket below.
_FX_CROSS_QUOTE_CCY: dict[str, tuple[str, tuple[float, float], str]] = {
    # symbol: (what USD-pair this rate represents, (plausible min, max), convention)
    "USDJPY": ("USDJPY", (80.0, 250.0), "indirect"),
    "GBPJPY": ("USDJPY", (80.0, 250.0), "indirect"),
    "EURJPY": ("USDJPY", (80.0, 250.0), "indirect"),
    "AUDJPY": ("USDJPY", (80.0, 250.0), "indirect"),
    "CADJPY": ("USDJPY", (80.0, 250.0), "indirect"),
    "USDCHF": ("USDCHF", (0.5, 1.5), "indirect"),
    "GBPCHF": ("USDCHF", (0.5, 1.5), "indirect"),
    "USDCAD": ("USDCAD", (0.9, 2.0), "indirect"),
    "GBPCAD": ("USDCAD", (0.9, 2.0), "indirect"),
    "EURGBP": ("GBPUSD", (0.9, 2.0), "direct"),
    "EURAUD": ("AUDUSD", (0.3, 1.2), "direct"),
    "GBPAUD": ("AUDUSD", (0.3, 1.2), "direct"),
    "GBPNZD": ("NZDUSD", (0.3, 1.2), "direct"),
}

# Deliberate, documented exceptions — see module docstring. Not checked here;
# each requires a human decision (a broker-spec confirmation), not a formula.
_MANUAL_REVIEW_REQUIRED = {"GER40"}

_JPY_POINT_SIZE = 0.01          # "already full pip" JPY-cross convention
_PIPETTE_POINT_SIZE = 0.00001   # 5th-decimal convention used by the other crosses


def check_profile_consistency() -> tuple[list[str], list[str]]:
    """Returns (failures, needs_manual_review)."""
    failures = []
    manual_review = []

    for symbol, p in INSTRUMENT_PROFILES.items():
        if symbol in _MANUAL_REVIEW_REQUIRED:
            manual_review.append(
                f"{symbol}: ratio={p.point_value_per_lot / p.point_size:.4f} vs "
                f"contract_size={p.contract_size} — documented deliberate exception, "
                f"verify against your broker's real contract spec (see inline comment "
                f"in compounding.py)."
            )
            continue

        if not p.point_size:
            failures.append(f"{symbol}: point_size is zero/falsy")
            continue

        if symbol in _FX_CROSS_QUOTE_CCY:
            pair_name, (lo, hi), convention = _FX_CROSS_QUOTE_CCY[symbol]
            quote_amount = p.contract_size * p.point_size
            if convention == "indirect":
                implied_rate = (quote_amount / p.point_value_per_lot) if p.point_value_per_lot else float("inf")
            else:
                implied_rate = p.point_value_per_lot / quote_amount if quote_amount else float("inf")
            if not (lo <= implied_rate <= hi):
                failures.append(
                    f"{symbol}: implied {pair_name} rate = {implied_rate:.4f}, "
                    f"outside plausible historical band [{lo}, {hi}] — "
                    f"point_value_per_lot={p.point_value_per_lot} may have a unit/decimal bug"
                )
            continue

        # Same-currency instruments: strict ratio == contract_size.
        ratio = p.point_value_per_lot / p.point_size
        if p.contract_size and abs(ratio - p.contract_size) / p.contract_size > 0.02:
            failures.append(
                f"{symbol}: point_value_per_lot/point_size = {ratio:.4f}, "
                f"expected contract_size = {p.contract_size} "
                f"(point_value_per_lot={p.point_value_per_lot}, point_size={p.point_size})"
            )

    return failures, manual_review


def test_profile_consistency():
    failures, _ = check_profile_consistency()
    assert not failures, "Instrument profile inconsistencies:\n" + "\n".join(failures)


if __name__ == "__main__":
    failures, manual_review = check_profile_consistency()
    print(f"Checked {len(INSTRUMENT_PROFILES)} profiles "
          f"({len(_FX_CROSS_QUOTE_CCY)} cross-currency, "
          f"{len(_MANUAL_REVIEW_REQUIRED)} flagged for manual review).")
    if manual_review:
        print(f"\n{len(manual_review)} NEED MANUAL REVIEW (not failures):\n")
        for m in manual_review:
            print(f"  - {m}")
    if failures:
        print(f"\n{len(failures)} FAILURES:\n")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nALL PROFILES CONSISTENT (cross-currency rates within plausible bands).")
