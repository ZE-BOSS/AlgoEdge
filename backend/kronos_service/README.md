# kronos_service

[Kronos](https://github.com/shiyu-coder/Kronos) as an **advisory** forecast feature service. It never places orders, sets SL/TP, sizes positions, or overrides the risk engine or `prop_firm_validator`.

**Status: Phase 1 (offline) only. Nothing in the trading stack imports this package.**

- Findings: `research/kronos/FINDINGS-2026-10-19.md`. Verdict: no usable signal; do not enable live.
- Plan: `research/kronos/IMPLEMENTATION_PLAN.md`.

## Install (separate from venv_win)

```bash
py -3.12 -m pip install --target .kronos_deps -r requirements-kronos.txt
```

Weights go to `models/kronos/<name>/` (`config.json`, `model.safetensors`, plus a `REVISION` file holding the HF commit). Use the pinned revisions:

| Repo | Revision |
|---|---|
| NeoQuasar/Kronos-mini | f4e68697d9d5aed55cef5c96aabc3376bcad9f81 |
| NeoQuasar/Kronos-Tokenizer-2k | 26966d0035065a0cae0ebad7af8ece35bc1fb51c |
| NeoQuasar/Kronos-small | 901c26c1332695a2a8f243eb2f37243a37bea320 |
| NeoQuasar/Kronos-Tokenizer-base | 0e0117387f39004a9016484a186a908917e22426 |

Example download for one file:

```bash
curl -L -C - -o models/kronos/Kronos-mini/model.safetensors https://huggingface.co/NeoQuasar/Kronos-mini/resolve/f4e68697d9d5aed55cef5c96aabc3376bcad9f81/model.safetensors
```

## Run

All commands need `PYTHONPATH=".kronos_deps;.kronos_deps/_small;venv_win/Lib/site-packages"`.

Benchmark this box:

```bash
py -3.12 -m backend.kronos_service.benchmark --threads 1
```

Forecast skill:

```bash
py -3.12 -m backend.kronos_service.research skill --symbols AAPL,NVDA --tf H1
```

Gate value:

```bash
py -3.12 -m backend.kronos_service.research gate --symbols XAUUSD --tf M5 --strategies ORB_v1
```

Tests:

```bash
py -3.12 -m pytest tests/test_kronos_service.py
```

The real-model tests skip themselves when torch or the weights are missing.

## Rules this code keeps

- **Same thread count.** Precompute and live must use the same `threads` (1). Multithreaded float maths isn't bit-exact, and sampled tokens can flip.
- **`model_id`** is on every feature row. It changes when the checkpoint or any output-affecting setting changes.
- **Decision bar only.** The window ends at the decision bar, so features at bar *t* never see bar *t+1* (`test_features_at_a_bar_do_not_see_any_later_bar`).
- **Gate failures.** Missing or stale features follow `fail_mode` (`open` by default). Every decision has a `to_log()` record.
