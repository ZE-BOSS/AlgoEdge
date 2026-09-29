"""
backend/investor/models.py

The investor ledger: who holds what share of the pool, and every movement that
got them there.

THE ONE RULE THIS SCHEMA ENFORCES
---------------------------------
`unit_transactions` is APPEND-ONLY and is the source of truth. Nothing else is.
`investor_units.units` is a cache that must be reproducible by replaying the
ledger, and `units.assert_cache_matches_ledger()` checks exactly that — if they
ever disagree, the platform has a bug and we want to find out that night rather
than at withdrawal time.

So: no `updated_at` on a ledger row, no UPDATE path in the code, and a mistake
is corrected by writing a compensating row, never by editing history. That is
what lets any investor statement be rebuilt from first principles years later.

WHY UNITS AND NOT PERCENTAGES
-----------------------------
An investor's stake is a number of UNITS, not a percentage of capital. NAV per
unit moves with the pool's P&L; a deposit buys units at that day's NAV.

Percentage-of-capital only works if nobody ever deposits or withdraws at a
different time. A and B put in $1,000 each, the pool trades to $3,000, C then
deposits $1,000: by capital share C owns 25%, and 25% of the $1,400 lifetime
profit is $350 — $250 of which was earned before C's money existed. Nothing in
the UI would look wrong. With units, C simply buys in at the higher NAV and the
arithmetic cannot produce that result.

MONEY IS NUMERIC, NEVER FLOAT
-----------------------------
`Numeric(18, 2)` for money and `Numeric(24, 8)` for units. Float arithmetic on
money is how a fund ends up a cent out on every statement and cannot say why.
Everything in units.py and nav.py works in `decimal.Decimal` for the same
reason.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)

from backend.data.models import Base, generate_id

# Column types, named once so they cannot drift apart between tables.
MONEY = Numeric(18, 2)
UNITS = Numeric(24, 8)
NAV = Numeric(18, 8)


# ── who ──────────────────────────────────────────────────────────────────────

class Investor(Base):
    """A person with money in the pool.

    Deliberately NOT the `users` table. `users` are operators of the trading
    system with an MT5 account attached and `is_admin`; an investor must never
    be able to reach a trading route by having a row in the same table and a
    flag flipped. Different table, different auth audience, different routes.
    """
    __tablename__ = "investors"

    id = Column(String(36), primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(40))
    country = Column(String(80))

    # Phase 3 adds login; Phase 1 investors are created by an admin and have no
    # credentials at all, which is why this is nullable.
    password_hash = Column(String(255))

    # pending -> active -> closing -> closed
    status = Column(String(20), nullable=False, default="pending", index=True)
    kyc_status = Column(String(20), nullable=False, default="none")

    # Where a withdrawal is paid. Changing these is a sensitive action: it is
    # the single most-abused flow in any investment platform, so it is logged to
    # `adjustments` and (from Phase 4) emails the investor and starts a
    # cooling-off period.
    payout_bank_name = Column(String(120))
    payout_account_number = Column(String(64))
    payout_account_name = Column(String(120))

    # The settings version in force when they joined. A later change to the
    # lock-up or notice period must not retroactively trap money committed under
    # the old terms, so the terms travel with the commitment.
    terms_version = Column(Integer)
    # per-investor terms, set by the admin; NULL = the fund's terms apply
    performance_fee_pct = Column(Numeric(9, 4))
    management_fee_pct = Column(Numeric(9, 4))
    min_investment = Column(MONEY)

    created_at = Column(DateTime, server_default=func.now())
    activated_at = Column(DateTime)
    closed_at = Column(DateTime)


# ── the ledger ───────────────────────────────────────────────────────────────

KIND_SUBSCRIBE = "SUBSCRIBE"      # money in -> units issued
KIND_REDEEM = "REDEEM"            # units cancelled -> money out
KIND_FEE = "FEE"                  # units cancelled to pay a fee
KIND_CORRECTION = "CORRECTION"    # a compensating row; history is never edited

LEDGER_KINDS = (KIND_SUBSCRIBE, KIND_REDEEM, KIND_FEE, KIND_CORRECTION)


class UnitTransaction(Base):
    """One movement of units. APPEND-ONLY — never updated, never deleted.

    `units` is SIGNED: positive issues, negative cancels. Summing this column
    for an investor gives their holding, which is the definition the cache must
    reproduce and the reason the sign lives here rather than being implied by
    `kind`.
    """
    __tablename__ = "unit_transactions"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    investor_id = Column(String(36), ForeignKey("investors.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)

    units = Column(UNITS, nullable=False)          # signed
    nav_per_unit = Column(NAV, nullable=False)     # the NAV this executed at
    amount = Column(MONEY, nullable=False)         # money moved, always positive

    # The WAT accounting date this belongs to (see nav.accounting_date).
    effective_date = Column(Date, nullable=False, index=True)

    # What caused it: ("deposit", 123), ("withdrawal", 45), ("fee_accrual", 9).
    source_kind = Column(String(24))
    source_id = Column(BigInteger)

    terms_version = Column(Integer)
    note = Column(Text)
    created_by = Column(String(36))                # admin user id, or null for system
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_unit_tx_investor_date", "investor_id", "effective_date"),
        # One ledger row per source event. Without this a retried admin click,
        # or a webhook delivered twice, issues the units twice — and because the
        # ledger is append-only there is no clean way to take them back.
        UniqueConstraint("source_kind", "source_id", name="uq_unit_tx_source"),
    )


class InvestorUnits(Base):
    """Cached unit balance. Derived — rebuildable from `unit_transactions`.

    Exists so a dashboard does not sum the whole ledger on every request. It is
    never the answer to "how many units does this investor hold?" in code that
    moves money; that question goes to the ledger.
    """
    __tablename__ = "investor_units"

    investor_id = Column(String(36), ForeignKey("investors.id"), primary_key=True)
    units = Column(UNITS, nullable=False, default=0)
    rebuilt_at = Column(DateTime, server_default=func.now())


# ── NAV ──────────────────────────────────────────────────────────────────────

class NavSnapshot(Base):
    """The pool's value on one accounting day.

    Taken once per WAT day. `units_in_issue` is stored rather than recomputed so
    a historic statement reproduces exactly, even after later corrections.
    """
    __tablename__ = "nav_snapshots"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    as_of_date = Column(Date, nullable=False, unique=True, index=True)

    pool_equity = Column(MONEY, nullable=False)    # broker equity, all accounts
    liabilities = Column(MONEY, nullable=False, default=0)   # confirmed-but-unpaid withdrawals, accrued fees
    units_in_issue = Column(UNITS, nullable=False)
    nav_per_unit = Column(NAV, nullable=False)

    source = Column(String(20), nullable=False, default="MT5")   # MT5 | MANUAL
    created_at = Column(DateTime, server_default=func.now())


# ── money in ─────────────────────────────────────────────────────────────────

DEPOSIT_REQUESTED = "requested"
DEPOSIT_CLAIMED = "claimed_sent"
DEPOSIT_CONFIRMED = "confirmed"
DEPOSIT_REJECTED = "rejected"

METHOD_BANK_TRANSFER = "BANK_TRANSFER"
METHOD_OFF_PLATFORM = "OFF_PLATFORM"      # already paid; admin records it


class Deposit(Base):
    """Money arriving.

    `amount_claimed` is what the investor says they sent; `amount_confirmed` is
    what actually landed. **Units are issued against the confirmed amount at the
    confirmed date's NAV** — the money was not at risk until it arrived, and the
    investor's claim is not evidence.
    """
    __tablename__ = "deposits"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    investor_id = Column(String(36), ForeignKey("investors.id"), nullable=False, index=True)

    method = Column(String(24), nullable=False, default=METHOD_BANK_TRANSFER)
    amount_claimed = Column(MONEY)
    amount_confirmed = Column(MONEY)

    # Base currency is USD. A deposit taken in another currency records the rate
    # used, so the conversion can be audited rather than re-derived later.
    currency = Column(String(8), nullable=False, default="USD")
    fx_rate_to_usd = Column(Numeric(18, 8))

    reference_code = Column(String(32), index=True)   # unique per investor, for bank matching
    proof_path = Column(String(512))

    state = Column(String(20), nullable=False, default=DEPOSIT_REQUESTED, index=True)
    effective_date = Column(Date)                     # the WAT day units were issued
    rejected_reason = Column(Text)

    confirmed_by = Column(String(36))
    confirmed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())


# ── money out ────────────────────────────────────────────────────────────────

WITHDRAWAL_REQUESTED = "requested"
WITHDRAWAL_EXCEPTION = "exception_pending"
WITHDRAWAL_APPROVED = "approved"
WITHDRAWAL_PAID = "paid"
WITHDRAWAL_DECLINED = "declined"


class Withdrawal(Base):
    """Money leaving.

    Capped at `withdrawal_cap_pct` of the investor's profit for the current WAT
    month (default 30%, admin-settable). Above the cap the request is not
    refused — it becomes an exception needing a written justification and a
    separate approval, which is the behaviour asked for.

    `cap_at_request` stores the cap that applied at the moment of asking, so a
    later settings change cannot make a historic decision look arbitrary.
    """
    __tablename__ = "withdrawals"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    investor_id = Column(String(36), ForeignKey("investors.id"), nullable=False, index=True)

    amount_requested = Column(MONEY, nullable=False)
    amount_paid = Column(MONEY)

    destination_bank_name = Column(String(120))
    destination_account_number = Column(String(64))
    destination_account_name = Column(String(120))

    state = Column(String(24), nullable=False, default=WITHDRAWAL_REQUESTED, index=True)
    is_exception = Column(Boolean, nullable=False, default=False)
    justification = Column(Text)
    cap_at_request = Column(MONEY)
    declined_reason = Column(Text)

    effective_date = Column(Date)
    approved_by = Column(String(36))
    approved_at = Column(DateTime)
    paid_at = Column(DateTime)
    payment_reference = Column(String(120))
    created_at = Column(DateTime, server_default=func.now())


# ── fees ─────────────────────────────────────────────────────────────────────

FEE_MANAGEMENT = "MANAGEMENT"
FEE_PERFORMANCE = "PERFORMANCE"


class FeeAccrual(Base):
    """A fee owed by one investor for one period.

    Performance fees carry a HIGH-WATER MARK per investor: an investor is only
    charged on new profit above their own previous peak, so a loss must be
    earned back before they are charged again. Storing the mark on the accrual
    means the figure a statement showed can always be reproduced.
    """
    __tablename__ = "fee_accruals"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    investor_id = Column(String(36), ForeignKey("investors.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)

    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    basis_amount = Column(MONEY, nullable=False)     # the value the rate was applied to
    rate_pct = Column(Numeric(9, 4), nullable=False)
    fee_amount = Column(MONEY, nullable=False)
    high_water_mark = Column(NAV)                    # performance fees only

    # charged: units cancelled, money still in the broker account (a liability)
    # paid:    the manager has taken it out of the broker account
    # waived:  calculated and recorded, deliberately not charged
    state = Column(String(20), nullable=False, default="accrued")
    charged_at = Column(DateTime)
    paid_at = Column(DateTime)
    days = Column(Integer)                           # days the fee covers (management)
    decided_by = Column(String(36))
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("investor_id", "kind", "period_start", "period_end",
                         name="uq_fee_period"),
    )


# ── settings, versioned ──────────────────────────────────────────────────────

class FundSettings(Base):
    """The fund's terms, versioned rather than mutated.

    A lock-up and a notice period are promises to an investor. Editing them in
    place would silently rewrite the terms of money already committed, so a
    change writes a NEW version and each commitment records the version it was
    made under (`Investor.terms_version`, `UnitTransaction.terms_version`).
    """
    __tablename__ = "fund_settings"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    version = Column(Integer, nullable=False, unique=True)

    performance_fee_pct = Column(Numeric(9, 4), nullable=False, default=20)
    management_fee_pct = Column(Numeric(9, 4), nullable=False, default=5)      # of each two-month period's profit
    withdrawal_cap_pct = Column(Numeric(9, 4), nullable=False, default=30)     # of the month's profit
    min_investment = Column(MONEY, nullable=False, default=200)
    lockup_days = Column(Integer, nullable=False, default=30)
    notice_days = Column(Integer, nullable=False, default=7)
    base_currency = Column(String(8), nullable=False, default="USD")

    # Where investors are told to send a bank transfer.
    bank_name = Column(String(120))
    bank_account_number = Column(String(64))
    bank_account_name = Column(String(120))
    bank_instructions = Column(Text)

    effective_from = Column(DateTime, server_default=func.now())
    created_by = Column(String(36))
    created_at = Column(DateTime, server_default=func.now())


# ── the audit spine ──────────────────────────────────────────────────────────

class Adjustment(Base):
    """Any admin override of a value an investor can see.

    There are legitimate reasons for an investor-facing number to differ from
    the raw broker number — a deposit that cleared the bank but is not yet
    funded to the broker, fees, rounding. Those are adjustments, and every
    serious fund has them.

    What separates an adjustment from a misrepresentation is that it is
    recorded, attributed, reasoned and reversible. `reason` is NOT NULL for
    exactly that purpose. There is no silent-overwrite path anywhere in this
    module.
    """
    __tablename__ = "adjustments"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    entity_type = Column(String(40), nullable=False)
    entity_id = Column(String(64), nullable=False)
    field = Column(String(64), nullable=False)

    old_value = Column(Text)
    new_value = Column(Text)
    reason = Column(Text, nullable=False)

    actor_id = Column(String(36), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_adjust_entity", "entity_type", "entity_id"),)


class InvestorAuditLog(Base):
    """Every state change on the investor side, immutable."""
    __tablename__ = "investor_audit_log"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    actor_id = Column(String(36))
    actor_kind = Column(String(16), nullable=False, default="admin")   # admin | investor | system
    action = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(40))
    entity_id = Column(String(64))
    detail = Column(JSON)
    ip = Column(String(64))
    created_at = Column(DateTime, server_default=func.now(), index=True)


# ── what investors are shown of the trading ──────────────────────────────────

DISCLOSURE_PUBLISHED = "published"
DISCLOSURE_HIDDEN = "hidden"
# A closed trade with no row here is "undisclosed" — it is in the admin's queue.
# There is deliberately no stored "undisclosed" state: a new trade must not need
# a write on the trading side to appear in the queue, because the investor side
# never writes to the trading side.


class TradeDisclosure(Base):
    """One closed trade as investors see it — or the decision not to show it.

    Holds its OWN copy of the published fields rather than a view onto `trades`.
    What an investor was shown is a statement the fund made; a later change to
    the trade row (a re-sync, a corrected fill) must not silently change what
    they were told. An admin edit to a published field writes an `adjustments`
    row, like every other change to an investor-facing number.

    There is no strategy, parameter, entry-logic, ticket or volume column, on
    purpose. Strategy identity is stripped HERE, at storage, so it cannot leak
    through an API response someone inspects — see disclosure.PUBLIC_FIELDS.
    """
    __tablename__ = "trade_disclosures"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    trade_id = Column(BigInteger, nullable=False, unique=True, index=True)

    state = Column(String(16), nullable=False)          # published | hidden

    symbol = Column(String(20))
    direction = Column(String(10))                      # BUY / SELL
    closed_on = Column(Date)
    result_amount = Column(MONEY)                       # pool P&L on the trade, USD
    result_pct = Column(Numeric(9, 4))                  # of pool equity, when known
    note = Column(Text)                                 # optional investor-facing comment

    edited = Column(Boolean, nullable=False, default=False)
    decided_by = Column(String(36), nullable=False)
    decided_at = Column(DateTime, server_default=func.now())


# ── investor credentials ─────────────────────────────────────────────────────

TOKEN_INVITE = "invite"        # first password, from a link the admin sends
TOKEN_RESET = "reset"          # forgotten password (emailed from Phase 4)


class InvestorToken(Base):
    """A single-use link token: an invitation to set a first password, or a reset.

    Only the SHA-256 of the token is stored. The raw token exists once, in the
    link, so a leaked database row cannot be replayed as a login link.
    """
    __tablename__ = "investor_tokens"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    investor_id = Column(String(36), ForeignKey("investors.id"), nullable=False, index=True)
    purpose = Column(String(16), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    created_by = Column(String(36))
    created_at = Column(DateTime, server_default=func.now())


# ── email, jobs, admin devices (Phase 4) ─────────────────────────────────────

class EmailLog(Base):
    """Every message the platform sends, and what became of it.

    Written for every message, including in log mode, so "did they get the
    email?" is answered from a row: when, to whom, which template, the
    provider's id (to trace a bounce), and the error if it failed.
    """
    __tablename__ = "email_log"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    kind = Column(String(40), nullable=False, index=True)       # template name
    to_address = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    investor_id = Column(String(36), index=True)
    state = Column(String(16), nullable=False, default="queued")  # queued | sent | logged | failed
    provider_id = Column(String(120))
    error = Column(Text)
    attachments = Column(JSON)                                    # filenames only
    created_at = Column(DateTime, server_default=func.now(), index=True)
    sent_at = Column(DateTime)


class JobRun(Base):
    """One row per scheduled job per day, so a job runs once however many
    worker processes are up: the second insert hits the unique constraint."""
    __tablename__ = "investor_job_runs"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    job = Column(String(40), nullable=False)
    run_key = Column(String(40), nullable=False)                  # e.g. the WAT date
    result = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("job", "run_key", name="uq_job_run"),)


class AdminDevice(Base):
    """A browser an admin has signed in from, for the new-device alert."""
    __tablename__ = "admin_devices"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    user_id = Column(String(36), nullable=False, index=True)
    fingerprint = Column(String(64), nullable=False)
    ip = Column(String(64))
    user_agent = Column(String(300))
    first_seen = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "fingerprint", name="uq_admin_device"),)


# ── the public website (Phase 5) ─────────────────────────────────────────────

class Application(Base):
    """Someone applying through the website. Not an investor until the admin
    creates one — an application moves no money and grants no access."""
    __tablename__ = "investor_applications"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(40))
    country = Column(String(80))
    amount_band = Column(String(40))
    message = Column(Text)
    status = Column(String(16), nullable=False, default="new", index=True)  # new | contacted | accepted | declined
    investor_id = Column(String(36))                                         # once accepted
    ip = Column(String(64))
    created_at = Column(DateTime, server_default=func.now(), index=True)
    decided_by = Column(String(36))


# ── the Android app (Phase 6) ────────────────────────────────────────────────

class AppRelease(Base):
    """One uploaded build of the investor app.

    `version_code` is the integer Android compares to decide whether an install
    is an upgrade; it must rise with every release. The app checks the current
    release on launch and offers the download when it is behind, because a
    sideloaded app gets no store updates. `sha256` is shown next to the download
    so a careful investor can confirm the file is the one we published.
    """
    __tablename__ = "app_releases"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    platform = Column(String(16), nullable=False, default="android")
    version_name = Column(String(32), nullable=False)
    version_code = Column(Integer, nullable=False)
    filename = Column(String(200), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), nullable=False)
    notes = Column(Text)
    is_current = Column(Boolean, nullable=False, default=False)
    uploaded_by = Column(String(36))
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("platform", "version_code", name="uq_release_code"),)


class InvestorDevice(Base):
    """A phone with the app installed, for push notifications."""
    __tablename__ = "investor_devices"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    investor_id = Column(String(36), ForeignKey("investors.id"), nullable=False, index=True)
    push_token = Column(String(200), nullable=False, unique=True)
    platform = Column(String(16))
    app_version = Column(String(32))
    last_seen = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
