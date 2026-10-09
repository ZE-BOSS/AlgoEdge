"""
tests/test_email_compliance.py

Commercial email carries a postal address and a one-click unsubscribe, or it
does not go out. Transactional email carries neither obligation and must never
be given an unsubscribe link.

WHY
---
`_layout` emitted a footer of prose with no postal address, and `_send_resend`
set no `List-Unsubscribe` header. CAN-SPAM requires a physical postal address in
commercial email and the penalty is charged PER MESSAGE, and Gmail and Yahoo now
effectively require RFC 8058 one-click unsubscribe of bulk senders — so the same
omission is both a legal exposure and a deliverability problem.

The distinction is load-bearing in the other direction too. A statement or a
payout confirmation must NOT offer an unsubscribe: switching those off is not a
preference anyone should be given, and an unsubscribe link on a transactional
message invites exactly that.

THE BLANK-PROFILE CASE IS THE NORMAL ONE
----------------------------------------
There is no registered address yet. So "what happens while the profile is
empty" is not an edge case to be tolerated, it is today's behaviour, and the
rule is: transactional mail is unaffected, commercial mail is refused, and no
half-written legal notice is ever rendered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.notify import company, templates
from backend.notify.outbox import COMMERCIAL, TRANSACTIONAL, Message, blocked_reason


class _Investor:
    id = "inv-1"
    email = "ada@example.com"
    name = "Ada Obi"
    preferences = None


FULL = company.Snapshot(
    legal_entity_name="Alphavantiq Capital Ltd",
    registration_number="RC 1234567",
    postal_address="1 Example Road, Lagos, Nigeria",
    support_email="invest@alphavantiqcapital.com",
    policy_version="2026-10",
)


@pytest.fixture(autouse=True)
def _blank_profile():
    """Every test states the profile it wants; none inherits one."""
    company.set_snapshot(company.Snapshot.empty())
    yield
    company.set_snapshot(None)


# ── the refusal ─────────────────────────────────────────────────────────────

def test_commercial_email_is_refused_while_there_is_no_postal_address():
    m = Message(kind="launch", to="ada@example.com", subject="We have launched",
                html="", text="", category=COMMERCIAL,
                unsubscribe_url="https://x/unsubscribe?t=abc")
    why = blocked_reason(m)
    assert why is not None, "a commercial send with no address on file must be blocked"
    assert "postal address" in why


def test_commercial_email_is_refused_without_an_unsubscribe_link():
    company.set_snapshot(FULL)
    m = Message(kind="launch", to="ada@example.com", subject="s", html="", text="",
                category=COMMERCIAL)
    assert "unsubscribe" in (blocked_reason(m) or "")


def test_commercial_email_sends_once_both_are_present():
    company.set_snapshot(FULL)
    m = Message(kind="launch", to="ada@example.com", subject="s", html="", text="",
                category=COMMERCIAL, unsubscribe_url="https://x/u?t=abc")
    assert blocked_reason(m) is None


def test_transactional_email_is_never_blocked_by_a_blank_profile():
    """A statement must not stop going out because a marketing field is unset."""
    m = Message(kind="statement", to="ada@example.com", subject="s", html="", text="")
    assert m.category == TRANSACTIONAL, "TRANSACTIONAL must be the default"
    assert blocked_reason(m) is None


def test_an_unknown_category_is_refused_rather_than_guessed():
    m = Message(kind="x", to="a@b", subject="s", html="", text="", category="MARKETING")
    assert blocked_reason(m) is not None


# ── what the message actually contains ──────────────────────────────────────

def _msg(**kw):
    return templates._msg("promo", _Investor(), "Subject", "Title",
                          ["Hello Ada,", "Something happened."], **kw)


def test_the_identity_line_is_rendered_from_the_profile():
    company.set_snapshot(FULL)
    m = _msg()
    for fragment in ("Alphavantiq Capital Ltd", "RC 1234567", "1 Example Road, Lagos, Nigeria"):
        assert fragment in m.html, f"{fragment} must appear in the footer"


def test_no_half_written_legal_notice_when_the_profile_is_incomplete():
    """A name with no address is not a compliant footer, so neither is rendered.

    Printing half of a legal notice is worse than printing none: it looks like
    the requirement was met.
    """
    company.set_snapshot(company.Snapshot(legal_entity_name="Alphavantiq Capital Ltd"))
    m = _msg()
    assert "Alphavantiq Capital Ltd ·" not in m.html
    assert "registered no." not in m.html


def test_a_commercial_message_carries_the_link_in_both_parts():
    company.set_snapshot(FULL)
    url = "https://x/unsubscribe?t=abc"
    m = _msg(category=COMMERCIAL, unsubscribe_url=url)
    assert url in m.html, "the HTML part must carry the link"
    assert f"Unsubscribe: {url}" in m.text, (
        "the plain-text part must carry it too — a client that renders text only "
        "would otherwise have no way to unsubscribe"
    )


def test_a_transactional_message_is_not_given_an_unsubscribe_link():
    company.set_snapshot(FULL)
    m = _msg()
    assert "Unsubscribe" not in m.html
    assert "Unsubscribe" not in m.text


@pytest.mark.parametrize("fn,args", [
    ("reset", ("https://x/accept?token=t",)),
    ("password_changed", ()),
])
def test_every_account_email_is_transactional(fn, args):
    """Nothing that exists today is commercial, and the default must reflect it."""
    company.set_snapshot(FULL)
    m = getattr(templates, fn)(_Investor(), *args)
    assert m.category == TRANSACTIONAL
    assert m.unsubscribe_url is None


# ── the provider headers ────────────────────────────────────────────────────

def test_the_resend_payload_sets_one_click_unsubscribe_for_commercial_mail():
    """RFC 8058. Checked on the source because building the payload requires a
    network client, and the headers are the whole point of the branch."""
    src = Path("backend/notify/outbox.py").read_text(encoding="utf-8")
    assert '"List-Unsubscribe": f"<{m.unsubscribe_url}>"' in src
    assert '"List-Unsubscribe-Post": "List-Unsubscribe=One-Click"' in src
    # and only for commercial mail
    head = src.split("List-Unsubscribe", 1)[0]
    assert "if m.category == COMMERCIAL and m.unsubscribe_url:" in head


def test_a_blocked_message_is_recorded_rather_than_dropped():
    """"Why did the announcement not go out" has to be answerable."""
    src = Path("backend/notify/outbox.py").read_text(encoding="utf-8")
    assert "_record_blocked(m, stop)" in src
    assert 'state="blocked"' in src


# ── the profile itself ──────────────────────────────────────────────────────

def test_the_postal_address_omits_what_is_not_set():
    from backend.investor.models import CompanyProfile

    row = CompanyProfile(address_line1="1 Example Road", city="Lagos", country="Nigeria")
    assert row.postal_address == "1 Example Road, Lagos, Nigeria"
    assert CompanyProfile().postal_address == ""


def test_a_profile_with_no_name_cannot_send_commercial_email():
    from backend.investor.models import CompanyProfile

    assert not CompanyProfile(city="Lagos", country="Nigeria").can_send_commercial_email
    assert not CompanyProfile(legal_entity_name="X Ltd").can_send_commercial_email
    assert CompanyProfile(legal_entity_name="X Ltd", city="Lagos").can_send_commercial_email


def test_the_profile_is_not_defaulted_into_existence():
    """`fund.current_settings` creates a default row; this must not.

    A blank legal identity written to the database looks like a decision
    somebody made, and that decision has not been made yet.
    """
    src = Path("backend/notify/company.py").read_text(encoding="utf-8")
    body = src.split("async def current_row", 1)[1].split("async def refresh", 1)[0]
    assert "CompanyProfile(" not in body, "current_row must never insert a row"


def test_the_cache_is_safe_before_anything_has_loaded_it():
    company.set_snapshot(None)
    snap = company.get()
    assert snap.display_name == ""
    assert snap.footer_identity == ""
    assert snap.can_send_commercial_email is False
