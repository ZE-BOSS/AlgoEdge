"""Shared test setup.

The fund's agreed defaults (a $3,000 minimum, 50% performance, 5% management)
would make every older investor test re-derive its figures. Those tests are
about the ledger, not the rates, so they run against small round terms; the
tests that ARE about the rates or the defaults opt out with
@pytest.mark.real_terms.
"""

from decimal import Decimal

import pytest

TEST_TERMS = {"performance_fee_pct": Decimal("20"), "management_fee_pct": Decimal("2"),
              "min_investment": Decimal("200")}


def pytest_configure(config):
    config.addinivalue_line("markers", "real_terms: use the fund's real default terms")


@pytest.fixture(autouse=True)
def _small_test_terms(request, monkeypatch):
    if not request.module.__name__.split(".")[-1].startswith("test_investor"):
        return
    if request.node.get_closest_marker("real_terms"):
        return
    from backend.investor import fund
    monkeypatch.setattr(fund, "DEFAULT_SETTINGS", {**fund.DEFAULT_SETTINGS, **TEST_TERMS})
