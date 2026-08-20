from dataclasses import dataclass

@dataclass
class BiasIFVGParams:
    # Spec's documented primary NY-session edge is 09:30-11:00 ET. The previous
    # 08:00-17:00 default spanned nearly the whole trading day and effectively
    # disabled the "primary trading window" hypothesis the strategy is built on.
    session_start: str = "09:30"
    session_cutoff: str = "11:00"
    # Spec's mandatory day-stop rule (stop after 1 win; after 1 loss only take a
    # 2nd trade if it's A+; stop after 2 losses regardless) caps realistic same-day
    # trade count at 2 — see BiasIFVGEngine.notify_outcome/_can_trade_today.
    max_trades_per_day: int = 2
    target_rr: float = 2.0
    sl_buffer_atr_mult: float = 0.0
    """
    Additional SL buffer as a multiple of ATR(14), applied after structural SL is computed.
    0.0 = disabled. Example: 0.5 widens SL by 0.5 x ATR to reduce tight-SL over-fitting.
    Configurable from the Settings panel (live) and Backtester strategy params section.
    """
    a_plus_confluence_threshold: int = 85
    """
    Minimum confluence_score a setup must have to qualify as a "clearly A+ setup"
    (spec §day-stop rule) — the only kind of setup allowed to be a 2nd trade after
    an opening loss. Default matches the engine's current fixed confluence_score.
    """
    rejection_min_body_atr_mult: float = 0.15
    """
    Minimum candle body size, as a multiple of M15 ATR(14), for a rejection wick to
    qualify as a rejection-block key level. Filters out near-doji candles (body ~0)
    that would otherwise produce noisy, low-quality "rejection levels" purely from
    wick-to-body ratio. 0.0 disables the floor.
    """
