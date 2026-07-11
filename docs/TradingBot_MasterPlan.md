# AlgoEdge Trading Bot — Master Architecture & Implementation Plan
### MT5-Integrated | SMC Strategy | Multi-User | Real-Time | AI-Extensible

---

## Executive Summary

This document is the complete engineering and product blueprint for building a **zero-cost, locally-hosted, production-grade algorithmic trading bot** that connects to MetaTrader 5 (MT5), implements Smart Money Concepts (SMC) as its first strategy, serves multiple users with isolated strategy execution, delivers real-time data to a web dashboard over WebSockets, and is architecturally prepared for future AI/ML/RL and LLM extensions.

The target for the MVP is a system that is demonstrably profitable at a **65–70%+ win rate** through rigorous backtesting before going live, and that captures full analytics: entry/exit charts, Sharpe ratio, drawdown, win rate, risk/reward ratios, and trade-by-trade snapshots.

### Document Index

| Document | Purpose |
|----------|---------|
| `TradingBot_MasterPlan.md` *(this file)* | System architecture, tech stack, database design, infrastructure, build roadmap, AI/ML/RL extensions |
| `SMC_Strategy.md` | Complete SMC strategy: market structure, OB/FVG detection, liquidity mapping, sniper entry model (6-step), candlestick confirmation bible (Tier 1–3), confluence scoring (0–100), all algorithmic parameters |

Both documents are required. The master plan governs **how** the system is built. The strategy document governs **what** the system trades on.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Why This Tech Stack](#2-why-this-tech-stack)
3. [The MT5 Integration Layer](#3-the-mt5-integration-layer)
4. [SMC Strategy Engine (MVP Strategy)](#4-smc-strategy-engine-mvp-strategy)
5. [The Data Pipeline & Database Design](#5-the-data-pipeline--database-design)
6. [Real-Time Backend — FastAPI + WebSocket + Redis](#6-real-time-backend--fastapi--websocket--redis)
7. [Risk Management System — Full Specification](#7-risk-management-system)
8. [Frontend — PWA Dashboard](#8-frontend--pwa-dashboard)
9. [LLM Integration — AI Trade Analysis](#9-llm-integration--ai-trade-analysis)
10. [Analytics Engine — Chart Snapshots & Trade Metrics](#10-analytics-engine--chart-snapshots--trade-metrics)
11. [Backtesting Engine — Full Specification](#11-backtesting-engine--full-specification)
12. [Multi-User Architecture](#12-multi-user-architecture)
13. [Project Folder Structure](#13-project-folder-structure)
14. [MVP Build Roadmap (Phased Plan)](#14-mvp-build-roadmap-phased-plan)
15. [Extension Roadmap — AI/ML/RL Layer](#15-extension-roadmap--aimllrl-layer)
16. [Competitive Landscape](#16-competitive-landscape)
17. [Zero-Cost Infrastructure Plan](#17-zero-cost-infrastructure-plan)

---

## 1. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER'S LOCAL DESKTOP/PC                       │
│                                                                       │
│  ┌─────────────────┐    ┌──────────────────────────────────────────┐ │
│  │  MetaTrader 5   │◄──►│           PYTHON CORE ENGINE             │ │
│  │  (Broker GUI)   │    │  ┌────────────┐  ┌───────────────────┐  │ │
│  │                 │    │  │ MT5 Bridge │  │  Strategy Engine  │  │ │
│  │  - Live Feeds   │    │  │ (mt5 lib)  │  │  (SMC Detector)   │  │ │
│  │  - Order Exec   │    │  └────────────┘  └───────────────────┘  │ │
│  │  - Account Data │    │  ┌────────────┐  ┌───────────────────┐  │ │
│  └─────────────────┘    │  │ Trade Exec │  │  Analytics Engine │  │ │
│                         │  └────────────┘  └───────────────────┘  │ │
│                         └──────────────┬───────────────────────────┘ │
│                                        │                              │
│  ┌─────────────────────────────────────▼──────────────────────────┐  │
│  │              FASTAPI BACKEND (Async + WebSocket)                │  │
│  │   /ws/live  →  Real-time price feed, trade signals, events     │  │
│  │   /api/*    →  REST: config, history, user management          │  │
│  └───────────────────────┬────────────────────────────────────────┘  │
│                          │                                            │
│  ┌───────────────────────▼──────────────┐  ┌──────────────────────┐  │
│  │        REDIS (In-Memory Cache)        │  │   POSTGRESQL (Railway)  │  │
│  │  - Live price ticks                  │  │  - Trade history      │  │
│  │  - Active positions                  │  │  - OHLCV snapshots    │  │
│  │  - Strategy state per user           │  │  - Performance stats  │  │
│  │  - Pub/Sub for WebSocket broadcast   │  │  - Chart images       │  │
│  └──────────────────────────────────────┘  └──────────────────────┘  │
│                                                                       │
└────────────────────────────────────────┬────────────────────────────┘
                                         │ HTTP/WebSocket (LAN or localhost)
                             ┌───────────▼──────────────┐
                             │   WEB DASHBOARD (React)   │
                             │  - Live chart (TradingView│
                             │    Lightweight Charts)    │
                             │  - Trade journal & stats  │
                             │  - Chart snapshot gallery │
                             │  - User/strategy config   │
                             └──────────────────────────┘
```

**Key Design Principle:** Everything runs locally on one machine. MT5 must be running (as it always does for live trading). The Python engine connects to MT5 via its official Python API. FastAPI serves data to the browser-based dashboard in real time. Zero cloud dependency, zero running cost.

---

## 2. Why This Tech Stack

### Backend: Python + FastAPI + Uvicorn

Python is the dominant language for algorithmic trading for a clear reason: it has the richest ecosystem of trading, ML, and data libraries anywhere. FastAPI is the fastest Python web framework for building async APIs and WebSocket servers — in benchmarks it has handled 250,000+ WebSocket messages per second with Redis Pub/Sub. Uvicorn (ASGI server) ensures non-blocking, high-concurrency execution.

**Alternatives considered and why rejected:**
- Node.js: Excellent for WebSockets, but the ML/trading library ecosystem is not comparable.
- Django: Too heavy, synchronous by default — unacceptable for real-time trading.
- Flask: Too limited for WebSocket-heavy real-time applications.

### In-Memory Speed Layer: Redis

Redis is the backbone of real-time speed. Every tick received from MT5 is pushed into Redis. Strategy signals, open positions, account state — all hot data lives in Redis. FastAPI subscribes to Redis Pub/Sub channels and streams updates to the browser the instant they land. Redis latency is sub-millisecond.

**Why Redis over a simple Python dict:** Redis persists across process restarts, supports multiple workers, and provides Pub/Sub broadcasting natively. Critical for multi-user isolation.

### Database: PostgreSQL (Railway-hosted)

For the MVP with 1–2 users, PostgreSQL is perfectly sufficient — zero infrastructure, zero cost, high performance for < millions of rows. It stores: historical OHLCV data, trade logs, performance metrics, and chart snapshot metadata.

For scale (5+ users, months of tick data), migrate to TimescaleDB (PostgreSQL extension). It handles millions of inserts per second and offers SQL compatibility — no learning curve.

### MT5 Integration: Official Python API (`MetaTrader5` package)

MetaQuotes provides an official Python package (`pip install MetaTrader5`) that connects Python directly to a running MT5 terminal on the same machine. This gives you: real-time tick data, OHLCV history at any timeframe, live order placement, position management, account info, and broker connectivity — all for free.

### Frontend: React + TradingView Lightweight Charts

TradingView's open-source Lightweight Charts library is purpose-built for financial charting. It renders 100,000+ candlesticks at 60fps in the browser. React handles state management and the dashboard UI. The connection to the backend uses native browser WebSockets for zero-latency streaming.

### Chart Snapshots: Matplotlib + mplfinance

For generating static chart snapshots at entry and exit points (saved as PNG images), Matplotlib with the `mplfinance` extension is the optimal tool. It can render a professional candlestick chart with SMC markup (order blocks, FVGs, entry/exit arrows) and save it in under 200ms — fast enough to not block trade execution.

---

## 3. The MT5 Integration Layer

### How MT5 Python API Works

```python
import MetaTrader5 as mt5

# Initialize and connect to running MT5 terminal
mt5.initialize()
mt5.login(account_id, password, server)

# Get live tick (call this in a tight async loop)
tick = mt5.symbol_info_tick("EURUSD")

# Get OHLCV history for strategy calculation
rates = mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_M15, 0, 500)

# Place a buy order
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": "EURUSD",
    "volume": 0.1,
    "type": mt5.ORDER_TYPE_BUY,
    "price": mt5.symbol_info_tick("EURUSD").ask,
    "sl": stop_loss,
    "tp": take_profit,
    "comment": "SMC_BOT_USER1",
    "magic": 20250101,  # unique ID per user/strategy
}
result = mt5.order_send(request)
```

### MT5 Bridge Service

The MT5 Bridge is a dedicated Python service that:
1. Maintains a persistent connection to the running MT5 terminal.
2. Runs a tick polling loop (~every 50–100ms per symbol) using `asyncio` — fast enough for Forex/derivatives without exceeding MT5 API rate limits.
3. On each new bar (H4, M15, M5, M5), triggers strategy evaluation for all active users.
4. Publishes all data to Redis channels for distribution.
5. Handles order placement with retry logic and error handling (slippage, requotes, off-quotes).

**Critical design note:** MT5's Python API is **not thread-safe**. All MT5 calls must happen from the same thread. The bridge runs in a dedicated `asyncio` event loop with all MT5 calls wrapped using `loop.run_in_executor` with a single-threaded `ThreadPoolExecutor`.

### Symbol Management

```python
WATCHED_SYMBOLS = {
    "EURUSD": ["H4", "M15", "M5"],
    "GBPUSD": ["H4", "M15", "M5"],
    "XAUUSD": ["H4", "M15", "M5"],   # Gold
    "US30":   ["H4", "M15", "M5"],   # Dow Jones
    "BTCUSD": ["H4", "M15", "M5"],   # Crypto derivative
}
```

---

## 4. SMC Strategy Engine (MVP Strategy)

> **Full Strategy Specification:** See `SMC_Strategy.md` — the authoritative reference document for all SMC detection logic, sniper entry rules, candlestick confirmation patterns, confluence scoring, and parameter tuning. This section provides the architectural summary only.

Smart Money Concepts (SMC) is an ICT-derived methodology that models **institutional order flow**. The core thesis (as detailed in *Mind of the Market* by Pamela Donald and backed by ICT research) is that price is driven by liquidity, and institutions engineer price moves to sweep retail stop orders before delivering price to their real target.

### SMC Component Stack (Full Detail in SMC_Strategy.md)

**1. Market Structure (BOS / ChoCH)**
- HTF (H4) BOS = trend bias confirmed. LTF (M5) ChoCH = precision entry trigger.
- Three phases: Trending → Reversal → Consolidation (no trades in consolidation).
- Macro structure always overrides micro structure — never fight H4 bias.

**2. Liquidity Mapping (BSL / SSL)**
- Equal highs = buy-side liquidity (BSL). Equal lows = sell-side liquidity (SSL).
- Inducement (IDM) traps early entries before the real OB zone. The bot waits.
- Internal liquidity (FVGs) fills before external liquidity is targeted.

**3. Order Block (OB) Detection**
- Bullish OB = last **bearish** candle before bullish BOS impulse.
- Bearish OB = last **bullish** candle before bearish BOS impulse.
- Validity rules: tied to a BOS/ChoCH, backed by liquidity sweep, fresh (max 1 touch), impulse ≥ 2× OB size.
- Mitigation tracking: OBs invalidated after price closes inside them.

**4. Fair Value Gap (FVG) Detection**
- 3-candle imbalance: `candle[i-1].high < candle[i+1].low` (bullish FVG).
- Entry at 50% of FVG (CE — Consequent Encroachment level).
- **OB + FVG confluence = highest probability setup in the entire system.**

**5. Premium / Discount + OTE Zone**
- Equilibrium = 50% of the last swing range.
- Discount (below 50%) = buy zone. Premium (above 50%) = sell zone.
- Optimal Trade Entry (OTE) = 61.8%–78.6% Fibonacci retracement of last leg.
- Best entries sit in OTE zone, inside discount/premium, at an unmitigated OB with FVG confluence.

**6. Institutional Price Delivery Model (IPDM)**
- Phase 1 Accumulation: ATR below average, tight range → do not trade.
- Phase 2 Manipulation: Liquidity sweep (wick + close back inside) → alert mode.
- Phase 3 Expansion: ChoCH after sweep → entry zone reached → execute.
- Power of 3 daily: Asian range accumulates, London sweep manipulates, NY expands.

### Sniper Entry — The 6-Step Execution Sequence

```
Step 1: H4 Bias → BULLISH / BEARISH / NEUTRAL (no trade if neutral)
Step 2: Map liquidity pools on H4 + M15 (SSL/BSL targets)
Step 3: Identify highest-scoring POI (OB / FVG / S&D zone on M15)
Step 4: Wait for liquidity sweep at session kill zone
Step 5: LTF ChoCH on M5 → identifies precision entry OB/FVG
Step 6: Candlestick confirmation at LTF zone → execute if score ≥ 65
```

### Candlestick Confirmation Layer (from Candlestick Bible)

All entries require at least one of the following patterns at the POI on M5/M1:

**Tier 1 — Preferred (highest confidence):**
- **Bullish Engulfing:** Large bullish candle body engulfs previous bearish body → institutional buying confirmed
- **Bearish Engulfing:** Large bearish candle body engulfs previous bullish body → institutional selling confirmed
- **Hammer / Bullish Pin Bar:** Long lower wick (≥ 2× body) at SSL sweep level → rejection of lows, buyer intent
- **Shooting Star / Bearish Pin Bar:** Long upper wick (≥ 2× body) at BSL sweep level → rejection of highs, seller intent

**Tier 2 — Acceptable:**
- **Dragonfly Doji:** Open ≈ Close ≈ High, long lower wick → bullish rejection, full seller failure
- **Gravestone Doji:** Open ≈ Close ≈ Low, long upper wick → bearish rejection, full buyer failure
- **Morning Star (3-candle):** Bearish → small doji → strong bullish → multi-candle reversal confirmation
- **Evening Star (3-candle):** Bullish → small doji → strong bearish → multi-candle reversal confirmation

**Tier 3 — Secondary (requires higher confluence score):**
- **Inside Bar:** Consolidation at POI before directional break — enter on breakout close
- **Rejection Wick:** Any candle with wick ≥ 2× body at key zone level
- **Displacement Candle:** Strong body candle signaling start of ChoCH expansion

**Rejected patterns (not used):** Standard Doji (equal wicks), Spinning Tops, Tweezer tops/bottoms in isolation.

### Confluence Scoring Gate

Every signal is scored before execution. Minimum 65/100 required:

| Factor | Score |
|--------|-------|
| H4 bias confirmed | +15 |
| M15 structure aligned | +10 |
| Liquidity sweep detected | +15 |
| Fresh OB (first touch) | +15 |
| OB + FVG confluence | +10 |
| OTE zone (Fib 61.8–78.6%) | +5 |
| Tier 1 candlestick confirmation | +15 |
| LTF ChoCH confirmed | +10 |
| Active kill zone session | +5 |
| **Minimum to trade** | **≥ 65** |

### Python SMC Library

The open-source `smartmoneyconcepts` package (joshyattridge, GitHub) provides the foundation: swing detection, BOS/ChoCH, OBs, FVGs, liquidity, premium/discount. Our `SMCEngine` class wraps it adding: multi-timeframe orchestration, confluence scoring, candlestick pattern detection, validation gates, session/news filters, and Redis signal publishing.

### Session & News Filters

**Active windows (GMT):** London 07:00–10:00 (kill zone 07:00–08:30), NY 12:00–15:00 (kill zone 12:00–13:30), London/NY overlap 12:00–15:00.

**Blocked windows:** Asian session 22:00–06:00, Friday after 20:00, ±30 minutes around HIGH-impact news (ForexFactory RSS feed for free calendar data).

---

## 5. The Data Pipeline & Database Design

### Data Flow

```
MT5 Terminal
    │
    ▼ (every tick / every new bar)
MT5 Bridge (Python)
    │
    ├──► Redis: "ticks:{symbol}" → latest tick (pub/sub)
    ├──► Redis: "ohlcv:{symbol}:{timeframe}" → latest N candles (cache)
    ├──► Redis: "positions:{user_id}" → open positions
    │
    └──► PostgreSQL:
            ├── ohlcv_history table (persistent OHLCV archive)
            ├── trades table (every trade: open/close/stats)
            ├── signals table (every signal generated, acted on or not)
            └── chart_snapshots table (path to PNG files)
```

### Database Schema (PostgreSQL)

```sql
-- Core OHLCV history (used for backtesting + chart rendering)
CREATE TABLE ohlcv (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    timestamp   INTEGER NOT NULL,   -- Unix epoch ms
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    UNIQUE(symbol, timeframe, timestamp)
);
CREATE INDEX idx_ohlcv_lookup ON ohlcv(symbol, timeframe, timestamp DESC);

-- Every trade executed by the bot
CREATE TABLE trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,          -- "SMC_v1"
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,          -- "BUY" or "SELL"
    entry_price     REAL,
    exit_price      REAL,
    stop_loss       REAL,
    take_profit     REAL,
    volume          REAL,
    entry_time      INTEGER,                -- Unix epoch ms
    exit_time       INTEGER,
    pnl             REAL,                   -- Realized P&L in account currency
    pnl_pips        REAL,
    risk_reward     REAL,
    status          TEXT,                   -- "OPEN", "CLOSED", "CANCELLED"
    exit_reason     TEXT,                   -- "TP", "SL", "MANUAL", "TRAIL"
    max_drawdown    REAL,                   -- Max adverse excursion during trade
    entry_snapshot  TEXT,                   -- path to entry chart PNG
    exit_snapshot   TEXT,                   -- path to exit chart PNG
    mt5_ticket      INTEGER,                -- MT5 position ticket
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- Strategy signals (including ones not acted on — for analysis)
CREATE TABLE signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT,
    signal_type     TEXT,       -- "OB_ENTRY", "FVG_ENTRY", "BOS", "CHOCH"
    timeframe       TEXT,
    price_at_signal REAL,
    ob_top          REAL,
    ob_bottom       REAL,
    fvg_top         REAL,
    fvg_bottom      REAL,
    htf_bias        TEXT,
    acted_on        INTEGER DEFAULT 0,   -- 1 if trade was placed
    trade_id        INTEGER,
    signal_time     INTEGER,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- Users and their configurations
CREATE TABLE users (
    id              TEXT PRIMARY KEY,   -- UUID
    name            TEXT NOT NULL,
    mt5_account     INTEGER,
    mt5_password    TEXT,               -- encrypted
    mt5_server      TEXT,
    active_strategy TEXT DEFAULT 'SMC_v1',
    risk_per_trade  REAL DEFAULT 1.0,   -- % of account per trade
    max_daily_loss  REAL DEFAULT 5.0,   -- % of account max daily drawdown
    allowed_symbols TEXT,               -- JSON array
    is_active       INTEGER DEFAULT 1,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- Aggregate performance stats per user per strategy
CREATE TABLE performance_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    period_start    INTEGER,
    period_end      INTEGER,
    total_trades    INTEGER,
    winning_trades  INTEGER,
    losing_trades   INTEGER,
    win_rate        REAL,
    total_pnl       REAL,
    max_drawdown    REAL,
    sharpe_ratio    REAL,
    profit_factor   REAL,
    avg_rr          REAL,
    best_trade       REAL,
    worst_trade      REAL,
    max_consec_wins  INTEGER,
    max_consec_losses INTEGER,
    tp1_hit_rate     REAL,
    tp2_hit_rate     REAL,
    tp3_hit_rate     REAL,
    sl_hit_rate      REAL,
    trail_hit_rate   REAL,
    be_hit_rate      REAL,
    london_win_rate  REAL,
    ny_win_rate      REAL,
    updated_at       INTEGER DEFAULT (strftime('%s','now'))
);

-- Sub-positions (multi-TP) per parent trade
CREATE TABLE trade_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_trade_id INTEGER NOT NULL REFERENCES trades(id),
    user_id         TEXT NOT NULL,
    tp_level        INTEGER NOT NULL,
    mt5_ticket      INTEGER,
    volume          REAL,
    entry_price     REAL,
    stop_loss       REAL,
    take_profit     REAL,
    planned_rr      REAL,
    realized_rr     REAL,
    pnl             REAL,
    status          TEXT,
    exit_price      REAL,
    exit_time       INTEGER,
    be_applied      INTEGER DEFAULT 0,
    trail_method    TEXT,
    trail_activated INTEGER DEFAULT 0,
    mae_pips        REAL,
    mfe_pips        REAL,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- Saved backtest runs
CREATE TABLE backtest_runs (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    start_date      INTEGER NOT NULL,
    end_date        INTEGER NOT NULL,
    params_snapshot TEXT NOT NULL,
    total_trades    INTEGER,
    win_rate        REAL,
    profit_factor   REAL,
    sharpe_ratio    REAL,
    max_drawdown_pct REAL,
    total_pnl       REAL,
    tp1_hit_rate    REAL,
    tp2_hit_rate    REAL,
    tp3_hit_rate    REAL,
    be_hit_rate     REAL,
    trail_hit_rate  REAL,
    notes           TEXT,
    llm_analysis    TEXT,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- Individual trades within a saved backtest
CREATE TABLE backtest_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_id     TEXT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    symbol          TEXT,
    direction       TEXT,
    entry_price     REAL,
    exit_price      REAL,
    stop_loss       REAL,
    tp1_price       REAL,
    tp2_price       REAL,
    tp3_price       REAL,
    entry_time      INTEGER,
    exit_time       INTEGER,
    tp_level_hit    INTEGER,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_r           REAL,
    planned_rr      REAL,
    realized_rr     REAL,
    be_applied      INTEGER DEFAULT 0,
    trail_method    TEXT,
    mae_pips        REAL,
    mfe_pips        REAL,
    confluence_score INTEGER,
    session         TEXT,
    llm_analysis    TEXT
);

-- LLM analysis results
CREATE TABLE llm_analyses (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    context_type    TEXT NOT NULL,
    source_id       TEXT,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    analysis_text   TEXT NOT NULL,
    user_question   TEXT,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- Push notification subscriptions (VAPID)
CREATE TABLE push_subscriptions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    endpoint        TEXT NOT NULL UNIQUE,
    p256dh          TEXT NOT NULL,
    auth            TEXT NOT NULL,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- Encrypted LLM API keys
CREATE TABLE api_keys (
    user_id         TEXT NOT NULL,
    provider        TEXT NOT NULL,
    encrypted_key   BLOB NOT NULL,
    verified        INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, provider)
);

-- Compounding plan state per user (also cached in Redis)
CREATE TABLE compounding_state (
    user_id                 TEXT PRIMARY KEY,
    current_step            INTEGER DEFAULT 1,
    risk_amount             REAL DEFAULT 20.0,
    entry_balance           REAL DEFAULT 0.0,
    consecutive_wins        INTEGER DEFAULT 0,
    consecutive_losses      INTEGER DEFAULT 0,
    total_wins_at_level     INTEGER DEFAULT 0,
    total_losses_at_level   INTEGER DEFAULT 0,
    last_step_change_reason TEXT DEFAULT 'INIT',
    last_step_change_balance REAL DEFAULT 0.0,
    updated_at              INTEGER DEFAULT (strftime('%s','now'))
);

-- Compounding step transitions history (for analytics)
CREATE TABLE compounding_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    event_type      TEXT NOT NULL,  -- ADVANCE, DOWNGRADE_THRESHOLD, DOWNGRADE_LOSS_COUNT, MANUAL
    from_step       INTEGER,
    to_step         INTEGER,
    from_risk       REAL,
    to_risk         REAL,
    balance_at_event REAL,
    trade_id        TEXT,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

-- User risk + strategy configuration
CREATE TABLE user_configs (
    user_id         TEXT PRIMARY KEY,
    config_json     TEXT NOT NULL,
    preset_name     TEXT,
    updated_at      INTEGER DEFAULT (strftime('%s','now'))
);
```

### Redis Key Design

```
ticks:{EURUSD}           → {"bid":1.0850,"ask":1.0851,"time":1718000000}
ohlcv:{EURUSD}:{M15}      → JSON array of last 500 candles (refreshed per bar)
position:{USER1}         → JSON array of open MT5 positions for user 1
signal:latest:{USER1}    → latest signal object for user 1
account:{USER1}          → {"balance":10000,"equity":10200,"margin":200}
channel:ticks            → Pub/Sub channel (all subscribers get all ticks)
channel:trades           → Pub/Sub channel (new trade events)
channel:signals          → Pub/Sub channel (new signal events)
strategy:state:{USER1}   → current internal state of SMC engine for user 1
```

---

## 6. Real-Time Backend — FastAPI + WebSocket + Redis

### Application Structure

```python
# main.py — FastAPI application
from fastapi import FastAPI, WebSocket
from redis.asyncio import Redis
import asyncio

app = FastAPI()

# WebSocket manager — one connection pool per user
class ConnectionManager:
    def __init__(self):
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, user_id: str):
        await ws.accept()
        self.active.setdefault(user_id, []).append(ws)

    async def broadcast_to_user(self, user_id: str, message: dict):
        for ws in self.active.get(user_id, []):
            try:
                await ws.send_json(message)
            except Exception:
                self.active[user_id].remove(ws)

manager = ConnectionManager()

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(websocket, user_id)
    redis = Redis.from_url("redis://localhost:6379")
    pubsub = redis.pubsub()
    # Subscribe to channels relevant to this user
    await pubsub.subscribe(
        f"channel:ticks",
        f"channel:trades:{user_id}",
        f"channel:signals:{user_id}",
        f"channel:account:{user_id}",
    )
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except Exception:
        pass
```

### REST API Endpoints

```
GET  /api/health                  → System status (MT5 connected, strategy running)
GET  /api/users/{id}/trades       → Trade history with filters (date, symbol, status)
GET  /api/users/{id}/stats        → Performance stats (win rate, Sharpe, drawdown)
GET  /api/users/{id}/positions    → Current open positions
GET  /api/users/{id}/config       → User strategy config
PUT  /api/users/{id}/config       → Update user strategy config
GET  /api/trades/{id}/snapshot    → Entry or exit chart snapshot PNG
GET  /api/charts/{symbol}/{tf}    → Historical OHLCV for chart rendering
POST /api/strategy/toggle         → Start/pause strategy for a user
GET  /api/signals                 → Recent signals with analysis metadata
POST /api/backtest                → Run SMC backtest on historical data
```

### Event Types Sent Over WebSocket

```json
{
  "type": "TICK",
  "symbol": "EURUSD",
  "bid": 1.08504,
  "ask": 1.08512,
  "spread": 0.8,
  "timestamp": 1718000000000
}

{
  "type": "TRADE_OPENED",
  "trade_id": "uuid-here",
  "symbol": "EURUSD",
  "direction": "BUY",
  "entry_price": 1.08504,
  "stop_loss": 1.08200,
  "take_profit": 1.09100,
  "volume": 0.10,
  "risk_reward": 2.0,
  "timestamp": 1718000000000
}

{
  "type": "TRADE_CLOSED",
  "trade_id": "uuid-here",
  "exit_price": 1.09100,
  "pnl": 59.60,
  "exit_reason": "TP",
  "snapshot_url": "/api/trades/uuid-here/snapshot/exit"
}

{
  "type": "SIGNAL",
  "signal_type": "OB_ENTRY",
  "symbol": "EURUSD",
  "direction": "BUY",
  "htf_bias": "BULLISH",
  "ob_zone": [1.08150, 1.08400],
  "fvg_zone": [1.08250, 1.08350]
}

{
  "type": "ACCOUNT_UPDATE",
  "balance": 10000.00,
  "equity": 10059.60,
  "margin_used": 208.00,
  "free_margin": 9851.60,
  "daily_pnl": 59.60
}
```

---

## 8. Frontend — PWA Dashboard

> **Full Specification:** See `Frontend_PWA_LLM_Spec.md` — complete PWA setup, service worker config, push notification implementation, and LLM integration architecture.

### 8.1 Technology Stack

React 18 + Vite + `vite-plugin-pwa` (Workbox). TradingView Lightweight Charts v5 for all financial rendering. Zustand for state. Tailwind CSS for styling. Native browser WebSocket for real-time data.

### 8.2 Hosting & Backend Connection

Frontend is hosted on any static host (Vercel/Netlify/VPS). Backend runs **locally** on the user's desktop. The frontend continuously polls `{local_ip}:8000/api/health` every 5 seconds:

```
Backend OFFLINE → Shows all cached/historical data. Banner: "🔴 Backend Offline"
Backend comes ONLINE → Auto-reconnects WebSocket. Toast: "🟢 Backend Connected"
```

User configures their local backend IP once in Settings. All cached data (trades, analytics, snapshots) is available offline — only live prices and new signals require backend connection.

### 8.3 PWA — Install as App

The app ships a full Web App Manifest. Users can install it:
- **Android Chrome:** "Add to Home Screen" → installs as standalone app with splash screen
- **iOS Safari:** Share → "Add to Home Screen" → full-screen app on iPhone/iPad  
- **Desktop Chrome/Edge:** Install prompt in address bar → native desktop window

Once installed, it behaves identically to a native app — no browser chrome, offline capable, receives push notifications even when closed.

### 8.4 Push Notification System (VAPID Web Push)

Uses the **Web Push API + VAPID keys** — notifications are delivered via browser vendor push servers (Google FCM / Apple APNs). They appear as OS-native notifications, even with the browser closed, on all platforms including Android and Desktop.

| Event | Urgency |
|---|---|
| Trade opened | High |
| TP1/TP2/TP3 hit | High |
| SL hit | High |
| Break-even triggered | Normal |
| Daily loss circuit breaker | High |
| Consecutive loss streak | High |
| New SMC signal detected | Normal |
| Backend reconnected | Normal |
| LLM analysis ready | Normal |
| Daily performance summary | Low |

`requireInteraction: true` on SL hits and circuit breakers — notification stays on screen until dismissed.

### 8.5 Dashboard Pages

```
/ Dashboard     Live chart + SMC markup overlay + open positions + risk widget
/ Journal       All trades (live + backtest) with AI analysis per trade
/ Backtester    Run/configure/save/compare backtests
/ Analytics     Equity curve, session breakdown, TP hit rates, drawdown chart
/ Signals       All signals generated with confluence score + rejection reason
/ Settings
    ├── Strategy    Symbols, timeframes, confluence threshold
    ├── Risk        Full risk control panel (Section 7)
    ├── AI Models   LLM provider + API keys
    ├── Alerts      Push notification preferences
    └── Connection  Backend URL + MT5 account setup
```

### 8.6 Risk Control Panel in Frontend

The Settings → Risk page exposes every parameter from `RiskParams` as interactive sliders, radio buttons, and toggles. Changes save immediately to the backend and take effect on the next signal evaluation cycle. See `RiskManagement_Spec.md` Section 9 for the exact UI layout.

---

## 9. LLM Integration — AI Trade Analysis

### 9.1 Supported Providers

| Provider | Default Model | Fast Model |
|---|---|---|
| Anthropic Claude | claude-sonnet-4-6 | claude-haiku-4-5 |
| OpenAI | gpt-4o | gpt-4o-mini |
| Google Gemini | gemini-1.5-pro | gemini-1.5-flash |

User selects provider and model in Settings. API keys are AES-256 encrypted before storage in PostgreSQL — never stored in plaintext. Multiple providers can be configured simultaneously; user picks the active one per-session.

### 9.2 Analysis Contexts

**1. Single Trade Analysis (Post-Trade)**
After any trade closes (live or backtest), user clicks "Analyze with AI":
- Entry quality score and reasoning
- SMC confluence assessment
- Trade management review (BE, trailing, partial closes)
- One specific actionable takeaway
- Risk flag if any parameter violated best practices

**2. Trade Series Analysis**
Select N recent trades → submit as batch:
- Pattern detection across the series
- Session performance breakdown
- Correlation between confluence score and outcomes
- Specific parameter adjustment recommendations

**3. Backtest Summary Analysis**
After saving a backtest, optionally submit aggregate stats:
- Overall viability assessment
- Strength/weakness identification
- Parameter tuning recommendations
- Risk management feedback

**4. Custom Question**
Any analysis screen has a free-text "Ask a question" field — user types their own query about the data.

### 9.3 Analysis Flow

```
User clicks "Analyze with Claude" on a trade
         │
         ▼
Backend packages trade data into structured prompt
(entry, exit, SL, TP levels, RR, confluence score,
SMC context, BE/trail details, MAE/MFE)
         │
         ▼
LLM API call (async, non-blocking)
         │
         ▼
Response stored in DB (analysis_results table)
         │
         ▼
Push notification: "📊 AI analysis ready for EURUSD trade"
         │
         ▼
User views analysis in Trade Journal alongside chart snapshots
```

### 9.4 Auto-Analysis Option

Users can enable automatic analysis:
- `llm_auto_analyze_live`: analyze every live trade when it closes
- `llm_auto_analyze_backtest`: analyze each saved backtest automatically

Both disabled by default (API cost awareness). When enabled, fires analysis silently in background and notifies when ready.

---

## 8. Analytics Engine — Chart Snapshots & Trade Metrics

### Chart Snapshot System

This is a key differentiator. On every trade entry and exit, the bot generates a **PNG chart snapshot** showing exactly what the market looked like at that moment, with full SMC markup.

```python
import mplfinance as mpf
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd

def generate_trade_snapshot(
    symbol: str,
    timeframe: str,
    candles: pd.DataFrame,       # Last N candles of OHLCV
    order_blocks: list,          # List of active OBs
    fvgs: list,                  # List of active FVGs
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    direction: str,
    snapshot_type: str,          # "ENTRY" or "EXIT"
    trade_id: str
) -> str:
    """Generate and save a chart snapshot. Returns file path."""

    # Add SMC overlays as mplfinance additional plots
    addplots = []

    # Mark entry price line
    entry_line = pd.Series(entry_price, index=candles.index)
    addplots.append(mpf.make_addplot(entry_line, color='blue', linestyle='--', width=1.5))

    # Mark SL and TP
    sl_line = pd.Series(stop_loss, index=candles.index)
    tp_line = pd.Series(take_profit, index=candles.index)
    addplots.append(mpf.make_addplot(sl_line, color='red', linestyle=':', width=1))
    addplots.append(mpf.make_addplot(tp_line, color='green', linestyle=':', width=1))

    # Build figure
    fig, axes = mpf.plot(
        candles,
        type='candle',
        style='charles',
        addplot=addplots,
        volume=True,
        returnfig=True,
        figsize=(14, 8),
        title=f"{symbol} {timeframe} — {snapshot_type} | {direction}"
    )

    ax = axes[0]

    # Draw Order Block rectangles
    for ob in order_blocks:
        rect = mpatches.FancyBboxPatch(
            (ob['start_idx'], ob['bottom']),
            ob['end_idx'] - ob['start_idx'],
            ob['top'] - ob['bottom'],
            boxstyle="square,pad=0",
            linewidth=1,
            edgecolor='blue' if ob['type'] == 'bullish' else 'red',
            facecolor='lightblue' if ob['type'] == 'bullish' else 'lightsalmon',
            alpha=0.3
        )
        ax.add_patch(rect)

    # Draw FVG zones
    for fvg in fvgs:
        rect = mpatches.Rectangle(
            (fvg['start_idx'], fvg['low']),
            fvg['end_idx'] - fvg['start_idx'],
            fvg['high'] - fvg['low'],
            linewidth=0,
            facecolor='yellow',
            alpha=0.25
        )
        ax.add_patch(rect)

    # Save to disk
    snapshot_dir = f"./snapshots/{symbol}"
    os.makedirs(snapshot_dir, exist_ok=True)
    filepath = f"{snapshot_dir}/{trade_id}_{snapshot_type.lower()}.png"
    fig.savefig(filepath, dpi=120, bbox_inches='tight')
    plt.close(fig)

    return filepath
```

Snapshots are saved locally and their file paths are stored in the trades table. The frontend fetches them via the `/api/trades/{id}/snapshot/{entry|exit}` endpoint.

### Trade Metrics Computation

Every time a trade closes, the analytics engine computes and stores:

```python
def compute_trade_metrics(trade: Trade) -> dict:
    return {
        "pnl": trade.exit_price - trade.entry_price if trade.direction == "BUY"
               else trade.entry_price - trade.exit_price,
        "pnl_pips": calculate_pips(trade.symbol, trade.entry_price, trade.exit_price),
        "risk_reward_realized": abs(trade.exit_price - trade.entry_price) /
                                abs(trade.entry_price - trade.stop_loss),
        "duration_minutes": (trade.exit_time - trade.entry_time) / 60,
        "max_adverse_excursion": trade.max_drawdown,  # Updated during trade
    }

def compute_portfolio_stats(trades: list[Trade]) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    returns = [t.pnl for t in trades]

    return {
        "win_rate": len(wins) / len(trades) if trades else 0,
        "profit_factor": sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses)) if losses else float('inf'),
        "sharpe_ratio": calculate_sharpe(returns),       # annualized
        "max_drawdown": calculate_max_drawdown(returns), # largest peak-to-trough
        "avg_risk_reward": sum(t.risk_reward_realized for t in trades) / len(trades),
        "expectancy": (win_rate * avg_win) - (loss_rate * avg_loss),
        "total_pnl": sum(returns),
        "consecutive_losses": max_consecutive_losses(trades),
    }
```

---

## 9. Multi-User Architecture

### User Isolation Strategy

Each user is completely isolated at every layer:

**MT5 Level:**
- Each user has their own MT5 account credentials and broker connection.
- All orders placed for User 1 use `magic=1001`, User 2 uses `magic=1002`.
- The bridge only reads/manages positions matching the user's magic number.
- Users can be on different brokers simultaneously.

**Strategy Engine Level:**
- Each user gets their own `SMCEngine` instance with their own state.
- Strategy state is stored in Redis under `strategy:state:{user_id}`.
- User 1 on EURUSD does not interfere with User 2 on GBPUSD.
- Strategies can run on different symbols, timeframes, or parameter sets.

**Database Level:**
- All tables include `user_id` foreign key.
- API routes are scoped per user. User 1 cannot access User 2's trades.

**WebSocket Level:**
- Each user connects to `/ws/{user_id}`.
- Redis Pub/Sub channels are namespaced: `channel:trades:{user_id}`.
- Users only receive their own trade events.

### Adding a New User (MVP Flow)

1. Admin creates user via `POST /api/admin/users` with MT5 credentials.
2. System validates MT5 credentials by attempting login.
3. User row created in PostgreSQL; strategy defaults applied.
4. User's WebSocket channel created in Redis.
5. MT5 bridge picks up new user config from Redis on next cycle.
6. User is live within seconds.

---

## 10. Project Folder Structure

```
algoedge/
│
├── backend/
│   ├── main.py                    # FastAPI app + WebSocket server
│   ├── config.py                  # System-wide configuration
│   │
│   ├── mt5/
│   │   ├── bridge.py              # MT5 connection, tick polling, order execution
│   │   ├── data_fetcher.py        # OHLCV history + live tick fetching
│   │   └── order_manager.py       # Order placement, multi-position, modification
│   │
│   ├── strategies/
│   │   ├── base_strategy.py       # Abstract strategy interface
│   │   ├── smc/
│   │   │   ├── __init__.py
│   │   │   ├── market_structure.py  # BOS, ChoCH, HH/HL/LH/LL detection
│   │   │   ├── order_blocks.py      # OB identification + mitigation tracking
│   │   │   ├── fvg.py               # Fair Value Gap 3-candle detection
│   │   │   ├── liquidity.py         # BSL/SSL, equal highs/lows, inducement
│   │   │   ├── supply_demand.py     # S&D zones (DBR/RBD patterns)
│   │   │   ├── ipdm.py              # IPDM phase detection (Accum/Manip/Expand)
│   │   │   ├── premium_discount.py  # Fibonacci OTE + equilibrium zones
│   │   │   ├── candlestick.py       # Tier 1/2/3 pattern detection
│   │   │   ├── confluence.py        # Confluence scoring system (0–100)
│   │   │   ├── engine.py            # Main SMC orchestrator (6-step sniper)
│   │   │   ├── signals.py           # Signal generation + all validation gates
│   │   │   └── params.py            # SMCParams + RiskParams + UserConfig
│   │   └── registry.py            # Strategy plugin registry
│   │
│   ├── risk/
│   │   ├── engine.py              # RiskEngine: multi-TP, trailing, BE, circuit breakers
│   │   ├── position_sizer.py      # Lot size calc (fixed % + Kelly Criterion)
│   │   ├── trailing_manager.py    # All 4 trailing stop methods
│   │   ├── breakeven_manager.py   # Break-even system
│   │   ├── circuit_breaker.py     # Daily/weekly loss + streak guards
│   │   ├── multi_tp.py            # Multi-position TP1/TP2/TP3 orchestration
│   │   ├── compounding.py         # Stepped risk compounding engine + instrument profiles
│   │   └── news_filter.py         # Economic calendar feed integration
│   │
│   ├── analytics/
│   │   ├── metrics.py             # Trade + portfolio stats (MAE/MFE/TP rates)
│   │   ├── snapshots.py           # Entry/exit chart snapshot (mplfinance)
│   │   └── reports.py             # RiskReport generation
│   │
│   ├── backtester/
│   │   ├── engine.py              # Backtesting engine (uses same RiskEngine)
│   │   ├── runner.py              # CLI + API entry point
│   │   ├── optimizer.py           # Grid search over params
│   │   └── report.py              # Backtest report → DB / export CSV
│   │
│   ├── services/
│   │   ├── llm_service.py         # LLM analysis: Claude + OpenAI + Gemini
│   │   ├── push_service.py        # Web Push VAPID notification sender
│   │   └── api_key_store.py       # Encrypted API key storage (Fernet)
│   │
│   ├── data/
│   │   ├── redis_client.py        # Redis connection + pub/sub helpers
│   │   ├── database.py            # PostgreSQL + Alembic migrations
│   │   └── models.py              # SQLAlchemy ORM (all new tables)
│   │
│   ├── api/
│   │   ├── routes/
│   │   │   ├── trades.py          # Trade history + live positions
│   │   │   ├── backtest.py        # Run, save, load, delete backtests
│   │   │   ├── stats.py           # Performance analytics
│   │   │   ├── config.py          # User config + risk + compounding settings
│   │   │   ├── charts.py          # OHLCV + snapshot endpoints
│   │   │   ├── compounding.py     # Compounding state, step history, projection
│   │   │   ├── llm.py             # LLM analysis endpoints
│   │   │   ├── push.py            # Push subscription endpoints
│   │   │   └── admin.py           # User management
│   │   └── websocket.py           # WebSocket + Redis pub/sub bridge
│   │
│   └── utils/
│       ├── logger.py              # Structured logging (loguru)
│       ├── encryption.py          # Fernet encryption helpers
│       └── timeutils.py           # Session detection + timezone utils
│
├── frontend/
│   ├── public/
│   │   ├── manifest.json          # PWA manifest
│   │   ├── sw.js                  # Service worker (push handler)
│   │   └── icons/                 # App icons (72/192/512px)
│   ├── src/
│   │   ├── App.jsx
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx      # Live chart + positions + risk widget
│   │   │   ├── Journal.jsx        # Trade journal + AI analysis per trade
│   │   │   ├── Backtester.jsx     # Run/configure/save/compare backtests
│   │   │   ├── Analytics.jsx      # Equity curves, TP rates, session breakdown
│   │   │   ├── Signals.jsx        # Signal log with confluence scores
│   │   │   └── Settings/
│   │   │       ├── Strategy.jsx   # Symbols, timeframes, scoring threshold
│   │   │       ├── Risk.jsx       # Full risk control panel (RiskParams)
│   │   │       ├── Compounding.jsx # Compounding plan toggle + step config
│   │   │       ├── Instruments.jsx # Per-symbol overrides and enable/disable
│   │   │       ├── AIModels.jsx   # LLM provider selection + API keys
│   │   │       ├── Alerts.jsx     # Push notification preferences
│   │   │       └── Connection.jsx # Backend URL + MT5 + Deriv MT5 config
│   │   ├── components/
│   │   │   ├── LiveChart.jsx      # TradingView Lightweight Charts + SMC overlay
│   │   │   ├── PositionCard.jsx   # Open position with TP1/TP2/TP3 progress
│   │   │   ├── TradeRow.jsx       # Expandable trade table row
│   │   │   ├── BacktestRow.jsx    # Backtest history row (expandable trades)
│   │   │   ├── SnapshotViewer.jsx # Entry + exit chart snapshot side-by-side
│   │   │   ├── LLMAnalysis.jsx    # AI analysis display + request panel
│   │   │   ├── RiskWidget.jsx     # Live risk + compounding status widget
│   │   │   ├── CompoundingWidget.jsx # Step progress bar + advance/retreat controls
│   │   │   ├── CompoundingChart.jsx  # Equity curve with step boundaries overlay
│   │   │   ├── InstrumentBadge.jsx   # Symbol type indicator (synthetic/forex/gold)
│   │   │   ├── MetricCard.jsx     # KPI display card
│   │   │   ├── BackendStatus.jsx  # Connection indicator + offline banner
│   │   │   └── IOSInstallPrompt.jsx # iPhone installation guide
│   │   ├── hooks/
│   │   │   ├── useWebSocket.js    # WS connection + reconnection logic
│   │   │   ├── useBackendConnection.js  # Health polling + status
│   │   │   ├── usePushNotifications.js  # VAPID subscription management
│   │   │   └── useTrades.js       # Trade data fetching + caching
│   │   ├── store/
│   │   │   ├── tradesStore.js     # Zustand: trades state
│   │   │   ├── riskStore.js       # Zustand: risk config + live risk state
│   │   │   └── connectionStore.js # Zustand: backend + WS connection state
│   │   └── services/
│   │       ├── api.js             # REST API client (Axios)
│   │       └── notifications.js   # Push notification registration
│   ├── package.json
│   └── vite.config.js             # Vite + PWA plugin config
│
├── snapshots/                     # Generated chart PNG files
│   └── {SYMBOL}/{trade_id}_{entry|exit}.png
│
├── data/
│   └── algoedge (Railway PostgreSQL)                # PostgreSQL database
│
├── logs/
│   ├── backend.log
│   └── frontend.log
│
├── docs/
│   ├── TradingBot_MasterPlan.md   # This document
│   ├── SMC_Strategy.md            # SMC strategy specification
│   ├── RiskManagement_Spec.md     # Risk management specification
│   └── Frontend_PWA_LLM_Spec.md  # Frontend + PWA + LLM specification
│
├── requirements.txt
├── .env.example
├── start.bat                      # Windows startup script
└── README.md
```

---

## 11. MVP Build Roadmap (Phased Plan)

### Phase 0 — Foundation (Week 1–2)

- [ ] Set up project structure, Python environment, FastAPI skeleton
- [ ] Establish MT5 Python API connection: login, data fetch, place test order
- [ ] Set up Redis locally (Windows: Redis for Windows / WSL2)
- [ ] Set up PostgreSQL with all schema migrations
- [ ] Build MT5 bridge: tick polling loop → Redis publisher
- [ ] Build basic REST endpoints for health check and OHLCV data
- [ ] Build React skeleton with Vite, connect TradingView Lightweight Charts, display live ticks

**Milestone:** MT5 connected, live price ticking in the browser.

### Phase 1 — SMC Strategy Engine (Week 3–5)

- [ ] Integrate `smartmoneyconcepts` Python library
- [ ] Build multi-timeframe pipeline (H4 → M15 → M5 entry)
- [ ] Implement market structure detection (BOS / ChoCH)
- [ ] Implement order block detection and validity tracking
- [ ] Implement FVG detection
- [ ] Implement liquidity sweep detection
- [ ] Implement premium/discount zone calculation
- [ ] Build trade signal generator with all validation rules (RR, session, spread)
- [ ] Build position sizer (% risk → lot size conversion)
- [ ] Implement session filter + news filter

**Milestone:** Strategy generates signals on historical data. Signals visible in dashboard.

### Phase 2 — Risk Engine & Trade Execution (Week 6–8)

- [ ] Build `RiskEngine` class with all params from `RiskParams`
- [ ] Implement multi-position (TP1/TP2/TP3) order placement
- [ ] Implement all 4 SL methods (OB_EXTREME, SWING_POINT, FVG_EDGE, ATR_BASED)
- [ ] Implement break-even system with configurable trigger RR
- [ ] Implement all 4 trailing methods (FIXED_PIPS, ATR_TRAIL, STRUCTURE_TRAIL, PCT_TRAIL)
- [ ] Implement portfolio circuit breakers (daily loss, weekly loss, streak guard)
- [ ] Build position sizer with Kelly Criterion option
- [ ] Build chart snapshot system (mplfinance, entry + exit)
- [ ] Build analytics engine: compute all metrics including TP breakdown, MAE/MFE
- [ ] Build trade journal in dashboard with expandable rows

**Milestone:** Bot places multi-TP live trades on demo. BE and trailing active. All metrics captured.

### Phase 3 — Backtesting Engine & Validation (Week 9–11)

- [ ] Build backtesting engine using same `RiskEngine` as live trading
- [ ] Implement multi-TP, trailing, BE all working correctly in backtest
- [ ] Implement MAE/MFE tracking per trade in backtest
- [ ] Build save/discard prompt after each backtest run
- [ ] Build Backtest History page (browse all saved backtests, drill to trades)
- [ ] Build parameter grid-search optimizer (uses `RISK_OPTIMIZATION_GRID`)
- [ ] Run SMC strategy on 2+ years of data per symbol
- [ ] Generate full `RiskReport` per backtest (all metrics including TP1/2/3 hit rates)
- [ ] Tune parameters until consistent ≥65% win rate out-of-sample
- [ ] Forward-test on demo account for minimum 3 weeks before live

**Milestone:** Strategy proven at ≥65% win rate. Risk engine validated. Save/load working.

### Phase 4 — PWA + Notifications + LLM (Week 12–13)

- [ ] Convert frontend to PWA (vite-plugin-pwa, manifest.json, icons)
- [ ] Implement Web Push / VAPID notification system (backend + frontend)
- [ ] Implement service worker with caching strategy (offline mode)
- [ ] Build backend connection auto-detect + offline banner
- [ ] Integrate LLM service (Claude + OpenAI + Gemini providers)
- [ ] Build API key storage (encrypted) + settings panel
- [ ] Build single-trade AI analysis UI in journal
- [ ] Build backtest summary AI analysis flow
- [ ] Add push notification for all event types (trade, risk, LLM ready)

**Milestone:** App installable on phone. Notifications working. AI analysis live.

### Phase 5 — Multi-User + Production Hardening (Week 14–15)

- [ ] Second user account onboarded, per-user config isolation confirmed
- [ ] Build admin panel (user management, strategy toggle, system health)
- [ ] Add structured logging (all events: signals, trades, errors, risk events)
- [ ] Add auto-recovery: if MT5 disconnects, bridge auto-reconnects in <30s
- [ ] Windows startup batch script (`start.bat`) — starts all services in order
- [ ] Full end-to-end test: 2 users, different risk configs, concurrent trades

**Milestone:** Full MVP in production use by 2 users with PWA + LLM + full risk engine.**

---

## 12. Extension Roadmap — AI/ML/RL/LLM Layer

This section defines the full product vision beyond the MVP. Each extension is a self-contained module that plugs into the strategy registry.

### Extension 1 — Unsupervised Pattern Discovery (Weeks 12–16)

**Goal:** Let the machine find patterns in historical market data without being told what to look for.

**Approach:**
- **Clustering (K-Means / DBSCAN):** Encode each candle or 20-candle window as a feature vector (OHLCV + indicators). Cluster similar price patterns. Label clusters by what price did after them. Clusters with strong predictive forward returns become candidate patterns.
- **Autoencoders (Unsupervised Deep Learning):** Train an autoencoder on OHLCV sequences. Low reconstruction error = normal pattern; high reconstruction error = anomalous/unusual pattern (often precedes large moves).
- **Hidden Markov Models (HMM):** Model the market as a sequence of hidden states (trending up, trending down, ranging, transitioning). HMM infers which state we're in and what the likely next state is.
- **DTW-based Pattern Matching:** Dynamic Time Warping to find historical price shapes that match the current market structure. If the current shape has a 73% historical accuracy of X pip move, that's an edge.

**Integration:** These patterns feed into the strategy engine as additional filters — they don't replace SMC but confirm or deny setups.

### Extension 2 — Reinforcement Learning Agent (Weeks 16–24)

**Goal:** Train an agent that learns optimal trade timing through interaction with a simulated market environment.

**Stack:** Python + Stable-Baselines3 + OpenAI Gymnasium + custom `TradingEnv`

**Architecture:**
```
Custom TradingEnvironment (Gymnasium)
├── Observation Space: [OHLCV (100 candles), SMC signals, account state, position state]
├── Action Space: [HOLD, BUY, SELL, CLOSE_LONG, CLOSE_SHORT, ADJUST_TP, ADJUST_SL]
└── Reward Function: Sharpe-ratio-adjusted P&L (penalizes high variance, rewards consistency)

Algorithms to test (in order):
1. PPO (Proximal Policy Optimization) — stable, works well on financial envs
2. SAC (Soft Actor-Critic) — better for continuous action spaces
3. A2C — faster to train than PPO, good for initial experiments
4. TD3 — for more precise position sizing actions

Training Pipeline:
- Train on 5+ years of historical data (MT5 OHLCV)
- Validate on out-of-sample 1-year period
- Walk-forward validation: retrain every month on rolling 12-month window
- Paper trade for 4 weeks before live deployment
```

**The RL agent does not replace the SMC strategy in MVP.** Initially, the RL agent acts as a **filter/gate** — it votes on whether to take a trade that SMC has already identified. Over time, it can evolve into a full autonomous agent.

### Extension 3 — LLM Integration Layer (Weeks 20+)

**Goal:** Use LLMs for three specific high-value tasks.

**Task A — Trade Explanation Engine:**
After every trade closes, the LLM receives the full trade context and generates a human-readable explanation:
```
"This BUY trade on EURUSD was entered at 1.08504 on M15 after a bullish order block was
mitigated following a liquidity sweep of equal lows at 1.08200. The H4 structure was
bullish (BOS at 1.0900). The trade hit TP at 1.09100, delivering 2.4R. The setup
was high-quality: strong OB with 2.1x average volume, FVG confirmation, and execution
during London kill zone. One concern: the spread was 1.8 pips at entry vs the 1.2 pip
average — slightly elevated, suggesting minor liquidity concern at entry."
```

**Task B — Strategy Insights & Recommendations:**
Weekly, the LLM analyzes the last N trades and provides strategic feedback:
- "Your win rate on XAUUSD is 42% vs 71% on EURUSD. Consider reducing XAUUSD exposure."
- "You have 8 consecutive losses on Friday NY session. Review if this session is profitable."
- "Your average hold time for losing trades is 4.2 hours vs 1.8 hours for winners. Consider tighter management."

**Task C — Market Narrative (optional):**
The LLM reads summarized market structure data and generates a daily market bias report — useful for reviewing what the bot sees vs what a human trader would say.

**LLM Options (ordered by cost):**
1. **Local Ollama** (Llama 3.1 8B / Mistral 7B): Zero cost, runs on GPU. Best for privacy.
2. **Anthropic Claude API** (Haiku / Sonnet): Low cost per call, high quality.
3. **OpenAI GPT-4o-mini**: Very low cost for explanation tasks.

### Extension 4 — Monte Carlo Risk Simulation

Run Monte Carlo simulations on strategy performance:
- Simulate 1,000+ random orderings of historical trades
- Generate distribution of possible equity curves
- Determine probability of ruin at different risk levels
- Determine optimal position sizing (Kelly Criterion or fractional Kelly)
- Show risk-of-ruin curve at different max drawdown thresholds

This directly informs risk parameter settings and gives statistical confidence in strategy edge.

### Extension 5 — Multi-Strategy Framework

Each strategy is a plugin implementing `BaseStrategy`:

```python
class BaseStrategy:
    def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
               user_config: UserConfig) -> Optional[TradeSignal]:
        """Called on every new closed bar. Return signal or None."""
        raise NotImplementedError

    def on_tick(self, symbol: str, tick: Tick) -> Optional[TradeAction]:
        """Called on every tick for position management."""
        raise NotImplementedError

    def get_required_timeframes(self) -> list[str]:
        """Return list of timeframes this strategy needs."""
        raise NotImplementedError
```

Users can select which strategy to run from the dashboard. Strategies available:
- `SMC_v1` (MVP)
- `SMC_RL_v1` (SMC + RL filter — Extension 2)
- `MOMENTUM_v1` (EMA crossover + RSI — simple benchmark)
- Custom user-defined strategies (future)

---

## 13. Competitive Landscape & What to Learn From Them

### Freqtrade (48k GitHub stars — Open Source)

**What it does well:**
- Excellent multi-strategy framework with a clean plugin interface
- FreqAI ML module: trains models on features, integrates predictions as strategy signals
- Backtesting engine with Hyperopt parameter optimization
- Telegram integration for alerts + trade control from phone

**What to borrow:** Their strategy plugin interface design (`IStrategy` class) and their Hyperopt parameter optimization approach for SMC parameters.

**Gap vs our system:** Does not integrate with MT5 (crypto/CEX focused). No SMC support. No chart snapshot system. No RL support.

### Jesse (7.6k GitHub stars — Open Source)

**What it does well:**
- Zero look-ahead bias backtesting (most honest backtester in open source)
- Clean, minimal strategy API
- Monte Carlo testing built-in
- Full performance report with all key metrics

**What to borrow:** Their backtesting discipline (zero look-ahead bias) and report format.

**Gap vs our system:** No MT5. No SMC. No real-time dashboard. No multi-user.

### OctoBot (5.5k GitHub stars — Open Source + Cloud)

**What it does well:**
- Modular "tentacles" plugin architecture — clean extensibility
- Web UI without coding
- Community strategies

**What to borrow:** Tentacles architecture for plugin extensibility (our strategy registry)

### MQL5 SMC Bot (carlosrod723 on GitHub)

This is the closest existing project to our MVP. It combines MQL5 + Python + LSTM for SMC trading.

**What it does well:**
- Multi-strategy SMC (NFT, FT, SFT, CT)
- LSTM trade filter (reduces false signals 40–60% per author)
- Fractal-based liquidity sweeps + Fibonacci zones
- Trailing stops + partial exits

**Our advantage:** We build a full platform (dashboard, analytics, snapshots, multi-user, extensibility) rather than just an EA. Their LSTM implementation is a clear validation that ML filtering on SMC is effective — we adopt this in Extension 1.

---

## 7. Risk Management System

> **Full Specification:** See `RiskManagement_Spec.md` — the authoritative reference for all risk engine logic, position sizing, trailing methods, circuit breakers, and UI layout.

Risk management is the **primary layer** of AlgoEdge. Every parameter is **user-controlled** from the frontend Settings panel, and every setting applies identically in both live trading and backtesting.

### 7.1 Multi-Position (TP1/TP2/TP3) Architecture

Every trade spawns up to **3 simultaneous MT5 orders** at the same entry, same SL, different TP levels:

| Sub-Position | Default RR | Default Size | Trail Method After Previous TP Hit |
|---|---|---|---|
| TP1 | 1:3 (minimum) | 40% of lot | None — closes at TP |
| TP2 | 1:5 (standard) | 35% of lot | ATR Trail |
| TP3 | 1:7 or liquidity target | 25% of lot | Structure Trail (SMC swings) |

RR tiers available: **1:3 (min) → 1:5 → 1:7 → 1:10 (max)**. User sets all three levels. TP3 can optionally anchor to next SMC external liquidity pool instead of fixed RR.

### 7.2 Stop Loss Methods (User Selects)

| Method | Description | Best For |
|---|---|---|
| `OB_EXTREME` | Beyond Order Block high/low + buffer | Default, SMC-native |
| `SWING_POINT` | Beyond manipulation candle wick | Wider, more breathing room |
| `FVG_EDGE` | Beyond FVG zone boundary | Tight, high-confluence setups |
| `ATR_BASED` | Entry ± (ATR × multiplier) | Volatility-adjusted sizing |

SL buffer (default 5 pips) always added beyond the chosen method.

### 7.3 Break-Even System

```
Trigger:  When trade reaches be_trigger_rr (default 1.0R) in profit
Action:   Move SL to entry + be_buffer_pips (default +2 pips)
Auto-BE:  Fires automatically on TP1 hit (be_on_tp1_hit = true)
Safeguard: SL only moves in profitable direction — never backward
```

### 7.4 Four Trailing Stop Methods

| Method | Logic | Best For |
|---|---|---|
| `FIXED_PIPS` | Trail by N pips behind current price | Simple, consistent |
| `ATR_TRAIL` | Trail by ATR × multiplier (ratchet — never moves back) | Volatile markets (Gold, BTC) |
| `STRUCTURE_TRAIL` | Trail to each confirmed M5 swing point | SMC-native, max runners |
| `PCT_TRAIL` | Trail by % of current price | High-priced instruments |

**TP2 default → ATR_TRAIL. TP3 default → STRUCTURE_TRAIL.**

### 7.5 Portfolio Circuit Breakers

| Guard | Default | Action When Hit |
|---|---|---|
| Daily loss limit | 5% of account | Pause strategy + close all positions |
| Weekly loss limit | 10% of account | Pause for rest of week |
| Consecutive losses | 5 in a row | Pause + require manual re-enable |
| Max open positions | 3 | Block new entries until one closes |
| Correlated pairs | 4% combined risk | Warn/block correlated new entry |

All circuit breakers fire push notifications instantly.

### 7.6 Risk Presets (One-Click Starting Points)

Four built-in presets in `params.py`: `conservative`, `balanced`, `aggressive`, `runner`. Each pre-configures all TP/SL/trail/circuit-breaker values. User can select a preset then customise individual parameters.

### 7.7 What You Backtest = What Runs Live

The backtester uses the **exact same `RiskEngine` class** as live trading. No separate logic paths. This means:
- Backtest with multi-TP → same splits and RR levels run live
- Backtest with ATR trailing → same ATR period and multiplier used live
- Backtest with BE trigger → same R multiple triggers BE live
- Parameter optimization output → directly importable as live config

---

## 8. Compounding Plan System

> **Full Specification:** See `CompoundingPlan_Spec.md`.

The compounding plan is an **optional, toggleable** stepped-risk growth system. When enabled it replaces percentage-based sizing with fixed-dollar stepped risk — the anti-martingale approach: press when winning, reduce when losing.

### 8.1 Default Plan — 1:3 RR, 18 Steps

Starting from $20, scales to $11,120 through 9 risk tiers (2 trades each):

```
$20 risk  → Steps 1–2  (account  $20 → $140)
$30 risk  → Steps 3–4  (account $140 → $320)
$50 risk  → Steps 5–6  (account $320 → $620)
$100 risk → Steps 7–8  (account $620 → $1,220)
$200 risk → Steps 9–10 (account $1,220 → $2,420)
$250 risk → Steps 11–12 (account $2,420 → $3,920)
$300 risk → Steps 13–14 (account $3,920 → $5,720)
$400 risk → Steps 15–16 (account $5,720 → $8,120)
$500 risk → Steps 17–18 (account $8,120 → $11,120)
```

Note: Source image shows Step 4 = $230 (typo — corrected to $320 in implementation).

### 8.2 Advance Modes (User Selects)

- **AUTO**: Advances when balance crosses next threshold (default)
- **CONSERVATIVE**: Requires N consecutive wins at level first (recommended for beginners)
- **MANUAL**: User presses advance/retreat button in dashboard

### 8.3 Integration with Multi-TP

Compounding risk dollar amount is split across TP1/TP2/TP3 proportionally. Step 7 risk=$100 with 40/35/25% split → TP1 risks $40, TP2 $35, TP3 $25. Total always equals exactly the step's risk amount.

### 8.4 Custom Tables

Users define their own step tables via JSON in Settings. Validated for mathematical consistency (each step threshold must equal prior step's account_after_win).

---

## 9. Instrument Coverage & Profiles

> **Full Profiles:** See `compounding.py` → `INSTRUMENT_PROFILES` dictionary.

### 9.1 Primary Target Instruments

| Priority | Symbol | Type | Session Filter | News Filter | Compounding Start |
|---|---|---|---|---|---|
| **Primary** | V75 (Volatility 75 Index) | Deriv Synthetic | OFF — 24/7 | OFF | $20 min account |
| **Primary** | XAUUSD (Gold) | Commodity | ON — London/NY | ON | $100+ recommended |
| **Secondary** | V25, V50 (Deriv Synthetics) | Synthetic | OFF | OFF | $20 min account |
| **Secondary** | EURUSD, GBPUSD | Forex | ON | ON | $100+ recommended |
| **Extended** | US30, BTCUSD | Index/Crypto | Partial | ON | $200+ |

### 9.2 Synthetic Indices (Deriv MT5)

Synthetic indices require a **Deriv MT5 Synthetic account** (separate from standard MT5 accounts). Key properties:
- **24/7 trading** — no weekend gaps, no market hours — always open
- **No news events** — economic data does not affect algorithmic price generation
- **Clean SMC structure** — V75 especially exhibits clear OBs, FVGs, and liquidity pools
- **Low barrier** — $20 account can start the full compounding plan
- **Leverage** — up to 1:1000 on selected indices (use conservatively)

**SMC parameter differences for synthetics:**
```python
# Tighter settings vs standard forex
swing_length_htf   = 3     (vs 5 for forex)
swing_length_ltf   = 2     (vs 3 for forex)
ob_impulse_ratio   = 1.5   (vs 2.0 for forex)
session_filter     = False  (vs True for forex)
news_filter        = False  (vs True for forex)
```

The `InstrumentProfile` system in `compounding.py` handles all these overrides automatically. When the strategy engine receives a signal for a synthetic symbol, it applies the profile's parameter overrides before evaluation.

### 9.3 Gold (XAUUSD) Special Configuration

Gold requires wider stops and larger pip tolerances due to its volatility:
```python
liq_sweep_min_pips  = 50    # Gold sweeps are 50+ pips
fvg_min_gap_pips    = 30    # Larger minimum FVG
sl_buffer_pips      = 10    # Wider buffer
atr_trail_mult      = 2.0   # Wider ATR trail
max_spread_pips     = 5.0   # Allow up to 5 pip spread
```

Gold is most productive during London open and NY open kill zones, and is strongly affected by USD-related news (CPI, NFP, FOMC).

### 9.4 Adding New Instruments

Add a new `InstrumentProfile` to `INSTRUMENT_PROFILES` in `compounding.py`. The system will automatically: apply the profile's parameter overrides to the SMC engine, use the correct point/pip calculation for lot sizing, apply or skip session/news filters as specified.

New instruments can be enabled per-user from Settings → Strategy → Symbols.

---

## 15. Zero-Cost Infrastructure Plan

| Component | Solution | Cost |
|-----------|----------|------|
| MT5 Terminal | MetaTrader 5 (free from broker) | $0 |
| Python Runtime | Python 3.11+ | $0 |
| Redis | Redis for Windows / WSL2 | $0 |
| Database | PostgreSQL on Railway | $0 |
| Backend | FastAPI + Uvicorn (localhost) | $0 |
| Frontend | React + Vite (served from localhost) | $0 |
| Chart Library | TradingView Lightweight Charts (open source) | $0 |
| SMC Library | smartmoneyconcepts (open source) | $0 |
| RL Framework | Stable-Baselines3 + Gymnasium (open source) | $0 |
| LLM (local) | Ollama + Llama 3.1 8B (local GPU) | $0 |
| News Filter | ForexFactory RSS feed (free) | $0 |
| **Total** | | **$0/month** |

### Hardware Requirements

- **Minimum:** Windows 10/11, 8GB RAM, 4-core CPU, 50GB free disk
- **Recommended:** Windows 11, 16GB RAM, 8-core CPU, dedicated GPU (GTX 1060+) for local LLM
- **Why Windows:** MT5 Python API **only runs on Windows** (official support). Linux requires Wine/Docker workarounds.

### Startup Automation (start.bat)

```batch
@echo off
echo Starting AlgoEdge Trading Bot...

:: Start MT5 (adjust path)
start "" "C:\Program Files\MetaTrader 5\terminal64.exe"
timeout /t 5

:: Start Redis
start "" redis-server

:: Start Python backend
cd backend
start "" uvicorn main:app --host 0.0.0.0 --port 8000 --reload
timeout /t 3

:: Start Frontend
cd ..\frontend
start "" npm run dev

echo All services started. Dashboard: http://localhost:5173
```

---

## Final Recommendation Summary

**Start lean, build with extension in mind.** The MVP is deliberately minimal — one strategy (SMC), two users, one database, one backend process. But every architectural decision is made so that adding a second strategy, a fifth user, an RL layer, or an LLM module is a matter of plugging in a new module, not rewriting the system.

The **highest-leverage first extensions** after MVP are:
1. **Backtesting rigor** — validate SMC at 65%+ before any live capital
2. **RL filter** (PPO) — even a basic RL filter on SMC signals historically reduces false entries by 40%
3. **Monte Carlo risk simulation** — before scaling capital, know your probability of ruin
4. **LLM trade explainer** — the compounding value of understanding every trade is enormous for strategy evolution

The **SMC + RL combination is the most promising path** to consistent 68–75% profitability based on existing research (the MQL5 SMC bot reports 40–60% false signal reduction from ML filtering, and FinRL-DeepSeek work shows LLM+RL hybrid agents beating pure RL baselines on out-of-sample data).

Build the MT5 bridge first. Everything else flows from having reliable, fast, clean market data.

---

*Document Version 1.0 | AlgoEdge Trading Bot Master Plan | June 2026*
