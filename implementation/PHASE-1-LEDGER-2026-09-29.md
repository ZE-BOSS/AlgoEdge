# Phase 1 — the ledger

**2026-09-29** · `backend/investor/` · `tests/test_investor_ledger.py`,
`tests/test_investor_fund.py` · 54 tests

Phase 1 of `INVESTOR-PLATFORM-2026-09-29.md`. No investor login yet, by design —
once the ledger is right everything else is presentation, and if it is wrong
everything else is confidently-presented wrong numbers.

---

## What exists now

| File | Holds |
|---|---|
| `models.py` | 10 tables: investors, the unit ledger, NAV snapshots, deposits, withdrawals, fee accruals, versioned settings, adjustments, audit log |
| `nav.py` | NAV arithmetic and the WAT accounting day. All `Decimal` |
| `units.py` | issue / cancel units, replay the ledger, detect cache drift |
| `fund.py` | settings versioning, daily NAV snapshot, deposit and withdrawal state machines, the withdrawal cap |
| `reconcile.py` | raw vs investor-facing, and a single investor's statement |

`backend/data/database.py` now imports the investor models inside `init_db`, so
`create_all` actually builds the tables. A model registers with
`Base.metadata` only when its module is imported — without that line the tables
are silently skipped and the first deposit fails against a table that does not
exist. All ten are confirmed registered.

---

## The four rules the code enforces

### 1. The ledger is the truth; everything else is a cache

`unit_transactions` is append-only. There is no UPDATE path anywhere in the
module, no `updated_at` on a ledger row, and a mistake is fixed by writing a
**compensating row**, never by editing history. `investor_units` is a cache that
must be reproducible by replay, and `assert_cache_matches_ledger()` proves it.

Two consequences that cost money if you get them wrong, both tested:

- **The overdraw check reads the ledger, not the cache.** A stale cache must
  never be able to authorise a payout. There is a test that corrupts the cache
  to say an investor is rich and confirms the redemption is still refused.
- **Reconciliation is unhealthy while the cache drifts**, even when the totals
  balance. Totals can agree while one investor's figure is wrong, and that is
  precisely the failure that stays hidden until somebody withdraws.

### 2. Units, not percentages

The worked example from §0 is now an executable test. A and B put in $1,000
each, the pool trades to $3,000, C deposits $1,000:

```
A: 10 units × 150 = $1,500      (keeps the profit they were in for)
C:  6.66666666 × 150 ≈ $1,000   (buys in at the higher NAV, gains nothing)
```

The test asserts C's lifetime gain **cannot** reach the $250 that
percentage-of-capital would have handed them out of A and B's pockets.

Two smaller decisions in the same spirit:

- **Units round DOWN.** Rounding up would issue a sliver nobody paid for on
  every deposit, diluting every existing holder by a hair each time. The residue
  stays in the pool.
- **A deposit too small to buy one unit is refused**, not accepted and rounded
  to zero units. Taking money and issuing nothing is theft by rounding.

### 3. Money is priced when it arrives

Units are issued for the **confirmed amount** at the **confirmed date's** NAV.
The investor's claim of what they sent is not evidence, and the money was not at
risk until it landed — issuing at the claim date would hand them profit the pool
earned while the transfer was in flight.

The same mechanism handles onboarding: `record_admin_deposit(..., on=<date>)`
prices a historic deposit at the NAV that applied then. There is a test that a
deposit dated before a doubling of the pool buys units at the old price.

### 4. Nothing is silently overwritten

`Adjustment.reason` is `NOT NULL`. `correct()` refuses an empty reason and
refuses a zero-unit correction. Every state change writes `investor_audit_log`.
There is no silent-overwrite path in the module, which is what separates an
adjustment from a misrepresentation.

---

## Your decisions, as implemented

| Decision | Where it lives |
|---|---|
| Unitisation | `unit_transactions`, the whole module |
| Performance **and** management fee | `FundSettings.performance_fee_pct` / `management_fee_pct`, `FeeAccrual` with a per-investor high-water mark |
| Cap = **30% of the month's profit**, admin-settable | `withdrawal_cap()`; the percentage is `FundSettings.withdrawal_cap_pct` |
| USD base | `FundSettings.base_currency`; `Deposit.fx_rate_to_usd` records the rate for any non-USD payment |
| $200 min / 30-day lock-up / 7-day notice, all updatable | `FundSettings`, versioned |

**The cap is on monthly profit, so a flat month leaves nothing withdrawable.**
`CapCheck.explanation` returns the sentence the UI must show — *"no profit this
month, so nothing is available under the standard limit — this needs an
exception request"* — rather than a disabled button with no reason.

**Money paid in during the month is capital, not profit**, and is excluded from
the cap. Otherwise a fresh deposit could be cycled straight back out as though
it were a gain. Tested.

**Over the cap is not a refusal.** With a written justification the request
becomes `exception_pending` and goes to a separate queue. Refusing outright
would push the conversation off-platform where there is no record of it.

**Terms are versioned, never edited.** A lock-up and a notice period are
promises; editing them in place would rewrite the terms of money already
committed. `new_settings_version()` writes a new row, old versions stay
readable, and `Investor.terms_version` / `UnitTransaction.terms_version` record
which terms each commitment was made under.

---

## Guards worth knowing about

- **A deposit cannot issue units twice.** `UNIQUE(source_kind, source_id)` on
  the ledger. A retried admin click or a doubly-delivered webhook is rejected at
  the database — and because the ledger is append-only there would be no clean
  way to take duplicated units back.
- **An approved withdrawal cannot be approved again** (it would cancel the units
  twice) or declined afterwards (they are already cancelled — write a
  correction).
- **Approval and payment are separate states.** Approving cancels units;
  `mark_withdrawal_paid` records the transfer reference. Paying an unapproved
  withdrawal is refused.
- **A full exit works in units, not cash**, so the holding lands on exactly
  zero. Converting to money and back leaves dust behind on almost every exit,
  and someone paid out in full should not still appear in the register.
- **A wiped-out pool is priced at zero**, not floored at something comfortable.
  Flooring would let redemptions pay out money the fund does not have.
- **The accounting day is WAT**, matching the trading side. A fund whose "today"
  disagrees with the bot's "today" produces a daily P&L that reconciles to
  nothing.

---

## Tests

**54, all passing.** `test_investor_ledger.py` (25) covers the NAV arithmetic,
the worked example, overdraw, full exit, idempotency, drift detection and
corrections. `test_investor_fund.py` (29) covers settings versioning, deposit
pricing, the cap in five scenarios, the withdrawal state machine and four
reconciliation cases.

Every failure mode above has a test that fails without the guard. The
reconciliation tests in particular assert the checks **catch** something — a
reconciliation that cannot go red is decoration.

---

## Not in Phase 1

Deliberately: no investor login, no routes, no UI, no email, no fee *calculation*
(the table and the high-water-mark column exist; the accrual job does not), no
PDF statements. Those are Phases 2–4.

One thing to schedule when the app runs this for real: **the nightly
`assert_cache_matches_ledger()` job**. The function exists and is tested; nothing
calls it on a timer yet. It wants to run after the NAV snapshot and alert on any
non-empty result.

---

## Next

Phase 2 is the admin console — the Investors section, the deposit / withdrawal /
exception / closure queues, trade disclosure, the reconciliation screen and the
audit viewer. It is all reads and state transitions over what now exists, and
`reconcile.build()` already returns the shape the screen needs.
