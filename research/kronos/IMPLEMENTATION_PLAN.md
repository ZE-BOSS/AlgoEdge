# Kronos in AlgoEdge: implementation plan

*19 Oct 2026. Companion to [FINDINGS-2026-10-19.md](FINDINGS-2026-10-19.md), which has the research and backtest results. Prompt: `Implementation/Kronos_Integration_Prompt.md`.*

## 0. The short version

- **Phase 1 is built and tested:** the offline parts. Kronos runs on CPU, its forecasts are precomputed walk-forward with no lookahead, and an evaluation harness measures forecast skill and the value of a gate through the app's own backtest engine.
- **Phase 2 (live gate and RL features)** is designed below, but not started. It waits for your approval. FINDINGS shows the gate has no value on ORB. The one candidate is **SPX500 H1 traded on Kronos's confident calls**, so the first live step should be a **shadow runner for SPX500 H1 only** (Phase 2a), not a gate on every slot.
- **Phase 3 (fine-tuning)** is a runbook below.

Three things in the prompt don't match the codebase, and the plan follows the codebase:

| The prompt assumes | What the code actually has | What the plan does |
|---|---|---|
| A Master-Worker MT5 design publishing **bar-close events on Redis** | One `algoedge-backend` process. `bot_service._scan_loop` fetches bars and drives each slot with `LiveBarFeed`. Redis carries **only ticks** (`ticks:{sym}` from `mt5_broker.py`). | Add one publisher in `bot_service`: after `LiveBarFeed.advance` it publishes the newly closed bars. Kronos subscribes and opens **no** MT5 connection. |
| A **TP grid of 1R/3R/5R** set by the risk engine | Per-slot TP settings (`tp_count`, `tp1_rr`, …) and per-strategy exit defaults | No effect on Kronos, which never touches exits. Noted only for accuracy. |
| `prop_firm_validator.py` | Exists: `backend/risk/prop_firm_validator.py` (`validate_trade`, `should_block_trading`) | Unchanged. The gate runs *before* it, so the validator still has the final word. |

## 1. What is built (Phase 1)

All of it is in `backend/kronos_service/`. Nothing under `backend/` imports it, so the trading stack runs without torch.

| File | What it does |
|---|---|
| `vendor/` | Upstream `model/kronos.py` and `module.py` at commit **67b630e** (13 Apr 2026), with the MIT `LICENSE`. One line is changed: the import is made relative. |
| `config.py` | `KronosConfig`, frozen: model (`Kronos-mini` or `Kronos-small`; `base` is refused), lookback, pred_len, sample_count, temperature, top_p, seed, threads. `model_id` hashes the checkpoint revision plus every setting that changes the output, and it is stamped on every feature row. |
| `predictor.py` | `KronosEngine`. It loads the weights once from `models/kronos/` (offline; files downloaded at pinned HF revisions) and returns **every sampled path** (upstream averages them away, which makes `p_up` and `dispersion` impossible). Each (symbol, bar, path) gets its own seeded RNG, so a forecast doesn't depend on its batch, and precompute and live agree. It also has a **KV cache**: 4–9× faster, and the output is identical (see §3). |
| `features.py` | Paths become `exp_return_pct`, `exp_return_atr`, `p_up`, `forecast_range_atr` and `dispersion_atr`. `vector()` is the RL form: zero-fill plus a `kronos_available` flag, and never a stale value. |
| `gate.py` | `GateConfig` and `decide()`. A BUY needs `p_up ≥ min_agreement` (a SELL needs `1 − p_up`), with optional dispersion and expected-move limits. `fail_mode` is `open` (the default) or `closed` for missing or stale rows. `to_log()` gives the per-decision record. The backtest uses this function, and live will use the same one. |
| `precompute.py` | The walk-forward window ends at the **decision bar**: the same bar `base_signals` stamps a setup on, so they join exactly. ATR comes from the window, and future bar times are calendar arithmetic. A cache in `data/kronos/` is keyed by `model_id`. |
| `evaluate.py` | **Forecast skill:** hit rate and net basis points after the measured spread, against momentum and always-long, plus rank IC, `p_up` calibration and range correlation. **Gate value:** a strategy's setups through `BacktestEngine` three ways: all, gate-allowed and gate-*rejected*. The rejected set is the control. |
| `research.py` | The CLI that produced FINDINGS. |
| `benchmark.py` | p50/p95 latency per model, samples and batch, and how many symbols fit in 25% of an M5 or M15 bar. **Run it on the VPS before enabling anything.** |
| `requirements-kronos.txt` | torch 2.5.1 (CPU), einops, huggingface_hub, safetensors and torch's own deps, pinned. They install into `.kronos_deps/`, **not** `venv_win`. |
| `tests/test_kronos_service.py` | 15 tests: feature maths; gate staleness and both fail modes; **no lookahead** (rewrite every bar after *t*, and the features at *t* don't move); the store round trip; `model_id` sensitivity; real-model reproducibility and batch independence; and KV-cache output identical to upstream's loop. |

Also verified once, outside the suite: with sampling turned off, the inference matches upstream `KronosPredictor.predict` to within 2×10⁻⁵ on both models.

## 2. Phase 2: live (designed, NOT built, needs approval)

```
bot_service._scan_loop ── LiveBarFeed.advance ──► publish "bars.closed:{sym}:{tf}"  (new, ~10 lines)
                                                         │  (Redis pub/sub, last `lookback` bars)
                                                         ▼
        pm2 "algoedge-kronos"  (BelowNormal, max_memory_restart 1500M, threads=1)
        backend/kronos_service/service.py
          ├─ budget scheduler: per bar, priority-ordered symbols until 25% of the bar
          │    is used; the rest SKIP this bar (no backlog, ever)
          ├─ CPU-credit watchdog: CloudWatch CPUCreditBalance if credentials exist,
          │    else process CPU-time vs t3.large baseline; pause below threshold
          ├─ KronosEngine.paths → features.compute (the same code as precompute)
          └─ SET  kronos:{sym}:{tf}  (TTL = one bar)  +  PUBLISH kronos.features
                                                         │
bot_service, after a slot's signal and BEFORE the risk engine:
    row = GET kronos:{sym}:{tf};  d = gate.decide(slot.kronos_gate, signal.direction, row, decision_bar)
    log d.to_log(...) to logs/kronos_gate.jsonl;  drop the signal if not d.allow
```

**Files:**
- `backend/kronos_service/service.py` and `publisher.py` (new);
- `bot_service.py`: the publish and gate hooks;
- `config_schema.py`: an `InstrumentSlot.kronos_gate` dict;
- `ecosystem.config.js`: an `algoedge-kronos` entry, disabled by default.

**Config (per slot, default off):**
```json
"kronos_gate": {"enabled": false, "min_agreement": 0.6,
                "max_dispersion_atr": null, "min_exp_return_atr": null, "fail_mode": "open"}
```

**Instrument scope:** forex, metals, indices and stock CFDs. Deriv Crash/Boom/Volatility are off by default; they can be enabled for research only.

**RL features:** five columns plus `kronos_available`, added to `rl/features.py` behind `AgentSpec.kronos_features=False`. They're precomputed for training with `precompute.extend` and read live from Redis. They're masked when missing, never forward-filled. This also needs the agent's live-parity test from the multi-strategy work: a BarFeed-driven check that the live row equals the training row.

**Every gated trade records `model_id`** in the signal metadata, so a result traces back to a checkpoint.

**The budget, measured on this 4-core PC at 1 thread** (Kronos-mini, 512-bar lookback, 12-bar horizon, 5 paths): **3.2 s per symbol per bar.**

| Bar | 25% budget | Symbols per bar (PC) | Likely on the t3.large |
|---|---|---|---|
| M5 | 75 s | ~23 | ~6–10, before CPU credits |
| M15 | 225 s | ~70 | ~20–30 |
| H1 | 900 s | all of them | all of them |

The t3.large estimate is a guess: burstable baseline is ~30% per vCPU, so expect 2–4× slower once credits run out. That's why the default timeframes should be **M15 and H1**, not M5, and why `benchmark.py` must be run on the VPS first.

## 3. Engineering notes

- **The KV cache is exact.** At 1 thread it gives the same sampled paths as upstream's loop (max difference 0.0). At 4 threads the difference is ≤ 7×10⁻⁴ from non-deterministic multithreaded float maths. **So live and precompute must use the same `threads` (1).** It's `model_id`-neutral by design, but documented.
- **CPU timings at 1 thread** (5 paths, 12-bar horizon):

| Model | Upstream loop | Cached |
|---|---|---|
| mini, 512 bars | 14.0 s | 3.2 s |
| small, 400 bars | 69.6 s | 7.6 s |
| mini, 1000 bars | 51.6 s | 15.8 s |

- **Batching doesn't help on CPU:** 4 series cost 4× one series. It only pays on a GPU.
- **Future timestamps** are `last bar + k × timeframe` in both paths. They ignore weekends and session gaps identically, so there's no parity gap.

## 4. Phase 3: fine-tuning (runbook)

1. `scripts/export_mt5_history.py`: per-symbol OHLCV CSVs in `finetune_csv/`'s format (timestamps, open, high, low, close, volume, amount). MT5 tick volume becomes `volume`; `amount` is left out.
2. On Colab or a rented GPU: clone Kronos at 67b630e, edit `finetune_csv/configs/*.yaml` (data path, `lookback`, `pred_len`), then run `finetune_tokenizer.py` and then `finetune_base_model.py` (`torchrun --nproc_per_node=1`). Train only on bars **before** the evaluation window.
3. Copy the checkpoints to `models/kronos/<model>-ft-<date>/`, and write `REVISION` = `ft-<date>-<data hash>`.
4. Re-run `research.py skill` and `gate` on the **same post-training window**. A fine-tuned model replaces zero-shot only if it beats it there.

## 5. Phased rollout and promotion rule

| Phase | What | Gate to the next phase |
|---|---|---|
| 1 ✅ | Offline service, precompute, eval, benchmark, tests | Findings reviewed by you |
| 2a | Bar publisher + `algoedge-kronos` process **shadow mode**: features computed and logged, gate decisions logged, nothing dropped | 2 weeks of logs; latency inside budget on the VPS; no execution-latency regression |
| 2b | Gate enabled per slot | Only for a symbol/strategy pair where the **rejected half did clearly worse than the allowed half out of sample** with ≥ 100 trades each side (`evaluate.gate_study`) |
| 2c | RL features | An agent with them beats the same agent without them through the validation gate |
| 3 | Fine-tuning | The fine-tuned model beats zero-shot on the same window |

## 6. Open questions for you

1. **Approve Phase 2a (shadow mode)?** Only worth it for pairs the findings support.
2. **VPS budget:** OK to add a 6th pm2 process with up to ~1.5 GB RAM on the t3.large? Measured: 345–435 MB per Kronos-mini process on this PC, including the backend imports.
3. **Stocks:** they trade only in US hours on FundedNext and Deriv. Should Kronos cover stock CFDs live, given what the findings say?
4. **Instance:** if Kronos is kept, T3 Unlimited or a c6i.large removes the CPU-credit problem for roughly $15–60/month more.
