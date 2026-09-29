"""
backend/notify/templates.py

Every email the platform sends, as plain functions returning a Message.

Written as inline-styled, table-laid-out HTML rather than MJML (the plan's
first idea): MJML needs a Node build step on the server for no gain at this
size, and table + inline CSS is what MJML compiles to anyway — it is what
Gmail, Outlook and Apple Mail all render the same way. Every message also has a
plain-text part, which spam filters look for and some people read.

Every value that came from a person (a name, a reason, a reference) is escaped.
No image is loaded from the web: many clients block remote images by default,
and a wordmark in text cannot be blocked.
"""

from __future__ import annotations

import html
import os

from backend.investor import nav as navmod
from backend.notify.outbox import Message

GOLD = "#b8912a"
INK = "#101722"


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def money(v) -> str:
    if v is None:
        return "—"
    s = navmod.text(navmod.money(v))
    neg = s.startswith("-")
    i, f = s.lstrip("-").split(".")
    return f"{'-' if neg else ''}${int(i):,}.{f}"


def units(v) -> str:
    return f"{navmod.units(v):,.4f}"


def _app() -> str:
    return os.getenv("INVESTOR_APP_URL", "http://localhost:5174").rstrip("/")


def _admin() -> str:
    return os.getenv("ADMIN_APP_URL", "http://localhost:5173").rstrip("/")


def _layout(title: str, blocks: list[str], *, button: tuple[str, str] | None = None,
            footer: str | None = None) -> str:
    body = "".join(f'<p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#2b3442">{b}</p>'
                   if not b.startswith("<") else b for b in blocks)
    btn = ""
    if button:
        label, url = button
        btn = (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 18px">'
               f'<tr><td style="background:{GOLD};border-radius:8px">'
               f'<a href="{_e(url)}" style="display:inline-block;padding:12px 22px;font-weight:600;'
               f'font-size:15px;color:#ffffff;text-decoration:none">{_e(label)}</a></td></tr></table>')
    foot = footer or ("You are receiving this because you have an account with Alphavantiq Capital. "
                      "We will never ask for your password by email.")
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f3f4f6">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<tr><td style="background:{INK};border-radius:12px 12px 0 0;padding:18px 28px;font-size:13px;letter-spacing:3px;font-weight:700;color:#ffffff">
ALPHAVANTIQ <span style="color:{GOLD};font-weight:500">CAPITAL</span></td></tr>
<tr><td style="padding:28px">
<h1 style="margin:0 0 16px;font-size:20px;color:{INK}">{_e(title)}</h1>
{body}{btn}
</td></tr>
<tr><td style="padding:0 28px 24px;font-size:12px;line-height:1.5;color:#7a8494">{_e(foot)}</td></tr>
</table></td></tr></table></body></html>"""


def _rows(pairs: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<tr><td style="padding:6px 0;color:#6b7280;font-size:14px">{_e(k)}</td>'
        f'<td style="padding:6px 0;text-align:right;font-size:14px;color:{INK};font-weight:600">{_e(v)}</td></tr>'
        for k, v in pairs)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin:4px 0 18px;border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb">{cells}</table>')


def _text(title: str, lines: list[str], pairs: list[tuple[str, str]] | None = None,
          link: str | None = None) -> str:
    out = [title, ""] + lines
    if pairs:
        out += [""] + [f"{k}: {v}" for k, v in pairs]
    if link:
        out += ["", link]
    out += ["", "— Alphavantiq Capital"]
    return "\n".join(out)


def _msg(kind, inv, subject, title, lines, *, pairs=None, button=None, attachments=None,
         to=None, footer=None) -> Message:
    blocks = [_e(line) for line in lines]
    if pairs:
        blocks.append(_rows(pairs))
    return Message(kind=kind, to=to or inv.email, subject=subject,
                   html=_layout(title, blocks, button=button, footer=footer),
                   text=_text(title, lines, pairs, button[1] if button else None),
                   investor_id=getattr(inv, "id", None), attachments=attachments or [])


def _first(inv) -> str:
    return (inv.name or "").split(" ")[0] or "there"


# ── access ───────────────────────────────────────────────────────────────────

def invite(inv, url: str, expires) -> Message:
    return _msg("invite", inv, "Your Alphavantiq Capital account", "Welcome to Alphavantiq Capital",
                [f"Hello {_first(inv)},",
                 "Your investor account is ready. Choose a password to sign in and see your holding, "
                 "statements and transactions.",
                 f"This link works once and expires on {expires:%d %b %Y at %H:%M} UTC."],
                button=("Set your password", url))


def signup(inv, url: str, expires) -> Message:
    return _msg("signup", inv, "Confirm your email", "Welcome to Alphavantiq Capital",
                [f"Hello {_first(inv)},",
                 "Thank you for signing up. Confirm this is your email address by choosing a password; "
                 "you can then sign in, see how to add money, and follow the fund.",
                 f"This link works once and expires on {expires:%d %b %Y at %H:%M} UTC. "
                 "If you did not sign up, ignore this email and nothing happens."],
                button=("Confirm and choose a password", url))


def reset(inv, url: str) -> Message:
    return _msg("reset", inv, "Reset your password", "Reset your password",
                [f"Hello {_first(inv)},",
                 "Someone — hopefully you — asked to reset the password for your account. "
                 "The link below works once, for 30 minutes.",
                 "If this wasn't you, ignore this email: your password has not changed."],
                button=("Choose a new password", url))


def password_changed(inv) -> Message:
    return _msg("password_changed", inv, "Your password was changed", "Your password was changed",
                [f"Hello {_first(inv)},",
                 "The password for your account was just changed, and every other device was signed out.",
                 "If this wasn't you, reply to this email or contact us immediately."])


def payout_changed(inv, last4: str | None, hours: int) -> Message:
    return _msg("payout_changed", inv, "The account we pay you was changed",
                "The account we pay you was changed",
                [f"Hello {_first(inv)},",
                 f"The bank account we send your withdrawals to was changed to one ending in "
                 f"{last4 or '—'}.",
                 f"For your protection, withdrawals requested in the next {hours} hours are reviewed "
                 f"by us before they are paid.",
                 "If you did not ask for this change, contact us immediately."])


# ── money in ────────────────────────────────────────────────────────────────

def deposit_confirmed(inv, dep, units_issued, price) -> Message:
    extra = []
    if dep.amount_claimed and navmod.money(dep.amount_claimed) != navmod.money(dep.amount_confirmed):
        extra = [f"You told us you sent {money(dep.amount_claimed)}; {money(dep.amount_confirmed)} "
                 f"arrived, and that is the amount we invested."]
    return _msg("deposit_confirmed", inv, f"We received {money(dep.amount_confirmed)}",
                "Your money has arrived",
                [f"Hello {_first(inv)},", "Your transfer has arrived and has been invested.", *extra],
                pairs=[("Amount invested", money(dep.amount_confirmed)),
                       ("Units bought", units(units_issued)),
                       ("Price per unit", units(price)),
                       ("Priced on", dep.effective_date.strftime("%d %b %Y"))],
                button=("See your account", _app()))


def deposit_rejected(inv, dep) -> Message:
    return _msg("deposit_rejected", inv, "About your transfer", "We could not match your transfer",
                [f"Hello {_first(inv)},",
                 f"We could not confirm the transfer of {money(dep.amount_claimed)} you told us about.",
                 f"Reason: {dep.rejected_reason}",
                 "Nothing has been added to your account. Reply to this email if you think this is wrong."])


# ── money out ───────────────────────────────────────────────────────────────

def withdrawal_received(inv, w, notice_days: int) -> Message:
    if w.is_exception:
        lines = ["We have received your withdrawal request. Because it is above the standard limit, "
                 "we review it before it is approved, and will be in touch."]
    else:
        lines = [f"We have received your withdrawal request and will pay it within {notice_days} days."]
    return _msg("withdrawal_received", inv, f"Withdrawal request: {money(w.amount_requested)}",
                "Withdrawal request received", [f"Hello {_first(inv)},", *lines],
                pairs=[("Amount", money(w.amount_requested)),
                       ("To", f"{w.destination_bank_name or ''} ····{(w.destination_account_number or '')[-4:]}")])


def withdrawal_approved(inv, w) -> Message:
    return _msg("withdrawal_approved", inv, f"Withdrawal approved: {money(w.amount_requested)}",
                "Your withdrawal is approved",
                [f"Hello {_first(inv)},",
                 "Your withdrawal has been approved and the payment is being sent. "
                 "We will email again with the transfer reference once it has gone."],
                pairs=[("Amount", money(w.amount_requested))])


def withdrawal_paid(inv, w) -> Message:
    return _msg("withdrawal_paid", inv, f"Payment sent: {money(w.amount_paid)}", "Your payment has been sent",
                [f"Hello {_first(inv)},", "Your withdrawal has been paid."],
                pairs=[("Amount", money(w.amount_paid)),
                       ("To", f"{w.destination_bank_name or ''} ····{(w.destination_account_number or '')[-4:]}"),
                       ("Transfer reference", w.payment_reference or "—")])


def withdrawal_declined(inv, w) -> Message:
    return _msg("withdrawal_declined", inv, "About your withdrawal request", "Your withdrawal was not approved",
                [f"Hello {_first(inv)},",
                 f"Your request to withdraw {money(w.amount_requested)} was not approved.",
                 f"Reason: {w.declined_reason}",
                 "Nothing has been taken from your account. Reply to this email to discuss it."])


# ── leaving ─────────────────────────────────────────────────────────────────

def closure_requested(inv, quote: dict) -> Message:
    return _msg("closure_requested", inv, "Your request to close your account",
                "We have your request to close your account",
                [f"Hello {_first(inv)},",
                 "We will sell your units, pay the proceeds to your account on file, and then close the "
                 "account. To change your mind, reply before we pay.",
                 "The figure below is today's; the final amount uses the price on the day we process it."],
                pairs=[("Holding today", money(quote["gross_value"])),
                       ("Fees to date this month", money(quote["fees_owed"])),
                       ("Estimated payout", money(quote["net_payable"]))])


def closure_approved(*, email: str, name: str, result: dict, statement_pdf: bytes | None) -> Message:
    class _P:  # the investor row is anonymised by now; this carries what was captured before
        pass
    p = _P()
    p.email, p.name, p.id = email, name, result.get("investor_id")
    return _msg("closure_approved", p, "Your account is closed", "Your account is closed",
                [f"Hello {_first(p)},",
                 "Your final payment has been sent and your account is now closed. Your final statement "
                 "is attached. As promised, we have deleted your personal details; the fund keeps an "
                 "anonymous record of the transactions, as the law requires.",
                 "Thank you for investing with us."],
                pairs=[("Final payment", money(result["amount_paid"])),
                       ("Transfer reference", result.get("payment_reference") or "—")],
                attachments=[("alphavantiq-final-statement.pdf", statement_pdf)] if statement_pdf else [])


def statement(inv, label: str, pdf: bytes, closing_value) -> Message:
    return _msg("statement", inv, f"Your statement for {label}", f"Your statement for {label}",
                [f"Hello {_first(inv)},", f"Your statement for {label} is attached.",
                 f"Holding at the end of the month: {money(closing_value)}."],
                button=("See your account", _app()),
                attachments=[(f"alphavantiq-statement-{label.replace(' ', '-').lower()}.pdf", pdf)])


# ── to the admin ────────────────────────────────────────────────────────────

def _admin_to() -> str | None:
    return os.getenv("ADMIN_ALERT_EMAIL") or None


def admin_new_device(user, ip: str | None, ua: str | None, when) -> Message | None:
    to = user.email
    return _msg("admin_new_device", None, "New sign-in to the admin console", "New sign-in to the admin console",
                ["Your admin account was just signed in to from a browser we have not seen before.",
                 "If this was you, there is nothing to do. If it wasn't, change your password now and "
                 "check the audit log."],
                pairs=[("Account", user.email), ("When (UTC)", when.strftime("%d %b %Y %H:%M")),
                       ("IP address", ip or "—"), ("Browser", (ua or "—")[:120])],
                to=to, footer="Security alert from the Alphavantiq Capital admin console.")


def admin_digest(stats: dict) -> Message | None:
    to = _admin_to()
    if not to:
        return None
    pairs = [(k, str(v)) for k, v in stats.items()]
    return _msg("admin_digest", None, f"Daily summary — {stats.get('Day', '')}", "Daily summary",
                ["Yesterday on the investor platform:"], pairs=pairs,
                button=("Open the console", f"{_admin()}/investors"), to=to,
                footer="Daily digest. Turn it off with INVESTOR_DAILY_DIGEST=off.")


def admin_ledger_drift(drift: list) -> Message | None:
    to = _admin_to()
    if not to:
        return None
    pairs = [(str(i), f"ledger {w} / cache {g}") for i, w, g in drift]
    return _msg("admin_ledger_drift", None, "ACTION NEEDED: ledger check failed", "The nightly ledger check failed",
                ["An investor's cached unit balance no longer matches a replay of the ledger. The ledger "
                 "is the truth; the cache drives dashboards. Nothing should be approved for these investors "
                 "until it is understood."], pairs=pairs,
                button=("Open reconciliation", f"{_admin()}/investors/reconciliation"), to=to,
                footer="Nightly integrity check.")


def admin_application(app_row) -> Message | None:
    to = _admin_to()
    if not to:
        return None
    return _msg("admin_application", None, f"New application: {app_row.name}", "New investor application",
                ["Someone applied through the website."],
                pairs=[("Name", app_row.name), ("Email", app_row.email), ("Phone", app_row.phone or "—"),
                       ("Country", app_row.country or "—"), ("Intended amount", app_row.amount_band or "—"),
                       ("Message", (app_row.message or "—")[:500])],
                button=("Open applications", f"{_admin()}/investors/applications"), to=to)
