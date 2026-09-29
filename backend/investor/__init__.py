"""
backend/investor — the investor ledger.

Isolated from the trading engine on purpose: this module READS the pool's value
and never writes to it. A bug here must not be able to touch a live position,
and an investor request must never reach MT5.
"""
