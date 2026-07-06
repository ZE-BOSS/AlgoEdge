# Implementation Plan: Multi-Strategy Audit & Target Architecture

This document serves as the comprehensive audit and implementation plan to transition the AlgoEdge codebase to a multi-strategy architecture where `SMC_Strategy-1.md` (Strategy One) and `strategy2.md` (Strategy Two) are the **only two canonical strategies** implemented.

---

## 1. Current State

### Documentation Inventory
- `CompoundingPlan_Spec.md`: Details the stepped fixed-dollar risk compounding algorithm.
- `CrashBoom_Strategy_Spec.md`: Strategy spec for synthetic indices based on continuous drift + discrete jump mechanics.
- `Frontend_PWA_LLM_Spec.md`: Frontend architecture, PWA features, and LLM trade analysis integration spec.
- `RiskManagement_Spec.md`: Core risk management system spec including multi-TP, trailing stops, and break-even rules.
- `SMC_Strategy-1.md`: **[CANONICAL: Strategy One]** Algorithmic strategy specification detailing the core Smart Money Concepts entry model (liquidity sweeps, order blocks, candlestick confirmation).
- `SMC_Strategy_Spec.md`: Older, alternative SMC configuration and strategy specification.
- `TradingBot_MasterPlan-2.md`: Overall system architecture, technology stack, and multi-user scaling vision.
- `strategy2.md`: **[CANONICAL: Strategy Two]** Definitive step-by-step SMC execution flow combining HTF bias, IPDM phase gating, and LTF ChoCH confirmation.

### Codebase Strategy Inventory
Currently, the codebase implements a single, monolithic SMC strategy.
- `backend/strategies/base_strategy.py`: Abstract base class defining the strategy interface.
- `backend/strategies/registry.py`: Registry for storing and initializing strategies.
- `backend/strategies/smc/engine.py`: The monolithic core SMC execution engine orchestrating all multi-timeframe signal logic.
- `backend/strategies/smc/params.py`: Configuration schemas and default parameters for both the SMC strategy and global risk management.
- `backend/strategies/smc/signals.py`: Validates signals against hard filters (RR, POI, spread).
- `backend/strategies/smc/*.py`: Various detection modules (`asian_range.py`, `candlestick.py`, `confluence.py`, `fvg.py`, `ipdm.py`, `liquidity.py`, `market_structure.py`, `order_blocks.py`, `premium_discount.py`, `supply_demand.py`).
- `backend/risk/*.py`: Global risk components (`engine.py`, `multi_tp.py`, `position_sizer.py`, `trailing_manager.py`, `breakeven_manager.py`, `circuit_breaker.py`, `news_filter.py`, `compounding.py`).
- `backend/backtester/*.py`: Backtesting execution environment, runner, optimizer, and reporting tools.

---

## 2. Target State

The system must run as a **MULTI-STRATEGY system** with exactly two distinct trading strategies:
1. **Strategy One (`strategy_one/`)**: Implements `SMC_Strategy-1.md` (Core SMC model, focusing on sweeps, OBs, and strict candlestick confirmation).
2. **Strategy Two (`strategy_two/`)**: Implements `strategy2.md` (4-Layer Multi-Timeframe Model, utilizing HTF Bias, IPDM Phase gating, M15 ChoCH, and M5 Confirmation).

### Target Architecture
- **Shared Core Indicators**: The mathematical detection logic (BOS/ChoCH, FVGs, OBs, IPDM, Asian Range) currently trapped inside the monolithic `smc` strategy folder must be extracted into a shared `backend/strategies/core/` directory. Both Strategy One and Strategy Two will import and utilize these shared primitives.
- **Strategy Selection**: Handled via `registry.py`. Users configure which strategy runs on which symbol via the frontend settings (e.g., `EURUSD: StrategyOne`, `XAUUSD: StrategyTwo`).
- **Configuration Isolation**: Strategy-specific parameters must live in their respective strategy directories. Global risk parameters currently trapped in `smc/params.py` must be migrated to `backend/risk/params.py`. All parameters will be fully user-controllable (exposed to the UI/DB schemas).

---

## 3. Full Discrepancy List

### A. Architectural Discrepancies
1. **Monolithic Strategy Engine (Severity: CRITICAL | Type: Architectural)**
   - *Current Code:* A single massive `backend/strategies/smc/engine.py` tries to combine the rules of both canonical specs into one monolithic pipeline.
   - *Spec Requirement:* The system must support Strategy One and Strategy Two as distinct, independent algorithms.
2. **Parameter Coupling (Severity: MODERATE | Type: Architectural)**
   - *Current Code:* `backend/strategies/smc/params.py` houses both strategy-specific parameters (e.g., OTE Fib levels) and global risk parameters (e.g., Kelly sizing, Multi-TP).
   - *Spec Requirement:* Risk parameters apply system-wide and must be centrally located in the Risk module.

### B. Legacy Conflicts & Missing Logic
1. **Stop Loss Hardcoding (Severity: MODERATE | Type: Legacy Conflict)**
   - *Current Code:* `engine.py` hardcodes a hierarchical structural SL fallback (Priority 1: M15 Swing, Priority 2: OB Extreme).
   - *Spec Requirement:* `RiskManagement_Spec.md` outlines user-selectable `sl_method` (`OB_EXTREME`, `SWING_POINT`, `FVG_EDGE`, `ATR_BASED`). The hardcoded logic must be replaced with configuration-driven options.
2. **IPDM & FVG Exceptions (Severity: MINOR | Type: Config Mismatch)**
   - *Current Code:* `engine.py` contains hardcoded exemptions (e.g., skipping IPDM synchronization and FVG filters to boost trade frequency).
   - *Spec Requirement:* These exemptions must be converted into user-controllable Boolean toggles (e.g., `require_ipdm_expansion_phase: bool`) within the Strategy Two parameter schema, defaulting to `False` if preferred, but not hardcoded as absolute bypasses in the engine logic.

---

## 4. Existing Codebase Issues

- **Hardcoded Asset Type Checks:** `engine.py` explicitly checks `if instrument_type != "SYNTHETIC"` to bypass the Asian Range filter. This should be driven by an instrument profile configuration, not hardcoded string matching.
- **Missing Guardrails:** The multi-TP scaling logic assumes ideal MT5 execution; it lacks protective bounds for symbols with extremely high minimum lot sizes where percentage-based splitting results in 0 lots.
- **Risk Module Independence:** The risk modules (`circuit_breaker`, `news_filter`) currently rely on context passed down from the monolithic SMC engine rather than operating as a pure independent layer.

---

## 5. Files/Modules to Delete

1. `docs/CrashBoom_Strategy_Spec.md`
   - *Justification:* Violates the explicit requirement that Strategy One and Strategy Two are the *only* two strategies implemented.
2. `docs/SMC_Strategy_Spec.md`
   - *Justification:* An older, superseded alternative SMC configuration document that conflicts with the canonical specs.
3. `backend/strategies/smc/engine.py` (Eventually)
   - *Justification:* Will be destroyed and replaced by `strategy_one/engine.py` and `strategy_two/engine.py`.

---

## 6. Files/Modules to Modify

1. **`backend/strategies/smc/params.py`**
   - *Action:* Split entirely. Move risk parameters to `backend/risk/params.py`. Move strategy parameters to the individual strategy folders.
2. **`backend/strategies/registry.py`**
   - *Action:* Update to support registering and instantiating `StrategyOne` and `StrategyTwo` dynamically based on user config.
3. **`backend/risk/engine.py`**
   - *Action:* Update to accept standard signals from the multi-strategy registry rather than SMC-specific contexts. Ensure it applies the `sl_method` configuration strictly.

---

## 7. Files/Modules to Create

1. **`backend/strategies/core/` (Directory)**
   - *Purpose:* House all the mathematical detection primitives currently located in the `smc` folder (e.g., `market_structure.py`, `ipdm.py`, `fvg.py`). Both strategies will import from here.
2. **`backend/strategies/strategy_one/engine.py` & `params.py`**
   - *Purpose:* Dedicated implementation of `SMC_Strategy-1.md`.
3. **`backend/strategies/strategy_two/engine.py` & `params.py`**
   - *Purpose:* Dedicated implementation of `strategy2.md`.
4. **`backend/risk/params.py`**
   - *Purpose:* Centralized, global risk parameter schema decoupled from strategy-specific logic.

---

## 8. Open Questions

1. **Symbol Subscription Management:** If a user configures EURUSD to trade using *both* Strategy One and Strategy Two, should the MT5 bridge generate two separate tick/bar loops, or will the bridge push a single bar event to the strategy registry which then broadcasts it to both strategy instances?
2. **Strategy Two IPDM Exemption:** The canonical spec for Strategy Two recently updated the documentation to note that the IPDM Expansion phase gate is *exempted* in the codebase to improve trade frequency. Should the new `Strategy_Two` engine permanently remove the IPDM module entirely, or just expose it as an optional toggle (`enforce_ipdm_phase: false`)?
