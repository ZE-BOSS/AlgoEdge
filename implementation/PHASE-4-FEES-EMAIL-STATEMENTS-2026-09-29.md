# Phase 4 — fees, email, statements

**2026-09-29** · `backend/investor/fees.py`, `backend/notify/`, `backend/investor/statements.py`,
`backend/investor/jobs.py` · tests: `test_investor_fees.py` (10), `test_investor_email.py` (12)

Phase 4 of `INVESTOR-PLATFORM-2026-09-29.md`, plus fee calculation, which had been
pushed back from earlier phases. It went in here because statements are the first
place fees appear.

---

## First, a pricing bug from Phase 1, now fixed

**Between a withdrawal's approval and its payment, every NAV snapshot shared the
withdrawn money out among the other investors.** Approval cancels the units, but
the money stays in the broker account until the transfer goes out. Snapshots
divided that money among everyone who was left.

Reproduced: two investors hold $1,000 each, and $500 is approved to one of them.
The other investor was then priced at **$1,333.33**.

The fix: `fund.owed_on(day)` adds up what the fund owes but has not paid out:
- approved withdrawals not yet paid
- fees charged but not yet withdrawn by the manager

Every snapshot now subtracts that automatically, and reconciliation uses the same
figure. The NAV form's "liabilities" box is now for anything *else* owed.

"Mark paid" now asks for the day the money actually left. This lets a
back-dated snapshot count what was owed on that date.

---

## Fees

Admin console → Investors → **Fees**.

1. Pick a month.
2. Check the preview. It shows each investor's value, days held, management fee,
   profit, high-water mark and performance fee, with the working.
3. Optionally tick investors to **waive**.
4. **Charge**.
5. Later, **Mark withdrawn** when you take the fees out of the broker account.

The formulas, per investor:

```
management  = value × rate × days held ÷ 365          (first month pro-rated)
profit      = value − management + paid out − paid in  (lifetime)
performance = rate × max(0, profit − high-water mark)
new mark    = max(old mark, profit − performance)
```

- **The high-water mark is set after the fee is taken off**, so an investor
  never pays again on the fee they already paid. A loss must be earned back
  before any new performance fee. The test walks through June to October step
  by step.
- **Rates come from the terms the investor committed under**, not today's.
- **A month can be charged once.** It needs a NAV snapshot within 7 days of
  month end, and it can only be charged after the month has ended.
- After charging, the Fees tab shows the **recorded** charges. At first it
  recalculated from the new ledger and showed different figures; this was caught
  in the browser and fixed.
- **A waived fee** is recorded but not charged. The high-water mark still moves:
  the investor was let off the fee, not handed the right to be charged it later.
- Charging cancels units at the month-end price. **The money stays a liability**
  (it's still in the broker account) until marked withdrawn, so it never
  inflates the other investors' price.
- **Closures:** the part of the current month's fees up to the closing day is
  charged on approval, and the closure quote shows it. Closure is refused while
  last month's fees are uncharged. Otherwise they would never be charged,
  because the monthly charge skips anyone holding zero units.

## Email (Resend)

`backend/notify/`. Every message is recorded in `email_log`; see the **Audit →
Emails** tab.

| Investor receives | When |
|---|---|
| Invitation / password reset | admin clicks **Email invitation** / **Email password reset**, or the investor uses **Forgotten your password?** |
| Money arrived (units, price, date; says so if the amount differs from what they told us) / transfer not matched | a deposit is confirmed / rejected |
| Withdrawal request received (paid within N days, or under review) / approved / paid (with reference) / declined (with reason) | each step |
| Closure request received (with the estimate) / account closed (**final statement attached**) | each step |
| Monthly statement (PDF attached) | admin clicks **Email this month's statements** |
| Password changed / payout account changed | security notices |

| Admin receives (at `ADMIN_ALERT_EMAIL`) | When |
|---|---|
| New sign-in to the admin console | an admin signs in from a browser not seen before (sent to that admin) |
| Daily summary | after each WAT day: deposits confirmed, withdrawals paid, open queues, NAV, AUM. Turn off with `INVESTOR_DAILY_DIGEST=off` |
| **Ledger check failed** | the nightly check finds a cached balance that doesn't match the ledger |

**Emails only go out after the database commit.** A measured finding: in this
FastAPI version, background tasks run *before* the request's database commit, so
using them could confirm a deposit that was then rolled back. Instead, messages
are attached to the database session, handed to the sender only after the
commit succeeds, and dropped on rollback.

A test caught one more case, and it's now fixed. If the session hadn't yet
touched the database, a rollback fired no event at all, so a later commit would
have sent the message anyway. `queue()` now opens the transaction first. The
rollback test fails when that handling is removed.

**Payout-account cooling-off** (plan §11). When an investor's payout account is
*changed* (as opposed to entered for the first time), they are emailed. Every
withdrawal they request in the next 48 hours goes to review, however small.

**Forgot password** gives the same answer for any address, so it can't be used
to find out who is a client. It is limited to 3 requests per address per hour.

**Templates** are inline-styled table HTML plus a plain-text part. That is what
MJML compiles to anyway, and MJML would have needed a Node build step on the
server. There are no remote images, so nothing gets blocked, and all
user-supplied text is escaped.

**Modes.** With `EMAIL_MODE=live` and a key, emails go through Resend with an
idempotency key, so a retry can't double-send. With `EMAIL_MODE=log`, or no key,
everything is recorded and nothing is sent.

## Statements

A PDF per investor per month:
- the opening value, money in, money out, investment result before fees, fees,
  and closing value, laid out so they add up on the page
- units and price at both ends
- every transaction in the month, with fees named (management or performance)

It uses DejaVu Sans, borrowed from matplotlib, which is already a dependency.
Names like "Ọlábísí Adébáyọ̀" render correctly; the built-in PDF fonts would
garble them.

- **Investors:** Activity → Monthly statements → Download PDF.
- **Admin:** any investor's page → Statements → pick a month.
- **Sending:** refused until that month's fees are charged, because the statement
  would otherwise change afterwards. Each month can be sent once.

## Scheduled jobs

`backend/investor/jobs.py` runs a loop inside the API process every 15 minutes.
After 01:00 WAT it runs the **nightly ledger check** (Phase 7 had planned it)
and the **daily digest** for the day before. A database row claims each job for
the day, so extra worker processes can't run it twice. Turn the loop off with
`INVESTOR_JOBS=off`.

## Also

- `fpdf2` has been added to `requirements.txt`. The existing `generate_pdf.py`
  already imported it but it was never listed.
- The admin login now returns `is_admin`. The Investors link is hidden for
  operators who aren't admins; the server enforces this either way.
- New tables: `email_log`, `investor_job_runs`, `admin_devices`, plus three new
  columns on `fee_accruals`. These were added through the existing `init_db`
  migration list, and tested by starting the API on a database created before
  Phase 4.

## To switch email on

1. In Resend, add and verify **alphavantiqcapital.com**. Resend will show SPF,
   DKIM and a return-path record; add them in **Hostinger → DNS** (see
   `docs/DEPLOY-INVESTOR-PLATFORM.md`, Phase 5).
2. In the server's `.env`:
   - `RESEND_API_KEY=…`
   - `EMAIL_MODE=live`
   - `EMAIL_FROM="Alphavantiq Capital <no-reply@alphavantiqcapital.com>"`
   - `EMAIL_REPLY_TO=…`
   - `ADMIN_ALERT_EMAIL=…`
3. Restart the API. The first email will show up in Audit → Emails as `sent`,
   with a Resend id.

This sandbox can't reach api.resend.com (its network policy blocks it), so live
sending was tested against a mock of Resend's API. That mock checks the auth
header, idempotency key, attachment encoding and error handling. Everything else
ran in log mode.

## Verified

- 113 investor tests pass (22 new). Full suite: 895 passed; the 3 failures are
  the trading-side tests that already fail on `dev`.
- Browser walkthrough on the pre-Phase-4 database:
  - migrations applied cleanly
  - charged August fees
  - emailed statements to two investors
  - emailed an invitation
  - ran the investor's forgot-password flow
  - downloaded a statement as the investor
  - all of it appeared in the Emails tab; no page errors
