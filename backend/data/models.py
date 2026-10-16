"""
backend/data/models.py

SQLAlchemy ORM models for all database tables.
PostgreSQL backend hosted on Railway.
Source: TradingBot_MasterPlan-2.md Section 5 — Database Schema
"""

import itertools
import threading
import time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


_id_lock = threading.Lock()
_id_seq = itertools.count()


def generate_id():
    """Generates a unique integer ID: millisecond timestamp + a process-local sequence.

    Previously used a millisecond timestamp plus a random 2-digit suffix
    (`random.randint(0, 99)`), which collided whenever multiple rows were created within
    the same millisecond — e.g. the 3 TP-leg trade_positions rows for one signal, built
    back-to-back in a loop — causing UNIQUE constraint failures on bulk insert. A
    monotonically-increasing sequence guarantees uniqueness for calls within one process,
    regardless of how many happen in the same millisecond (wrapped mod 1000, which is far
    above any realistic per-millisecond insert rate for this app).
    """
    with _id_lock:
        seq = next(_id_seq) % 1000
    return int(time.time() * 1000) * 1000 + seq


# ── OHLCV History ────────────────────────────────────────────────────────────

class OHLCV(Base):
    __tablename__ = "ohlcv"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    timestamp = Column(BigInteger, nullable=False)  # Unix epoch ms
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp"),
        Index("idx_ohlcv_lookup", "symbol", "timeframe", "timestamp"),
    )


# ── Trades ───────────────────────────────────────────────────────────────────

class Trade(Base):
    __tablename__ = "trades"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    user_id = Column(String(36), nullable=False, index=True)
    strategy_id = Column(String(50), nullable=False, default="VWAP_v1")
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)  # BUY / SELL
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    volume = Column(Float)
    entry_time = Column(DateTime)
    exit_time = Column(DateTime)
    pnl = Column(Float)
    pnl_pips = Column(Float)
    risk_reward = Column(Float)
    status = Column(String(20), default="OPEN")  # OPEN, CLOSED, CANCELLED
    exit_reason = Column(String(20))  # TP, SL, MANUAL, TRAIL
    max_drawdown = Column(Float)  # Max adverse excursion
    entry_snapshot = Column(Text)  # path to entry chart PNG
    exit_snapshot = Column(Text)   # path to exit chart PNG
    balance_before = Column(Float)
    balance_after = Column(Float)
    session = Column(String(20))            # LONDON, NY, LONDON/NY, ASIAN, 24/7
    session_close_time = Column(DateTime)   # when that session ends (UTC), None for 24/7
    confluence_score = Column(Integer)
    chart_data = Column(Text)
    mt5_ticket = Column(BigInteger)
    # [2026-09-27] The circuit breaker's signal-group id, so a LIVE position can
    # be matched back to the group its slot's profit target is measured on. The
    # breaker keys everything by group; without this, a live target could only be
    # resolved by symbol, which is ambiguous the moment a slot holds two groups.
    # Nullable: rows written before this change simply have no group.
    group_id = Column(String(36), index=True)
    # [2026-10-08] WHICH MT5 ACCOUNT THIS TRADE BELONGS TO.
    #
    # Until now the only account identifier was `User.mt5_account`, so every
    # historical row silently re-attributed the moment the connected login
    # changed: reconnect to a different account and the whole journal claims to
    # be that account's history. The circuit breaker has guarded against this
    # since account scoping was added (`reset_for_new_account`); the journal
    # never did.
    #
    # It also makes the ledger possible at all. Reconciling the journal against
    # "the MT5 balance" is meaningless while a row cannot say which balance.
    #
    # Nullable on purpose: rows written before this cannot be attributed with
    # certainty, and they are shown as "account unknown" rather than guessed
    # into an account. Measured on the VPS database — 23 of 94 rows carry an
    # mt5_ticket, so most of the pre-bot block will stay unattributed, which is
    # the honest answer.
    mt5_account = Column(BigInteger, index=True)
    created_at = Column(DateTime, server_default=func.now())
    positions = relationship("TradePosition", back_populates="parent_trade", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_trades_user_status", "user_id", "status"),
    )


# ── Trade Sub-Positions (Multi-TP) ───────────────────────────────────────────

class TradePosition(Base):
    __tablename__ = "trade_positions"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    parent_trade_id = Column(BigInteger, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), nullable=False)
    tp_level = Column(Integer, nullable=False)  # 1, 2, 3, 4, or 5
    mt5_ticket = Column(BigInteger)
    volume = Column(Float)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    planned_rr = Column(Float)
    realized_rr = Column(Float)
    pnl = Column(Float)
    status = Column(String(20))
    exit_price = Column(Float)
    exit_time = Column(DateTime)
    exit_reason = Column(String(30))  # SL, TP1..TP5, TRAIL, BE, CLIENT
    pnl_pips = Column(Float)
    be_applied = Column(Boolean, default=False)
    trail_method = Column(String(30))
    trail_activated = Column(Boolean, default=False)
    mae_pips = Column(Float)  # Maximum Adverse Excursion
    mfe_pips = Column(Float)  # Maximum Favorable Excursion
    created_at = Column(DateTime, server_default=func.now())
    parent_trade = relationship("Trade", back_populates="positions")


# ── Signals ──────────────────────────────────────────────────────────────────

class Signal(Base):
    __tablename__ = "signals"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    user_id = Column(String(36), nullable=False, index=True)
    strategy_id = Column(String(50), nullable=False)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10))
    signal_type = Column(String(30))  # OB_ENTRY, FVG_ENTRY, BOS, CHOCH
    timeframe = Column(String(10))
    price_at_signal = Column(Float)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    tp1_price = Column(Float)
    tp2_price = Column(Float)
    tp3_price = Column(Float)
    tp4_price = Column(Float)
    tp5_price = Column(Float)
    ob_top = Column(Float)
    ob_bottom = Column(Float)
    fvg_top = Column(Float)
    fvg_bottom = Column(Float)
    htf_bias = Column(String(10))
    confluence_score = Column(Integer)
    confluence_breakdown = Column(Text)  # JSON: {"htf_bias": 15, "sweep": 15, ...}
    acted_on = Column(Boolean, default=False)
    skip_reason = Column(String(200))  # Rejection reason if skipped
    trade_id = Column(BigInteger, ForeignKey("trades.id"))
    entry_snapshot = Column(Text)  # Path to entry chart PNG
    chart_data = Column(Text)
    session = Column(String(20))  # LONDON, NY, OVERLAP, 24/7
    session_close_time = Column(DateTime)  # when that session ends (UTC), None for 24/7
    signal_time = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "timeframe", "signal_time"),
    )


# ── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True)  # UUID
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(100), nullable=False)

    # Standard Broker (forex, commodities, indices, synthetics)
    mt5_account = Column(BigInteger)
    mt5_password_encrypted = Column(LargeBinary)  # Fernet encrypted
    mt5_server = Column(String(100))
    mt5_path = Column(String(500))

    license_key = Column(String(100))
    subscription_status = Column(String(50), default="active")
    active_strategy = Column(String(50), default="VWAP_v1")
    risk_per_trade = Column(Float, default=1.0)
    max_daily_loss = Column(Float, default=5.0)
    allowed_symbols = Column(Text)  # JSON array
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


# ── Performance Stats ────────────────────────────────────────────────────────

class PerformanceStats(Base):
    __tablename__ = "performance_stats"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=False, index=True)
    strategy_id = Column(String(50), nullable=False)
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    total_trades = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)
    win_rate = Column(Float)
    total_pnl = Column(Float)
    max_drawdown = Column(Float)
    sharpe_ratio = Column(Float)
    profit_factor = Column(Float)
    avg_rr = Column(Float)
    best_trade = Column(Float)
    worst_trade = Column(Float)
    max_consec_wins = Column(Integer)
    max_consec_losses = Column(Integer)
    tp1_hit_rate = Column(Float)
    tp2_hit_rate = Column(Float)
    tp3_hit_rate = Column(Float)
    tp4_hit_rate = Column(Float)
    tp5_hit_rate = Column(Float)
    sl_hit_rate = Column(Float)
    trail_hit_rate = Column(Float)
    be_hit_rate = Column(Float)
    london_win_rate = Column(Float)
    ny_win_rate = Column(Float)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ── Backtest Runs ────────────────────────────────────────────────────────────

class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id = Column(String(36), primary_key=True)  # UUID
    user_id = Column(String(36), nullable=False, index=True)
    strategy_id = Column(String(50), nullable=False)
    symbol = Column(String(20), nullable=False)
    # User-supplied title, decoupled from `symbol`. Falls back to `symbol` at
    # read time (see backtest.py) for old rows saved before this column
    # existed, and remains a sensible default for single-symbol runs — but
    # for portfolio runs `symbol` isn't a meaningful title at all, so this
    # lets the user set one explicitly instead of the UI defaulting to it.
    title = Column(String(200), nullable=True)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    params_snapshot = Column(Text, nullable=False)  # JSON
    total_trades = Column(Integer)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    sharpe_ratio = Column(Float)
    sortino_ratio = Column(Float)
    expectancy_r = Column(Float)
    max_drawdown_pct = Column(Float)
    total_pnl = Column(Float)
    tp1_hit_rate = Column(Float)
    tp2_hit_rate = Column(Float)
    tp3_hit_rate = Column(Float)
    tp4_hit_rate = Column(Float)
    tp5_hit_rate = Column(Float)
    be_hit_rate = Column(Float)
    sl_hit_rate = Column(Float)
    trail_hit_rate = Column(Float)
    notes = Column(Text)
    llm_analysis = Column(Text)
    run_logs = Column(Text)  # JSON serialized logs
    rejection_funnel = Column(JSON, default={})
    # [Phase 13 section C.7] Continuous per-leg bar series for replay-mode
    # scrubbing, downsampled to MAX_SERIES_BARS. Distinct from each trade's
    # own chart_data, which is a +/-30-bar slice and cannot show the run as a
    # whole. Nullable: runs saved before Phase 13 simply have none.
    replay_data = Column(Text)  # JSON
    # Computed once at save time (see runner.py) from the full in-memory
    # grouped trades — including original_signal/metadata, which are
    # deliberately stripped before persistence (see BacktestTrade below /
    # runner.py's _slim_sub_trades). by_confirmation in particular can never
    # be correctly regenerated from the saved trades alone, so it must be
    # captured here rather than recomputed on every GET /backtests/{id}.
    bias_stats = Column(JSON, default={})
    confluence_stats = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    trades = relationship(
        "BacktestTrade",
        back_populates="backtest_run",
        cascade="all, delete-orphan",
        order_by="BacktestTrade.entry_time",
    )


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    backtest_id = Column(String(36), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(20))
    direction = Column(String(10))
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    balance_before = Column(Float)
    balance_after = Column(Float)
    tp1_price = Column(Float)
    tp2_price = Column(Float)
    tp3_price = Column(Float)
    tp4_price = Column(Float)
    tp5_price = Column(Float)
    entry_time = Column(DateTime)
    exit_time = Column(DateTime)
    tp_level_hit = Column(Integer)
    exit_reason = Column(String(20))
    pnl = Column(Float)
    pnl_r = Column(Float)
    planned_rr = Column(Float)
    realized_rr = Column(Float)
    be_applied = Column(Boolean, default=False)
    trail_method = Column(String(30))
    mae_pips = Column(Float)
    mfe_pips = Column(Float)
    # Excursion in R (multiples of the initial stop distance). Pips are not
    # comparable across symbols, so these are the fields the confluence study
    # ranks on: "does this setup actually reach 1R / 2R, and how often?"
    mae_r = Column(Float)
    mfe_r = Column(Float)
    risk_pips = Column(Float)
    confluence_score = Column(Integer)
    session = Column(String(20))
    strategy_id = Column(String(50))
    llm_analysis = Column(Text)
    
    # Chart & SMC Data
    chart_data = Column(Text)
    # [B6] H1 context slice for the trade viewer's higher-timeframe pane.
    # The save paths were wired to write this before the column existed,
    # which made every save raise
    # "'chart_data_h1' is an invalid keyword argument for BacktestTrade"
    # AFTER the run had already completed — the run finished, reported its
    # P&L, then failed to persist.
    chart_data_h1 = Column(Text)
    chart_data_m15 = Column(Text)
    chart_data_m5 = Column(Text)
    smc_data = Column(Text)
    sub_trades = Column(Text)
    
    backtest_run = relationship("BacktestRun", back_populates="trades")


# ── LLM Analyses ─────────────────────────────────────────────────────────────

class LLMAnalysis(Base):
    __tablename__ = "llm_analyses"
    id = Column(String(36), primary_key=True)  # UUID
    user_id = Column(String(36), nullable=False, index=True)
    context_type = Column(String(30), nullable=False)  # TRADE, SERIES, BACKTEST, CUSTOM
    source_id = Column(String(36))
    provider = Column(String(20), nullable=False)  # anthropic, openai, gemini
    model = Column(String(50), nullable=False)
    analysis_text = Column(Text, nullable=False)
    user_question = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


# ── Push Subscriptions ───────────────────────────────────────────────────────

class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id = Column(String(36), primary_key=True)  # UUID
    user_id = Column(String(36), nullable=False, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(Text, nullable=False)
    auth = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


# ── API Keys (Encrypted) ────────────────────────────────────────────────────

class APIKey(Base):
    __tablename__ = "api_keys"
    user_id = Column(String(36), primary_key=True)
    provider = Column(String(20), primary_key=True)  # anthropic, openai, gemini
    encrypted_key = Column(LargeBinary, nullable=False)
    verified = Column(Boolean, default=False)


# ── User Config ──────────────────────────────────────────────────────────────

class UserConfigModel(Base):
    __tablename__ = "user_configs"
    user_id = Column(String(36), primary_key=True)
    config_json = Column(Text, nullable=False)  # Full UserConfig as JSON
    preset_name = Column(String(30))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

# ── What happened AFTER a trade was closed by hand ───────────────────────────

class TradeAftermath(Base):
    """The counterfactual for a trade that a person or an agent closed early.

    WHY THIS TABLE EXISTS
    A trade that hits its stop or its target needs no second look: the plan
    decided it. A trade someone CLOSED - because it looked wrong, or because
    the day ended, or because an agent judged it - has an unanswered question
    attached to it. Would it have reached the target? How far did it get
    before it turned? The journal records the exit and nothing after it, so
    the one decision the operator actually makes by hand is the one decision
    never scored.

    That is also the single richest training signal available for exit
    policy: every row is a labelled example of a human or agent exit against
    what the market went on to do.

    One row per trade, written by `services/trade_aftermath.py`. Rows stay
    `watch_complete = False` and are revisited until the watch window has
    fully elapsed, so a trade closed an hour ago is measured again tomorrow
    rather than being judged on the one hour that existed at the time.
    """
    __tablename__ = "trade_aftermath"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    trade_id = Column(BigInteger, ForeignKey("trades.id", ondelete="CASCADE"),
                      nullable=False, unique=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    symbol = Column(String(20), index=True)
    strategy_id = Column(String(50), index=True)
    direction = Column(String(10))

    # -- the close being scored ------------------------------------------
    closed_at = Column(DateTime, index=True)
    close_reason = Column(String(32), index=True)   # MANUAL, CLIENT, ...
    closed_by = Column(String(16))                  # USER / AGENT / BOT / UNKNOWN
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    closed_r = Column(Float)        # what the close actually banked, in R

    # -- the watch window -------------------------------------------------
    watch_until = Column(DateTime)  # closed_at + the max a trade may be held
    watch_hours = Column(Float)     # how long that window is, for the record
    watch_complete = Column(Boolean, default=False, index=True)
    bars_seen = Column(Integer, default=0)

    # -- what the market did next -----------------------------------------
    hit_tp = Column(Boolean, default=False)
    tp_at = Column(DateTime)
    tp_minutes = Column(Integer)    # from the close to the target
    hit_sl = Column(Boolean, default=False)
    sl_at = Column(DateTime)
    sl_minutes = Column(Integer)
    best_price = Column(Float)      # furthest IN FAVOUR within the window
    best_r = Column(Float)
    best_at = Column(DateTime)
    best_minutes = Column(Integer)
    r_at_watch_end = Column(Float)  # where it stood when the window ran out

    # -- the verdict, in one word ------------------------------------------
    # LEFT_MONEY  the trade went on to do materially better than the close
    # SAVED_LOSS  it went on to the stop, so closing early was right
    # FAIR        neither, within the noise band
    verdict = Column(String(16), index=True)
    forgone_r = Column(Float)       # best_r - closed_r; negative means saved
    note = Column(Text)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ── What a signal's own plan would have done ────────────────────────────────

class SignalOutcome(Base):
    """Every signal scored against its OWN entry, stop and first target.

    WHY THIS EXISTS ALONGSIDE TradeAftermath
    The trade aftermath scores what someone closed. This scores what the
    GATES decided. A skipped signal is recorded with the gate that stopped it
    and nothing more, so nobody can tell whether a filter is saving money or
    costing it - and earlier research in this project found shipped gates that
    were actively harmful. One row per signal, taken or skipped, gives:

      * for SKIPPED signals, the counterfactual: what the gate gave up
      * for TAKEN signals, the plan-as-written outcome, independent of how the
        trade was later managed - a clean "was the signal right" label

    The fill is ASSUMED at `entry_price` at signal time. A signal whose entry
    was never actually reached would be scored as if it had been; `note` says
    so, and that is the main way these numbers can flatter a gate.
    """
    __tablename__ = "signal_outcomes"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    signal_id = Column(BigInteger, ForeignKey("signals.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    symbol = Column(String(20), index=True)
    strategy_id = Column(String(50), index=True)
    timeframe = Column(String(10))
    direction = Column(String(10))
    acted_on = Column(Boolean, index=True)
    skip_gate = Column(String(80), index=True)      # the first clause of skip_reason
    signal_time = Column(DateTime, index=True)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)                     # tp1: the nearest target
    watch_until = Column(DateTime)
    watch_hours = Column(Float)
    watch_complete = Column(Boolean, default=False, index=True)
    bars_seen = Column(Integer, default=0)
    hit_tp = Column(Boolean, default=False)
    tp_minutes = Column(Integer)
    hit_sl = Column(Boolean, default=False)
    sl_minutes = Column(Integer)
    best_r = Column(Float)
    # What following the plan would have banked: +R at the target, -1 at the
    # stop, or where it stood when the window ran out.
    outcome_r = Column(Float)
    ambiguous_bar = Column(Boolean, default=False)
    note = Column(Text)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
