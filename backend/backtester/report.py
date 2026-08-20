"""
backend/backtester/report.py

Generates summary reports from backtest results, plus the shared post-processing
helpers both backtest engines use to correct metrics that `generate_risk_report`
cannot compute correctly on its own.

Why the corrections live here rather than in `backend/analytics/reports.py`:
`generate_risk_report()` is handed GROUPED trades (one dict per signal group),
so every metric it derives from `exit_reason` reflects the *group's* terminal
exit reason only. That is the right unit for win-rate/PnL but the wrong unit for
"how often did TP1 actually get hit", because a group whose TP1 leg banked
profit and whose TP2/TP3 legs later stopped out at break-even reports a
group-level exit_reason of `BE_SL` — the TP1 hit vanishes. Likewise the engines
track a bar-by-bar floating equity curve that the report never sees, so its
drawdown is computed from closed-trade equity only.
"""

from typing import Any

from backend.analytics.metrics import calculate_max_drawdown, compute_portfolio_stats
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def generate_backtest_report(backtest_results: dict[str, Any]) -> dict[str, Any]:
    """
    Format backtest results into a standardized report.

    NOTE: currently unused/dead code — no callers anywhere in the codebase
    (confirmed via grep). `equity_curve`/`drawdown_curve`/`monthly_returns`
    are hardcoded empty here; the actual wired-in report path is
    `backend.analytics.reports.generate_risk_report`, called directly by
    `backtester/engine.py` and `backtester/portfolio_engine.py`. Do not
    assume this function is live without verifying a caller has been added.
    """
    trades = backtest_results.get("trades", [])
    initial_balance = backtest_results.get("initial_balance", 10000.0)
    stats = compute_portfolio_stats(trades, initial_balance)

    report = {
        "summary": stats,
        "equity_curve": [],
        "drawdown_curve": [],
        "monthly_returns": {},
        "trade_log": trades
    }

    return report


# ──────────────────────────────────────────────────────────────────────────
#  Leg-level exit-reason statistics
# ──────────────────────────────────────────────────────────────────────────

def compute_leg_level_exit_stats(
    legs: list[dict[str, Any]],
    group_count: int | None = None,
) -> dict[str, float]:
    """
    Compute TP/SL/BE/TRAIL hit rates from LEG-level (sub-trade) exit reasons.

    `legs` is the flat list of closed positions the engine accumulated (one per
    TP level per signal), NOT the grouped list. `group_count` is the number of
    distinct signal groups; rates are expressed as
    "fraction of signal groups that had at least one leg exit this way", which
    is what a reader means by "TP1 hit rate". If `group_count` is omitted it is
    derived from the distinct `group_id`s present in `legs`.

    The previous (incorrect) figures counted only groups whose *group-level*
    exit_reason equalled "TPn". Since most multi-TP groups terminate on the
    final leg's BE_SL/SL, genuine TP1 hits were undercounted by roughly an
    order of magnitude (measured: 1-11% reported vs 39-63% actual).

    Exit-reason vocabulary produced by the engines (keep in sync):
        "TP1".."TP5", "SL", "BE_SL", "TRAIL_SL",
        "TIME_LIMIT", "SESSION_END", "END_OF_DATA", "END_OF_BACKTEST"
    Note that "TRAIL" and "BE" never appear verbatim — the old
    `exit_reasons.count("TRAIL")` in analytics/metrics.py therefore always
    returned exactly 0, and `be_hit_rate` read a `be_applied` flag that
    trade_grouper never copies onto the group dict (also always 0).
    """
    stats: dict[str, float] = {
        "tp1_hit_rate": 0.0, "tp2_hit_rate": 0.0, "tp3_hit_rate": 0.0,
        "tp4_hit_rate": 0.0, "tp5_hit_rate": 0.0,
        "sl_hit_rate": 0.0, "be_hit_rate": 0.0, "trail_hit_rate": 0.0,
    }
    if not legs:
        return stats

    groups_with: dict[str, set] = {k: set() for k in stats}

    def _gid(leg: dict[str, Any]) -> Any:
        return leg.get("group_id") or leg.get("id") or id(leg)

    all_groups: set = set()
    for leg in legs:
        gid = _gid(leg)
        all_groups.add(gid)
        reason = str(leg.get("exit_reason") or "").upper()

        if reason.startswith("TP"):
            suffix = reason[2:]
            if suffix.isdigit() and 1 <= int(suffix) <= 5:
                groups_with[f"tp{int(suffix)}_hit_rate"].add(gid)
        elif reason == "SL":
            groups_with["sl_hit_rate"].add(gid)
        elif reason == "BE_SL":
            groups_with["be_hit_rate"].add(gid)
        elif reason == "TRAIL_SL":
            groups_with["trail_hit_rate"].add(gid)

    total = group_count if (group_count and group_count > 0) else len(all_groups)
    if total <= 0:
        return stats

    for key, gids in groups_with.items():
        stats[key] = len(gids) / total
    return stats


def apply_leg_level_hit_rates(
    report: Any,
    legs: list[dict[str, Any]],
    grouped_trades: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    """
    Overwrite a RiskReport's tpN/sl/be/trail hit rates with leg-level figures.

    Applied by both engines immediately after `generate_risk_report(...)`.
    Returns the computed stats dict so callers can log/surface it.
    """
    group_count = len(grouped_trades) if grouped_trades is not None else None
    stats = compute_leg_level_exit_stats(legs, group_count)
    if report is None:
        return stats
    for key, value in stats.items():
        try:
            setattr(report, key, float(value))
        except Exception:
            pass
    return stats


# ──────────────────────────────────────────────────────────────────────────
#  Bar-level drawdown
# ──────────────────────────────────────────────────────────────────────────

def apply_bar_level_drawdown(
    report: Any,
    equity_curve: list[float],
    initial_balance: float,
) -> dict[str, float]:
    """
    Recompute max drawdown from the engine's BAR-level equity curve and write it
    back onto the report.

    `generate_risk_report` builds its own equity series by walking each grouped
    trade's `balance_after` — i.e. realized, closed-trade equity sampled once per
    group, in exit order. That series is blind to (a) floating drawdown while
    positions are open, and (b) the interleaving of concurrent positions across
    symbols, so the reported figure disagreed with the bar-level curve in both
    directions (measured 0.5-1% either way, which is why it wasn't a plain
    closed-vs-open-equity offset). The bar-level curve the engines already
    maintain — one point per bar, balance + floating PnL of everything still
    open — is the accurate series, so it becomes the single source of truth.

    Both figures are anchored to `initial_balance` (fixed-capital basis), which
    is the convention `calculate_max_drawdown` already uses and the one prop-firm
    drawdown limits are evaluated on.
    """
    result = {"max_drawdown_pct": 0.0, "max_drawdown_abs": 0.0, "calmar_ratio": 0.0}
    if not equity_curve:
        return result

    curve = [float(v) for v in equity_curve if v is not None]
    if not curve:
        return result

    dd_pct, dd_abs = calculate_max_drawdown(curve, initial_balance)
    result["max_drawdown_pct"] = dd_pct
    result["max_drawdown_abs"] = dd_abs

    if report is not None:
        prev_pct = getattr(report, "max_drawdown_pct", 0.0) or 0.0
        try:
            report.max_drawdown_pct = dd_pct
            report.max_drawdown_abs = dd_abs
            # Calmar is total return / max drawdown — must be rebased on the
            # corrected drawdown or it silently keeps the closed-equity figure.
            total_pnl = getattr(report, "total_pnl", 0.0) or 0.0
            if dd_pct > 0 and initial_balance:
                total_return_pct = total_pnl / initial_balance
                report.calmar_ratio = total_return_pct / dd_pct
                result["calmar_ratio"] = report.calmar_ratio
            else:
                report.calmar_ratio = 0.0
        except Exception:
            pass

        if abs(prev_pct - dd_pct) > 0.0005:
            logger.info(
                f"[REPORT] max_drawdown_pct corrected from closed-trade basis "
                f"{prev_pct * 100:.2f}% -> bar-level basis {dd_pct * 100:.2f}% "
                f"(abs ${dd_abs:,.2f}, {len(curve)} equity points)"
            )
    return result
