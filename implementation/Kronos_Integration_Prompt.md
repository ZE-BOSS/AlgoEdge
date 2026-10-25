# Coding Agent Prompt — Integrate Kronos Forecasting into AlgoEdge

## Role & context

You are a senior Python/ML engineer working on **AlgoEdge**, a Python 3.12 / FastAPI multi-strategy algorithmic trading system. Key existing parts:

- **Execution:** MT5 through a Master-Worker process design, because the MT5 Python package allows only one connection per process. It trades multiple brokers and prop firms (Exness, Deriv, FundedNext, Bloom Funded, etc.).
- **Event bus:** Redis pub/sub. On the Windows VPS this is Memurai.
- **Risk engine:** a deterministic risk parameter engine sets the TP grid (TP1 = 1R, TP2 = 3R, TP3 = 5R) and SL adjustments. Strategy modules set the SL level only. `prop_firm_validator.py` enforces prop-firm rules.
- **Backtesting:** `backend/backtester/engine.py` and `portfolio_engine.py`, with a React `Backtester.jsx` frontend.
- **ML/RL:** the system now includes ML and RL agents alongside its rule-based strategies.
- **Process management:** pm2.

**Goal:** integrate **Kronos** (https://github.com/shiyu-coder/Kronos, MIT license, AAAI 2026), an open-source foundation model for OHLCV candlesticks. It should become a **forecast feature service** whose outputs feed:

1. a gate/filter on existing strategy signals, and
2. observation features for the RL agents.

Kronos must **never** place orders, set SL/TP, or bypass the risk engine or `prop_firm_validator`.

## Hard infrastructure constraint: AWS EC2 t3.large (Windows)

- The instance has **2 vCPUs and 8 GB RAM**, and it is **burstable**. Baseline CPU is about 30% per vCPU; above that it spends CPU credits, and when they run out it gets throttled.
- Windows Server, MT5 terminals (one per broker), Redis/Memurai, FastAPI, the workers, and the RL agents already compete for these resources.
- **MT5 execution latency is the top priority.** Kronos must never starve execution workers of CPU.
- There is no GPU, so **all fine-tuning happens off-box**: Google Colab, a rented cloud GPU, or a local GPU machine. **Only CPU inference runs on the VPS.**

### Design rules that follow from this

- **Models:**
  - Use **Kronos-mini** (4.1M params, with `NeoQuasar/Kronos-Tokenizer-2k`, context 2048) or **Kronos-small** (24.7M params, with `NeoQuasar/Kronos-Tokenizer-base`, context 512).
  - Do **not** run Kronos-base on this box. Make the model a config value.
- **PyTorch:** install the **CPU-only** wheel to save disk and RAM.
  - Call `torch.set_num_threads(1)` and `torch.set_num_interop_threads(1)`.
  - Run inference under `torch.inference_mode()`.
- **Process:** run Kronos as its **own pm2 process** at Windows **BelowNormal** priority, separate from the MT5 master and workers.
  - Cap its memory with pm2 `max_memory_restart`.
  - Load the model once at startup.
  - Download the HF weights once to a local `models/kronos/` folder and load offline.
- **Timing:** inference is **bar-close driven only**, never per tick.
  - Default timeframes are M5 and M15. M1 is opt-in per symbol, and only if benchmarks show it fits the budget.
  - Use `predict_batch` across symbols that share the same timeframe, lookback, and horizon.
- **Sampling:** keep `sample_count` small and configurable (default 5). Cost scales roughly linearly with samples × pred_len × symbols.
- **Benchmark before enabling.** Build a `kronos_benchmark.py` that measures latency (p50/p95) and peak RSS per symbol batch on this VPS. Use it to derive a per-bar **compute budget**. Default: inference for all symbols must finish within 25% of the bar period.
  - If the budget is exceeded, drop symbols by priority list, reduce samples, or skip the cycle.
  - Never queue up a backlog of stale work.
- **Watchdogs:** add a CPU-credit watchdog.
  - Log `CPUCreditBalance` via CloudWatch, or approximate it with process CPU time.
  - Pause Kronos inference automatically when the balance falls below a configurable threshold, and resume when it recovers.
  - Document that T3 Unlimited mode or moving to a non-burstable instance (c/m family) is the scaling path.

## Architecture to implement

### 1. `kronos_service/` (new package)

- **`data_buffer.py`:**
  - Keeps a rolling OHLCV buffer per (symbol, timeframe), fed from the existing MT5 worker bar-close events on the Redis bus.
  - Do **not** open new MT5 connections from this service.
  - MT5 tick volume maps to `volume`. The `amount` column is optional, so leave it out.
- **`predictor.py`:**
  - Wraps `KronosTokenizer`, `Kronos` and `KronosPredictor`.
  - Vendor the repo's `model/` folder at a **pinned commit** and keep its LICENSE.
  - Lookback defaults to ~400 bars for small or ~1000 for mini, and must be at most the model's context. Horizon `pred_len` is configurable (default 12 bars).
- **`features.py`:** turns the sampled forecast paths into a compact feature vector:
  - `exp_return_h` — mean forecast close at the horizon vs the last close, in % and in ATR units.
  - `p_up` — fraction of sample paths that end above the last close.
  - `forecast_range_atr` — forecast high-low range over the horizon ÷ current ATR, used as a volatility signal.
  - `dispersion` — standard deviation across sample paths, used as an uncertainty signal.
  - `model_id`, `asof_bar_time`, `computed_at`, `latency_ms`.
- **`publisher.py`:**
  - Publishes features to a Redis key `kronos:{symbol}:{tf}` with a TTL of one bar, and emits a `kronos.features` event on the bus.
- **`service.py`:**
  - The main loop: handles bar-close events, the budget scheduler, the CPU-credit watchdog, health and heartbeat, and structured logging.
  - Exposes a FastAPI health/status route through the existing API, or a small separate one.

### 2. Consumers

- **Strategy gate (`kronos_gate.py`):**
  - An optional, per-strategy, config-driven filter applied *after* a strategy emits a signal and *before* the risk engine.
  - Example rules: reject a BUY if `p_up < threshold`; reject if `dispersion` is above a threshold.
  - Thresholds live in per-strategy config.
  - **Staleness:** if features are missing or stale (`asof_bar_time` is not the latest closed bar), apply the configured `fail_mode`: `open` (allow) or `closed` (block). Default is `open`.
- **RL agents:**
  - Add the Kronos features to the observation space behind a feature flag.
  - Missing values are masked by a `kronos_available` flag plus zero-fill, never with forward-filled stale values.
  - RL actions still pass through the deterministic risk engine and `prop_firm_validator`, which have final authority.
- **Instrument scope:**
  - Enabled for forex and metals by default.
  - **Deriv Crash/Boom synthetics are disabled by default.** Their price generation is algorithmic and spike arrivals are memoryless (Poisson), so a candle-pattern model has no exploitable structure there. Keep it enable-able only for research comparisons.

### 3. Backtest / live parity (critical)

- **`kronos_precompute.py`** runs offline (on a GPU box or overnight on CPU).
  - It performs a **walk-forward** pass over historical MT5 data and writes features to Parquet, keyed by (symbol, tf, asof_bar_time).
  - The forecast at bar *t* may only use bars ≤ *t*. Add an explicit test that proves there is no lookahead.
- The backtester reads precomputed features through the **same `features.py` schema and the same gate code** used live.
  - Live and backtest must not have separate logic paths.
- The precompute run and live inference must use the same model checkpoint, sampling params, and seed handling.
  - Record `model_id` in every trade log so results can be traced to a model version.

### 4. Evaluation harness (must pass before enabling live)

`kronos_eval.py` reports results per symbol and timeframe on out-of-sample walk-forward data:

- Directional hit-rate at the horizon **net of spread**, using real broker spreads.
- Comparison against naive baselines: random walk / last close, and a simple momentum sign.
- Calibration of `p_up` with a reliability table, and correlation of `forecast_range_atr` with realized range.
- **Gated vs ungated** results for each existing strategy: win rate, expectancy in R, max drawdown, trade count, and prop-rule violations.
- Promotion rule: a symbol/strategy pair is eligible for live use only if gated expectancy beats ungated on out-of-sample data with a reasonable trade count. Make that threshold configurable.

### 5. Fine-tuning (off-box, phase 2)

- Inspect the repo's `finetune_csv/` folder. Its pipeline takes CSV data, unlike the Qlib/A-share example.
- Build `scripts/export_mt5_history.py` to export per-symbol OHLCV CSVs in that format.
- Write a Colab/GPU runbook to fine-tune the tokenizer and predictor (`torchrun`; single GPU is fine), then copy checkpoints back to `models/kronos/<model_id>/`.
- A fine-tuned model must re-pass the evaluation harness before it replaces the zero-shot model.

## Deliverables and process

1. **First, before writing any code:** read the existing codebase, especially:
   - the Redis bus and event names,
   - the Master-Worker MT5 layout,
   - `backend/backtester/engine.py`, `portfolio_engine.py`, the risk engine, and `prop_firm_validator.py`,
   - the RL agent observation code,
   - pm2 ecosystem config.

   Then produce `Implementation_Plan_Kronos.md` covering the file list, interfaces, config schema, event names, phased rollout, and open questions. **Stop for my approval.**
2. Phase 1: `kronos_service` + benchmark script + precompute + eval harness. No live gating yet.
3. Phase 2: strategy gate and RL feature integration behind feature flags (default off).
4. Phase 3: fine-tuning export and runbook.
5. Include unit tests (feature math, staleness and fail-mode, no-lookahead), pinned `requirements-kronos.txt`, a pm2 config entry, and a short README.

## Non-negotiables

- Kronos is advisory only. It never sizes positions, never sets SL/TP, and never overrides risk or prop-firm validation.
- If Kronos crashes or is slow, trading continues normally according to `fail_mode`.
- Make no changes to existing strategy entry logic beyond the optional gate hook.
- Log every gate decision (symbol, strategy, features, decision, reason) for later analysis.
