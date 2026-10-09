"""
backend/mt5/cash_models.py

What money did, per broker account: the tables the reconciliation reads.

WHY THESE EXIST
---------------
`Trade` records what the bot did. Nothing recorded what the ACCOUNT did —
deposits, withdrawals, the prop firm's allocation, commission, swap, a broker
correction — so the journal could show a profit while the balance said
something else, and the investor ledger had no broker figure to reconcile
against at all. That gap is measurable: on the VPS database investors are told
the pool holds $6,110.62 while the trading account holds $3,903.11, and nothing
in the system says so.

THREE TABLES, THREE JOBS
------------------------
`MT5Account`     which accounts exist, and how each one books capital. The
                 classification is DATA because it differs per broker — see
                 `DEFAULT_DEAL_CLASSES`.
`MT5CashFlow`    one row per MT5 deal, classified, append-only, idempotent.
`OpeningAnchor`  where the ledger starts and what the balance was, because MT5
                 history does not go back far enough on every account.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No investor concepts. `backend/investor/` must not import MT5 — a bug in the
investor ledger must not be able to reach a live position — so these tables are
the interface between the two halves, and `backend/investor/sources.py` reads
them without ever touching the terminal.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from backend.data.models import Base, generate_id

# ── how a deal is classified ────────────────────────────────────────────────

CAPITAL_IN = "CAPITAL_IN"            # money arrived from outside
CAPITAL_OUT = "CAPITAL_OUT"          # money left
COST = "COST"                        # commission, swap, interest, tax
CREDIT_NOT_CAPITAL = "CREDIT_NOT_CAPITAL"   # broker credit or bonus: not withdrawable
TRADE_RESULT = "TRADE_RESULT"        # the profit or loss of a position
ADJUSTMENT = "ADJUSTMENT"            # a balance correction or a demo reset
UNKNOWN = "UNKNOWN"                  # must be reviewed before the books balance

CLASSES = (CAPITAL_IN, CAPITAL_OUT, COST, CREDIT_NOT_CAPITAL,
           TRADE_RESULT, ADJUSTMENT, UNKNOWN)

# MT5 deal types, read from the installed package rather than written from
# memory. Kept as integers so this module imports with no terminal present.
DEAL_BUY, DEAL_SELL = 0, 1
DEAL_BALANCE, DEAL_CREDIT, DEAL_CHARGE, DEAL_CORRECTION, DEAL_BONUS = 2, 3, 4, 5, 6
DEAL_COMMISSION_FIRST, DEAL_COMMISSION_LAST = 7, 11
DEAL_INTEREST = 12
DEAL_BUY_CANCELED, DEAL_SELL_CANCELED = 13, 14
DEAL_DIVIDEND, DEAL_DIVIDEND_FRANKED, DEAL_TAX = 15, 16, 17

# The default map. MEASURED on both terminals 2026-10-08, not assumed:
#
#   Deriv 6194570      capital arrives as DEAL_BALANCE (2)
#   FundedNext 11869564 capital arrives as DEAL_CHARGE (4), "Initial+Deposit"
#
# which is why this is a default and not a rule. A `type == 2` filter — the
# obvious correction to the old `type >= 2` — misses FundedNext's entire
# $25,000. `MT5Account.deal_class_overrides` is how an account says otherwise.
DEFAULT_DEAL_CLASSES: dict[int, str] = {
    DEAL_BUY: TRADE_RESULT,
    DEAL_SELL: TRADE_RESULT,
    DEAL_BALANCE: CAPITAL_IN,          # by sign: negative becomes CAPITAL_OUT
    DEAL_CREDIT: CREDIT_NOT_CAPITAL,
    DEAL_CHARGE: COST,                 # unless the account says it funds this way
    DEAL_CORRECTION: UNKNOWN,          # a broker fix; could be either, so ask
    DEAL_BONUS: CREDIT_NOT_CAPITAL,
    DEAL_INTEREST: COST,
    DEAL_BUY_CANCELED: COST,
    DEAL_SELL_CANCELED: COST,
    DEAL_DIVIDEND: TRADE_RESULT,
    DEAL_DIVIDEND_FRANKED: TRADE_RESULT,
    DEAL_TAX: COST,
    **{t: COST for t in range(DEAL_COMMISSION_FIRST, DEAL_COMMISSION_LAST + 1)},
}

DEAL_TYPE_NAMES: dict[int, str] = {
    0: "BUY", 1: "SELL", 2: "BALANCE", 3: "CREDIT", 4: "CHARGE", 5: "CORRECTION",
    6: "BONUS", 7: "COMMISSION", 8: "COMMISSION_DAILY", 9: "COMMISSION_MONTHLY",
    10: "COMMISSION_AGENT_DAILY", 11: "COMMISSION_AGENT_MONTHLY", 12: "INTEREST",
    13: "BUY_CANCELED", 14: "SELL_CANCELED", 15: "DIVIDEND",
    16: "DIVIDEND_FRANKED", 17: "TAX",
}


class MT5Account(Base):
    """One broker account the platform knows about.

    Every account is REAL. There is no simulated/live flag: a demo account is
    used to rehearse what live will do, and a rehearsal whose numbers are
    computed differently rehearses nothing. What differs between accounts is
    how the broker books capital, and that is `deal_class_overrides`.
    """

    __tablename__ = "mt5_accounts"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    login = Column(BigInteger, nullable=False, unique=True, index=True)
    label = Column(String(120))                 # "Deriv main", "FundedNext challenge"
    broker = Column(String(120))                # terminal_info().company
    server = Column(String(120))                # account_info().server
    currency = Column(String(8), default="USD")

    # Whether this account's money backs investor units. A prop-firm allocation
    # is the firm's money under a profit split, so counting it as capital
    # available to investors would overstate the fund; only the profit share is
    # ours. Off by default — an account has to be chosen for this.
    holds_investor_capital = Column(Boolean, nullable=False, default=False)
    # A prop allocation, tracked so it can be shown and excluded in one place.
    prop_allocation = Column(Float, default=0.0)
    profit_share_pct = Column(Float)            # our share of this account's profit

    # {"4": "CAPITAL_IN"} — deal type (as a string key, for JSON) to class.
    # This is the field that makes the classifier data rather than a rule.
    deal_class_overrides = Column(Text)
    # Comment patterns that mean "this is not a deposit, it is a reset".
    # Deriv's demo resets arrive as DEAL_BALANCE with a comment, and both a
    # top-up and a deduction carry the SAME comment, so the text cannot decide
    # direction — only that it is an ADJUSTMENT rather than investor capital.
    adjustment_comment_patterns = Column(Text)  # JSON list of lower-case substrings

    is_active = Column(Boolean, nullable=False, default=True)
    last_synced_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())


class OpeningAnchor(Base):
    """Where this account's ledger starts, and what the balance was there.

    Needed because MT5 history is not always complete. Measured: the Deriv
    account was reset on 2026-08-05, its deal history begins 2026-08-01, and
    everything before that is unrecoverable from the terminal — a `BALANCE`
    deal records the ADJUSTMENT, never the balance it adjusted.

    Normally derived rather than typed: `source='journal'` anchors at the first
    trade the bot took, using that row's `balance_before`, which is what "start
    from when the bot started trading" means. `source='manual'` is for an
    account whose history does not reach even that far.
    """

    __tablename__ = "mt5_opening_anchors"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    account_login = Column(BigInteger, nullable=False, index=True)
    as_of = Column(DateTime, nullable=False)
    balance = Column(Float, nullable=False)

    source = Column(String(16), nullable=False, default="journal")  # journal | manual | deal
    note = Column(Text)
    # The Trade row this was taken from, so the figure stays traceable to a row
    # somebody can click rather than being a number that appeared.
    from_trade_id = Column(BigInteger)
    entered_by = Column(String(36))
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("account_login", name="uq_anchor_per_account"),
    )


class MT5CashFlow(Base):
    """One MT5 deal, classified.

    APPEND-ONLY AND IDEMPOTENT, the same shape `unit_transactions` uses: the
    unique key is the broker's own deal ticket, so a re-sync — which happens on
    every start and on demand — cannot double-count a deposit.

    Both the raw figures and the classification are stored. The raw ones are
    what the broker said and never change; the classification is ours and can
    be corrected, which is why `classified_by` and `classified_at` exist.
    """

    __tablename__ = "mt5_cash_flows"

    id = Column(BigInteger, primary_key=True, default=generate_id)
    account_login = Column(BigInteger, nullable=False, index=True)
    deal_ticket = Column(BigInteger, nullable=False)

    # exactly as MT5 reported it
    deal_type = Column(Integer, nullable=False)
    deal_entry = Column(Integer)
    position_id = Column(BigInteger, index=True)
    order_ticket = Column(BigInteger)
    symbol = Column(String(32))
    volume = Column(Float)
    price = Column(Float)
    profit = Column(Float, nullable=False, default=0.0)
    commission = Column(Float, nullable=False, default=0.0)
    swap = Column(Float, nullable=False, default=0.0)
    fee = Column(Float, nullable=False, default=0.0)
    comment = Column(String(255))
    magic = Column(BigInteger)
    # Broker-server time as MT5 gave it, and the UTC we derived. Both, because
    # the offset is a guess that can be revised and the raw value cannot.
    deal_time = Column(DateTime, nullable=False, index=True)
    deal_time_utc = Column(DateTime, nullable=False, index=True)

    # ours
    flow_class = Column(String(24), nullable=False, index=True)
    # profit + commission + swap + fee: the signed amount this deal moved the
    # balance by. Stored so the reconciliation is a SUM and not a formula
    # repeated at every call site.
    amount = Column(Float, nullable=False, default=0.0)
    classified_by = Column(String(32))          # "default" | "override" | an admin id
    classified_at = Column(DateTime, server_default=func.now())
    review_reason = Column(Text)                # why this landed in UNKNOWN

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        # The whole idempotency guarantee, in one line.
        UniqueConstraint("account_login", "deal_ticket", name="uq_cash_flow_deal"),
        Index("ix_cash_flow_account_time", "account_login", "deal_time_utc"),
    )
