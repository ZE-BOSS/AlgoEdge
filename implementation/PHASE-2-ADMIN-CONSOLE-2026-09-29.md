# Phase 2 — the admin console

**2026-09-29** · `backend/api/routes/admin_investors.py`, `backend/investor/disclosure.py`,
closure in `backend/investor/fund.py` · `frontend/src/pages/Investors/` ·
`tests/test_investor_admin_routes.py` (21 tests)

Phase 2 of `INVESTOR-PLATFORM-2026-09-29.md`. Everything here is reads and state
transitions over the Phase 1 ledger. No route writes a ledger row and no route
does arithmetic on money; that stays in `backend/investor/`, where it is tested.

---

## Where it is

Sidebar → **Investors**. Eight tabs:

| Tab | What it does |
|---|---|
| Overview | AUM, NAV/unit, units in issue, reconciliation health, a count per queue |
| Investors | Register with units, value, capital in, withdrawn, profit and share. Add investor. Click through to one investor: statement, record deposit (with the date it really arrived), unit correction, details, payout account, closure, full ledger, deposits, withdrawals, adjustments |
| Queues | Deposits · Withdrawals · Exceptions · To pay · Closures |
| Disclosure | Closed live trades: publish as-is, edit and publish, hide. Shows exactly what investors will receive |
| NAV | Snapshot history; take a snapshot from an entered pool equity |
| Reconciliation | Raw against investor-facing, plus ledger-vs-cache drift |
| Fund terms | Current terms and every past version; publishing a change writes a new version |
| Audit | Audit log (filter by action) and the adjustments trail |

The sidebar tabs show a gold count when a queue has something in it.

---

## Rules the routes enforce

**Admin-only at the router.** `require_admin` sits on the router itself, so a
route added later cannot forget it. Every route returns 403 to a non-admin, and
there is a test for that.

**A refusal is a 400 with a sentence, and it writes nothing.** The module's
`FundError` / `LedgerError` become a 400 whose text the UI shows next to the
button. Because the error leaves through `get_db`, the session rolls back, so a
refused action does not leave half a deposit behind. Tested: a below-minimum
deposit returns 400 and there are no ledger rows afterwards.

**Money crosses the wire as strings, and is shown as strings.** A JSON number is
a float in the browser. The frontend formats the strings directly and never
parses them to numbers.

**A withdrawal shows what the holding is worth *now*.** The NAV can fall between
a request and its approval. The queue puts the current value next to the
request and flags it red when the holding no longer covers it. That way the
admin sees the problem before pressing approve.

**Changing a payout account needs a reason.** Each changed field writes an
`adjustments` row with the old account. The account numbers go into the
adjustment trail only, not the audit log, because the audit log is read more
widely.

**Re-pricing a day needs a reason.** If a day already has a NAV, replacing it
needs a reason and writes an adjustment holding the old NAV. Ledger rows that
already executed keep the price they executed at.

**Trade disclosure strips strategy at storage, not in the UI.**
`trade_disclosures` has no column that could hold strategy, parameters, ticket
or size. `public_view()` returns a *whitelist*, so any column added later stays
private until someone deliberately adds it to it. When the admin changes a
published figure (symbol, side, date, result) it needs a reason and becomes an
adjustment against the raw value. A note is commentary, not a figure, and needs
none. Withdrawing a published trade needs a reason, because investors may
already have seen it. `disclosure.published()` is the exact function Phase 3's
investor route will call, and the Disclosure tab's "What investors see" panel
calls it now.

**Closure follows the order you specified: pay, then approve, and approval
closes the account.**
- Approval is refused while the investor has an open deposit, a withdrawal not
  yet decided, or an approved withdrawal not yet marked paid. Any of those would
  pay twice or cancel units twice.
- The payout is recorded as a `paid` withdrawal carrying the transfer reference,
  so it shows up in every money-out total.
- Every unit is redeemed by `redeem_all`. It works in units, so the holding
  lands on exactly zero.
- The investor's name, email, phone, country, credentials and bank details are
  then erased, along with the bank details on their past withdrawals. The email
  becomes `closed-<id>@anonymised.invalid`, a reserved domain that cannot
  deliver. **The ledger and audit rows are kept**, so the fund can still rebuild
  its history.
- In the UI the button stays disabled until the admin types the investor's
  name. The action cannot be undone.

**The catch-all route is registered last.** FastAPI matches routes in the order
they are declared. If `GET /{investor_id}` were declared early it would swallow
`/disclosures` and `/reconciliation`. There is a test for this.

---

## Two Phase 1 bugs found in the browser and fixed

Both got past the Phase 1 tests. They only appear with data read back from the
database, and the browser walkthrough is what surfaced them.

1. **A zero balance printed as `0E-8`.** Zero rounded to 8 decimal places is
   `Decimal('0E-8')`, and `str()` prints exactly that. So every fully redeemed
   investor, and every pending one, would have shown "0E-8 units". The new
   `nav.text()` always writes plain digits. It is now used at every point where
   a Decimal is turned into a string, in `reconcile`, `fund`, `disclosure` and
   the routes. Pinned by a test.
2. **The withdrawal limit read "30.0000% of this month's profit".** The
   percentage comes back from a `NUMERIC(9,4)` column with trailing zeros, and
   `:g` kept them. It now reads "30%". Pinned by a test that reads the setting
   back from the database.

---

## Verified

- **75 investor tests pass**: Phase 1's 54, plus 21 route tests that go over HTTP
  through the real dependency chain and the same commit/rollback as `get_db`.
- Full suite: 3 failures (`live_exit_parity`, `live_slot_book`, `vol_target`).
  They fail the same way on `dev` without this change, and all three are on the
  trading side.
- Frontend lint is still at the 61-problem baseline, with none in the new files.
  `vite build` is clean.
- Browser walkthrough against a seeded database:
  - confirmed a claimed $1,500 deposit at the $1,450 that actually arrived
  - approved and paid an over-limit exception
  - published one trade, had an edit without a reason refused inline, then
    published the edit with a reason and saw it in the adjustments trail
  - reconciled against a wrong equity figure and saw the gap flagged red
  - closed an account end to end
  - at 390px wide there is no sideways scroll, and the only console errors are
    Google Fonts being blocked by the sandbox.

---

## Not in Phase 2

- **The fee accrual job.** The table and the high-water-mark column exist. The
  closure quote shows fees owed as $0.00 until the job is built.
- **Profit-split defaults and per-trade overrides** (plan §6). The disclosure
  table is the natural place to hang per-trade overrides, but nothing computes a
  split yet.
- **Mobile release manager.** That is Phase 6.
- **A live broker equity feed.** Reconciliation and snapshots take the figure the
  admin types in. By design, the investor module never reads MT5. Wiring the
  bot's account equity into a nightly snapshot job belongs with the nightly
  `assert_cache_matches_ledger()` job (Phase 7).
- **Actor names.** The audit and adjustment tables show the admin's user id. A
  lookup to email is a small follow-up.
- **`is_admin` is not in the login response**, so the Investors link shows for
  every signed-in user of the admin app. The server refuses non-admins with 403
  either way.

## Next

Phase 3 is the investor web app: investor auth with its own token audience,
dashboard, deposit and withdrawal requests, statements, and the published trade
list. The server side of most of it already exists: `reconcile.investor_statement`,
`fund.request_withdrawal` with the cap and exception path, `disclosure.published`
and `fund.request_closure(actor_kind="investor")`.
