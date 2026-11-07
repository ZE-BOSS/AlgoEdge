"""
backend/copytrade/models.py

Tables for Telegram copy trading.

    copytrade_settings   one row per user: the Telegram login (encrypted), the
                         risk rules, and whether copying is on
    copytrade_channels   the channels being watched, each with optional
                         overrides of the risk rules and a symbol map
    copytrade_signals    every message seen on a watched channel, what it was
                         read as, and what was done about it
    copytrade_orders     the MT5 orders / positions opened for a signal, one
                         row per TP leg - what follow-ups (BE, move SL, close)
                         act on

Registered with Base.metadata on import; backend/data/database.init_db imports
this module so create_all builds the tables.
"""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Float, Index, Integer, String, Text, func

from backend.data.models import Base, generate_id


class CopyTradeSettings(Base):
    __tablename__ = "copytrade_settings"
    user_id = Column(String(36), primary_key=True)
    enabled = Column(Boolean, default=False)          # watcher running and acting
    dry_run = Column(Boolean, default=True)           # read and log, place nothing
    # Telegram user login (MTProto). api_hash and the session string are encrypted.
    api_id = Column(Integer, nullable=True)
    api_hash_enc = Column(Text, nullable=True)
    phone = Column(String(32), nullable=True)
    session_enc = Column(Text, nullable=True)
    tg_user = Column(String(128), nullable=True)      # "First Last (@name)" once logged in
    # Rules - see copytrade.rules.DEFAULT_RULES for the meaning of each key
    rules = Column(JSON, default=dict)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CopyTradeChannel(Base):
    __tablename__ = "copytrade_channels"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    user_id = Column(String(36), nullable=False, index=True)
    chat_id = Column(BigInteger, nullable=False)      # Telegram peer id (-100... for channels)
    title = Column(String(256), default="")
    username = Column(String(128), nullable=True)
    enabled = Column(Boolean, default=True)
    rules = Column(JSON, default=dict)                # overrides of the user's rules
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (Index("idx_ct_channel_user_chat", "user_id", "chat_id", unique=True),)


class CopyTradeSignal(Base):
    __tablename__ = "copytrade_signals"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    user_id = Column(String(36), nullable=False, index=True)
    channel_id = Column(BigInteger, nullable=False, index=True)
    chat_id = Column(BigInteger, nullable=False)
    msg_id = Column(BigInteger, nullable=False)
    reply_to = Column(BigInteger, nullable=True)
    edited = Column(Boolean, default=False)
    text = Column(Text, default="")
    kind = Column(String(16), default="NONE")
    parsed = Column(JSON, default=dict)
    # For a follow-up: the OPEN signal it acted on
    root_signal_id = Column(BigInteger, nullable=True, index=True)
    symbol = Column(String(64), nullable=True)        # broker symbol, for OPENs
    status = Column(String(16), default="SEEN")       # SEEN / EXECUTED / PARTIAL / SKIPPED / ERROR / IGNORED / DRY_RUN
    detail = Column(Text, default="")
    actions = Column(JSON, default=list)              # what was sent to MT5, one line each
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (Index("idx_ct_signal_msg", "chat_id", "msg_id"),)


class CopyTradeOrder(Base):
    __tablename__ = "copytrade_orders"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    user_id = Column(String(36), nullable=False, index=True)
    signal_id = Column(BigInteger, nullable=False, index=True)
    channel_id = Column(BigInteger, nullable=False)
    leg = Column(Integer, default=1)                  # 1 = TP1 leg ...
    ticket = Column(BigInteger, nullable=True)        # MT5 order ticket (= position id once filled)
    kind = Column(String(8), default="MARKET")        # MARKET / LIMIT / STOP
    symbol = Column(String(64), nullable=False)
    side = Column(String(4), nullable=False)
    volume = Column(Float, default=0.0)
    entry = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)
    status = Column(String(12), default="OPEN")       # PENDING / OPEN / CLOSED / CANCELLED / FAILED / PAPER
    detail = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
