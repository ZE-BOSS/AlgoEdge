"""
backend/data/models.py

SQLAlchemy ORM models for all database tables.
PostgreSQL backend hosted on Railway.
Source: TradingBot_MasterPlan-2.md Section 5 — Database Schema
"""

from sqlalchemy import (
    Column, Integer, BigInteger, Float, Text, String, Boolean, LargeBinary,
    ForeignKey, Index, UniqueConstraint, DateTime, func,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime


class Base(DeclarativeBase):
    pass


# ── OHLCV History ────────────────────────────────────────────────────────────

class OHLCV(Base):
    __tablename__ = "ohlcv"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
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
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=False, index=True)
    strategy_id = Column(String(50), nullable=False, default="SMC_v1")
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
    mt5_ticket = Column(BigInteger)
    created_at = Column(DateTime, server_default=func.now())
    positions = relationship("TradePosition", back_populates="parent_trade", cascade="all, delete-orphan")


# ── Trade Sub-Positions (Multi-TP) ───────────────────────────────────────────

class TradePosition(Base):
    __tablename__ = "trade_positions"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
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
    id = Column(BigInteger, primary_key=True, autoincrement=True)
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
    trade_id = Column(BigInteger)
    entry_snapshot = Column(Text)  # Path to entry chart PNG
    session = Column(String(20))  # LONDON, NY, OVERLAP, 24/7
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

    # Standard Broker (forex, commodities, indices)
    mt5_account = Column(BigInteger)
    mt5_password_encrypted = Column(LargeBinary)  # Fernet encrypted
    mt5_server = Column(String(100))
    mt5_path = Column(String(500))

    # Deriv Broker (synthetics)
    deriv_mt5_account = Column(BigInteger)
    deriv_mt5_password_encrypted = Column(LargeBinary)  # Fernet encrypted
    deriv_mt5_server = Column(String(100))
    deriv_mt5_path = Column(String(500))

    active_strategy = Column(String(50), default="SMC_v1")
    risk_per_trade = Column(Float, default=1.0)
    max_daily_loss = Column(Float, default=5.0)
    allowed_symbols = Column(Text)  # JSON array
    is_active = Column(Boolean, default=True)
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
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    params_snapshot = Column(Text, nullable=False)  # JSON
    total_trades = Column(Integer)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    sharpe_ratio = Column(Float)
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
    created_at = Column(DateTime, server_default=func.now())
    trades = relationship("BacktestTrade", back_populates="backtest_run", cascade="all, delete-orphan")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    backtest_id = Column(String(36), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(20))
    direction = Column(String(10))
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
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
    confluence_score = Column(Integer)
    session = Column(String(20))
    llm_analysis = Column(Text)
    backtest_run = relationship("BacktestRun", back_populates="trades")


# ── Compounding State ────────────────────────────────────────────────────────

class CompoundingStateModel(Base):
    __tablename__ = "compounding_state"
    user_id = Column(String(36), primary_key=True)
    current_step = Column(Integer, default=1)
    risk_amount = Column(Float, default=20.0)
    entry_balance = Column(Float, default=0.0)
    consecutive_wins = Column(Integer, default=0)
    consecutive_losses = Column(Integer, default=0)
    total_wins_at_level = Column(Integer, default=0)
    total_losses_at_level = Column(Integer, default=0)
    last_step_change_reason = Column(String(30), default="INIT")
    last_step_change_balance = Column(Float, default=0.0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CompoundingEvent(Base):
    __tablename__ = "compounding_events"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=False, index=True)
    event_type = Column(String(40), nullable=False)  # ADVANCE, DOWNGRADE_THRESHOLD, etc.
    from_step = Column(Integer)
    to_step = Column(Integer)
    from_risk = Column(Float)
    to_risk = Column(Float)
    balance_at_event = Column(Float)
    trade_id = Column(String(36))
    created_at = Column(DateTime, server_default=func.now())


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
