"""
backend/groups/models.py

Signal groups (General / VIP / VVIP Telegram channels run by the group bot).

Registered with Base.metadata on import; backend/data/database.init_db imports
this module so create_all builds the tables.
"""
from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Float, Index, Integer, String, Text, func

from backend.data.models import Base, generate_id


class GroupSettings(Base):
    """One row: everything the admin can change about the group business."""
    __tablename__ = "group_settings"
    id = Column(Integer, primary_key=True, default=1)
    data = Column(JSON, default=dict)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SignalGroup(Base):
    """A Telegram channel or group the bot posts in."""
    __tablename__ = "signal_groups"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    chat_id = Column(BigInteger, nullable=False, unique=True)
    title = Column(String(200), default="")
    chat_type = Column(String(20), default="")        # group / supergroup / channel
    tier = Column(String(10), default="")             # GENERAL / VIP / VVIP ("" until the admin says)
    posting = Column(Boolean, default=False)          # off until the admin switches it on
    bot_status = Column(String(20), default="")       # administrator / member / left / kicked
    can_post = Column(Boolean, default=False)
    can_invite = Column(Boolean, default=False)
    can_delete = Column(Boolean, default=False)
    discussion_chat_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class SignalRoute(Base):
    """Which source feeds which group. The source is admin-only: it never
    appears in a post."""
    __tablename__ = "signal_routes"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    group_id = Column(BigInteger, nullable=False, index=True)
    source_type = Column(String(10), nullable=False)  # strategy / channel
    source_key = Column(String(100), nullable=False)  # strategy id, or copy-trading channel id
    symbol = Column(String(64), nullable=True)        # None = every symbol of that source
    enabled = Column(Boolean, default=True)
    sample_every = Column(Integer, default=1)         # 1 = every signal; 5 = one in five
    delay_s = Column(Integer, default=0)              # post this many seconds late
    lifecycle = Column(String(10), default="full")    # full / basic (entry + result only)
    headsups = Column(Boolean, default=True)
    max_losing_streak = Column(Integer, default=5)    # 0 = never pause
    paused_reason = Column(String(200), default="")
    counter = Column(Integer, default=0)              # signals seen, for sampling
    created_at = Column(DateTime, server_default=func.now())


class GroupSignal(Base):
    """One signal as posted in one group, with its thread and outcome."""
    __tablename__ = "group_signals"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    group_id = Column(BigInteger, nullable=False, index=True)
    route_id = Column(BigInteger, nullable=True, index=True)
    key = Column(String(80), nullable=False, index=True)    # strategy trade group / copier root / heads-up key
    kind = Column(String(10), default="SIGNAL")             # SIGNAL / HEADSUP
    symbol = Column(String(64), default="")
    side = Column(String(4), default="")
    entry = Column(Float, nullable=True)
    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    tps = Column(JSON, default=list)
    tps_hit = Column(JSON, default=list)
    status = Column(String(16), default="OPEN")             # QUEUED / OPEN / CLOSED / CANCELLED / WITHDRAWN / SKIPPED
    result_r = Column(Float, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    text = Column(Text, default="")
    send_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    closed_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_group_signals_group_key", "group_id", "key"),)


class GroupPost(Base):
    """Every message the bot sent or queued (signals, replies, daily posts)."""
    __tablename__ = "group_posts"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    chat_id = Column(BigInteger, nullable=False, index=True)
    kind = Column(String(24), default="")
    text = Column(Text, default="")
    reply_to = Column(BigInteger, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    status = Column(String(12), default="QUEUED")           # QUEUED / SENT / FAILED
    error = Column(String(300), default="")
    signal_id = Column(BigInteger, nullable=True)
    send_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    sent_at = Column(DateTime, nullable=True)


class GroupMember(Base):
    """A person in VIP / VVIP (or asking to join)."""
    __tablename__ = "group_members"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    telegram_id = Column(BigInteger, nullable=False, unique=True)
    username = Column(String(100), default="")
    name = Column(String(200), default="")
    tier = Column(String(10), default="")                   # VIP / VVIP
    level = Column(String(10), default="")                  # VVIP: STARTER / PRO / ELITE
    status = Column(String(16), default="NEW")              # NEW / ASKED / PENDING / ACTIVE / WARNED / REMOVED / DENIED
    step = Column(String(20), default="")                   # where the join chat is
    broker = Column(String(40), default="")
    mt5_login = Column(String(40), default="")
    balance = Column(Float, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    note = Column(String(300), default="")
    user_id = Column(String(36), nullable=True)             # investor-app account, once linked
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LLMDraft(Base):
    """An engagement post written by the LLM, waiting for the admin or posted."""
    __tablename__ = "group_llm_drafts"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    group_id = Column(BigInteger, nullable=True)
    kind = Column(String(24), default="")                   # morning / midday / summary / weekly / education / script
    text = Column(Text, default="")
    status = Column(String(12), default="PENDING")          # PENDING / POSTED / SKIPPED / ADMIN (scripts)
    admin_message_id = Column(BigInteger, nullable=True)
    facts = Column(JSON, default=dict)
    created_at = Column(DateTime, server_default=func.now())


class BotKV(Base):
    """Small persistent values (update offset, sampling counters)."""
    __tablename__ = "group_bot_kv"
    key = Column(String(60), primary_key=True)
    value = Column(Text, default="")


class ClientAccount(Base):
    """A VVIP client's MT5 account traded by the bot (phase 5)."""
    __tablename__ = "vvip_accounts"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    member_id = Column(BigInteger, nullable=True, index=True)
    user_id = Column(String(36), nullable=True, index=True)
    level = Column(String(10), default="STARTER")           # STARTER / PRO / ELITE
    broker = Column(String(40), default="")
    mt5_login = Column(String(40), default="")
    mt5_server = Column(String(100), default="")
    mt5_password_encrypted = Column(Text, default="")
    risk_pct = Column(Float, default=1.0)
    daily_loss_pct = Column(Float, default=5.0)
    max_dd_pct = Column(Float, default=20.0)
    symbols = Column(JSON, default=list)
    instance = Column(String(40), default="")               # the server instance trading it, once provisioned
    state = Column(String(16), default="NEW")               # NEW / PROVISIONING / ACTIVE / PAUSED / STOPPED
    paused_reason = Column(String(200), default="")
    terms_accepted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Payment(Base):
    """A VVIP payment or renewal (phase 5)."""
    __tablename__ = "vvip_payments"
    id = Column(BigInteger, primary_key=True, default=generate_id)
    member_id = Column(BigInteger, nullable=True, index=True)
    user_id = Column(String(36), nullable=True)
    provider = Column(String(20), default="")                # paystack / manual / usdt
    reference = Column(String(120), default="", unique=True)
    amount = Column(Float, default=0.0)
    currency = Column(String(8), default="USD")
    level = Column(String(10), default="")
    period_days = Column(Integer, default=30)
    status = Column(String(12), default="PENDING")           # PENDING / PAID / FAILED
    raw = Column(JSON, default=dict)
    created_at = Column(DateTime, server_default=func.now())
