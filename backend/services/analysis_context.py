"""
backend/services/analysis_context.py

[Phase 13 §F] Context builders for LLM analysis.

The central constraint: **a backtest result object is megabytes.** A portfolio
run carries hundreds of grouped trades, each with up to 500 candles across three
timeframes, plus run logs. Shipping that to a model is impossible (context) and
pointless (the candles carry no information the model can use).

So each builder here reduces its target to the numbers that actually carry
signal, in a compact text form. That reduction is the product: it is what turns
"here is 8 MB of JSON" into "here is what this run did, where its signals died,
and how its risk was actually deployed".

Every builder returns `(text, digest)` — the prompt body, and a small dict
persisted alongside the analysis so a saved answer stays interpretable later
without re-deriving the inputs.
"""

from __future__ import annotations

import json

import statistics
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Rough ceiling per section. Deliberately generous — Claude models here carry a
# 1M context — but not unbounded: a prompt padded with 400 near-identical trade
# rows produces worse analysis than one with 40 representative ones, because the
# signal-to-noise ratio of the prompt is itself a quality lever.
MAX_TRADE_ROWS = 60
MAX_LOG_LINES = 200
MAX_BLOCKED_ROWS = 40


# Cap on how much of a strategy spec doc goes into one prompt. Specs run to
# 40 KB; the parameter tables and rule sections are what matter, and the
# caller slices to the relevant part before this.
MAX_SPEC_CHARS = 24000


def _fmt(v: Any, nd: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:,.{nd}f}"
    return str(v)


def _pct(v: Any) -> str:
    """A value already expressed in percentage units."""
    return "—" if v is None else f"{float(v):.1f}%"


def _rate(v: Any) -> str:
    """
    A rate stored as a FRACTION, rendered as a percentage.

    Naming trap in this codebase, and a real one: `win_rate`, `sl_hit_rate`,
    `tp1_hit_rate` and — despite its name — `max_drawdown_pct` are all
    fractions. `analytics/reports.py:175` computes
    `max_drawdown_pct = max_drawdown_abs / initial_balance`, and
    `analytics/metrics.py:203` computes `win_rate = len(wins) / len(trades)`;
    the frontend multiplies by 100 at every render site. Passing these through
    a plain percent formatter reported a 21.4% drawdown as "0.2%" — a wrong
    number handed to an analyst is worse than no number, so they get their own
    helper rather than sharing one with genuine percentage fields.
    """
    return "—" if v is None else f"{float(v) * 100:.1f}%"


def _section(title: str) -> str:
    return f"\n## {title}\n"


# ─────────────────────────────────────────────────────────────────────────
# Backtest / portfolio run
# ─────────────────────────────────────────────────────────────────────────

def build_backtest_context(result: dict) -> tuple[str, dict]:
    """
    Reduce a backtest result to its diagnostic core.

    Ordered by what actually explains a result: headline metrics, then WHERE
    signals died (the rejection funnel), then how much risk was really deployed
    vs. requested (sizing diagnostics), then exits, then a trade sample. That
    ordering is not cosmetic — the funnel and the risk-deployment numbers are
    the two things that most often explain a disappointing run, and they were
    invisible before Phase 0 built them.
    """
    lines: list[str] = []
    digest: dict[str, Any] = {}

    rep = result.get("report") or {}
    trades = result.get("grouped_trades") or []
    is_portfolio = bool(result.get("per_symbol") or result.get("symbols"))
    # Saved and exported runs carry symbol/strategy/dates under
    # params_snapshot rather than at the top level, so fall through to it
    # instead of rendering "—" for the run's own identity.
    snap = result.get("params_snapshot") or {}

    def _id(key, default=None):
        return result.get(key) or snap.get(key) or default

    # ── Headline ──
    lines.append(_section("Run summary"))
    meta = {
        "Mode": "portfolio" if is_portfolio else "single-symbol",
        "Symbol(s)": _id("symbol") or ", ".join(
            str(x) for x in (_id("symbols") or [])[:12]
        ) or "—",
        "Strategy": _id("strategy_id", "—"),
        "Period": f"{_id('start_date', '?')} → {_id('end_date', '?')}",
        "Initial balance": _fmt(_id("initial_balance")),
        "Final balance": _fmt(result.get("final_balance")),
        "Risk per trade": _pct(_id("risk_per_trade_pct")),
        "Total trades (groups)": len(trades),
    }
    for k, v in meta.items():
        lines.append(f"- {k}: {v}")
    digest["meta"] = meta

    lines.append(_section("Performance"))
    perf = {
        "Net PnL": _fmt(rep.get("total_pnl")),
        "Win rate": _rate(rep.get("win_rate")),
        "Profit factor": _fmt(rep.get("profit_factor")),
        "Expectancy (R)": _fmt(rep.get("expectancy_r"), 3),
        "Sharpe": _fmt(rep.get("sharpe_ratio")),
        "Sortino": _fmt(rep.get("sortino_ratio")),
        "Max drawdown": _rate(rep.get("max_drawdown_pct")),
        "Max consecutive losses": rep.get("max_consecutive_losses"),
    }
    for k, v in perf.items():
        lines.append(f"- {k}: {v}")
    digest["performance"] = perf

    # Sample size is stated explicitly because every ratio above is meaningless
    # below ~30 trades, and a model given only the ratio will over-read it.
    if len(trades) < 30:
        lines.append(
            f"\n**Sample-size warning: {len(trades)} trades.** Treat every ratio "
            "above as directional at best; none of them is statistically "
            "meaningful at this n."
        )

    # ── Where signals died ──
    funnel = result.get("rejection_funnel") or {}
    if funnel:
        lines.append(_section("Rejection funnel — where signals died"))
        total = sum(v for v in funnel.values() if isinstance(v, (int, float)))
        for gate, count in sorted(
            ((k, v) for k, v in funnel.items() if isinstance(v, (int, float))),
            key=lambda kv: -kv[1],
        ):
            share = f" ({count / total * 100:.0f}%)" if total else ""
            lines.append(f"- {gate}: {count}{share}")
        digest["rejection_funnel"] = funnel

    blocked = result.get("blocked_signals") or []
    if blocked:
        lines.append(_section(f"Blocked signals (showing {min(len(blocked), MAX_BLOCKED_ROWS)} of {len(blocked)})"))
        by_reason: dict[str, int] = {}
        for b in blocked:
            by_reason[str(b.get("reason", "unknown"))] = by_reason.get(str(b.get("reason", "unknown")), 0) + 1
        for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {reason}: {n}")
        digest["blocked_by_reason"] = by_reason

    # ── Risk deployment ──
    realised = []
    binding: dict[str, int] = {}
    for t in trades:
        diag = (t.get("sizing_diagnostics") or {})
        if not diag:
            for sub in t.get("sub_trades") or []:
                diag = sub.get("sizing_diagnostics") or {}
                if diag:
                    break
        if diag:
            rp = diag.get("realised_risk_pct")
            if isinstance(rp, (int, float)):
                realised.append(float(rp))
            bc = diag.get("binding_constraint")
            if bc:
                binding[str(bc)] = binding.get(str(bc), 0) + 1

    if realised:
        lines.append(_section("Risk actually deployed"))
        lines.append(f"- Requested risk/trade: {_pct(_id('risk_per_trade_pct'))}")
        lines.append(f"- Realised risk median: {_fmt(statistics.median(realised), 3)}%")
        lines.append(f"- Realised risk min/max: {_fmt(min(realised), 3)}% / {_fmt(max(realised), 3)}%")
        if binding:
            lines.append("- Binding constraint counts: "
                         + ", ".join(f"{k}={v}" for k, v in sorted(binding.items(), key=lambda kv: -kv[1])))
        digest["risk_deployment"] = {
            "median_realised_pct": statistics.median(realised),
            "binding": binding,
            "n": len(realised),
        }

    # ── Exits ──
    exit_counts: dict[str, int] = {}
    for t in trades:
        for sub in t.get("sub_trades") or [t]:
            reason = str(sub.get("exit_reason") or sub.get("close_reason") or "unknown")
            exit_counts[reason] = exit_counts.get(reason, 0) + 1
    if exit_counts:
        lines.append(_section("Exit attribution"))
        for reason, n in sorted(exit_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {reason}: {n}")
        digest["exits"] = exit_counts

    # ── Per-leg breakdown ──
    per_symbol = result.get("per_symbol") or {}
    if per_symbol:
        lines.append(_section("Per-leg breakdown"))
        for key, stats in list(per_symbol.items())[:30]:
            if not isinstance(stats, dict):
                continue
            lines.append(
                f"- {key}: trades={stats.get('trades', '—')} "
                f"pnl={_fmt(stats.get('pnl'))} "
                f"win_rate={_rate(stats.get('win_rate'))}"
            )
        digest["legs"] = list(per_symbol)[:30]

    # ── Trade sample ──
    if trades:
        sample = _representative_trades(trades, MAX_TRADE_ROWS)
        lines.append(_section(f"Trade sample ({len(sample)} of {len(trades)})"))
        lines.append("| # | symbol | dir | entry | exit reason | R | pnl | confluences |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, t in enumerate(sample, 1):
            summary = ((t.get("smc_data") or {}).get("confluence_summary") or {})
            confl = ", ".join((summary.get("trigger") or [])[:2]) or "—"
            first = (t.get("sub_trades") or [{}])[0]
            lines.append(
                f"| {i} | {t.get('symbol', '—')} | {t.get('direction', '—')} | "
                f"{_fmt(first.get('entry_price'), 5)} | "
                f"{first.get('exit_reason') or first.get('close_reason') or '—'} | "
                f"{_fmt(t.get('realized_rr'), 2)} | {_fmt(t.get('combined_pnl'))} | {confl} |"
            )

    return "\n".join(lines), digest


def _representative_trades(trades: list[dict], limit: int) -> list[dict]:
    """
    Pick a sample that spans the outcome distribution rather than the first N.

    Taking `trades[:limit]` biases hard toward the start of the run — and if the
    strategy degraded over time, that is precisely the window that hides it.
    Sorting by R and sampling evenly across the sorted list keeps the best, the
    worst, and the middle.
    """
    if len(trades) <= limit:
        return trades
    ranked = sorted(trades, key=lambda t: (t.get("realized_rr") or 0))
    stride = len(ranked) / limit
    return [ranked[int(i * stride)] for i in range(limit)]


# ─────────────────────────────────────────────────────────────────────────
# Logs
# ─────────────────────────────────────────────────────────────────────────

def build_logs_context(logs: list[dict], label: str = "session") -> tuple[str, dict]:
    """
    Reduce a log slice, weighted toward errors.

    Every WARNING/ERROR/CRITICAL is kept; INFO fills whatever budget remains.
    A log dump truncated by simple recency usually drops the error and keeps the
    hundred routine lines that followed it.
    """
    errors = [e for e in logs if e.get("level") in ("WARNING", "ERROR", "CRITICAL")]
    others = [e for e in logs if e.get("level") not in ("WARNING", "ERROR", "CRITICAL")]

    keep = errors[:MAX_LOG_LINES]
    room = MAX_LOG_LINES - len(keep)
    if room > 0 and others:
        stride = max(1, len(others) // room)
        keep += others[::stride][:room]
    keep.sort(key=lambda e: str(e.get("time", "")))

    by_level: dict[str, int] = {}
    for e in logs:
        by_level[str(e.get("level"))] = by_level.get(str(e.get("level")), 0) + 1

    lines = [_section(f"Logs — {label}")]
    lines.append(f"- Total records: {len(logs)}")
    lines.append("- By level: " + ", ".join(f"{k}={v}" for k, v in sorted(by_level.items())))
    lines.append(f"- Showing {len(keep)} (every warning/error, plus an even INFO sample)\n")
    lines.append("```")
    for e in keep:
        lines.append(f"{e.get('time', '')} | {e.get('level', ''):<8} | [{e.get('category', '')}] {e.get('message', '')}")
    lines.append("```")

    return "\n".join(lines), {"total": len(logs), "by_level": by_level, "shown": len(keep)}


# ─────────────────────────────────────────────────────────────────────────
# Trades / signals
# ─────────────────────────────────────────────────────────────────────────

def build_trades_context(trades: list[dict], label: str = "live trades") -> tuple[str, dict]:
    lines = [_section(f"{label} ({len(trades)})")]
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    total_pnl = sum(float(t.get("pnl") or 0) for t in trades)
    lines.append(f"- Wins/losses: {wins}/{len(trades) - wins}")
    lines.append(f"- Net PnL: {_fmt(total_pnl)}")
    lines.append("")
    lines.append("| # | symbol | strategy | dir | entry | exit | pnl | reason |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, t in enumerate(trades[:MAX_TRADE_ROWS], 1):
        lines.append(
            f"| {i} | {t.get('symbol', '—')} | {t.get('strategy_id', '—')} | "
            f"{t.get('direction', '—')} | {_fmt(t.get('entry_price'), 5)} | "
            f"{_fmt(t.get('exit_price'), 5)} | {_fmt(t.get('pnl'))} | "
            f"{t.get('exit_reason') or t.get('close_reason') or '—'} |"
        )
    return "\n".join(lines), {"n": len(trades), "wins": wins, "net_pnl": total_pnl}


def build_signals_context(signals: list[dict]) -> tuple[str, dict]:
    """Fired vs. blocked, and the gate that blocked each — the useful half."""
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for s in signals:
        st = str(s.get("status", "unknown"))
        by_status[st] = by_status.get(st, 0) + 1
        if s.get("reject_reason"):
            r = str(s["reject_reason"])
            by_reason[r] = by_reason.get(r, 0) + 1

    lines = [_section(f"Signals ({len(signals)})")]
    lines.append("- By status: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])))
    if by_reason:
        lines.append("- Rejection reasons:")
        for r, n in sorted(by_reason.items(), key=lambda kv: -kv[1])[:20]:
            lines.append(f"  - {r}: {n}")
    lines.append("")
    lines.append("| # | time | symbol | strategy | dir | status | reason |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, s in enumerate(signals[:MAX_TRADE_ROWS], 1):
        lines.append(
            f"| {i} | {s.get('created_at', '—')} | {s.get('symbol', '—')} | "
            f"{s.get('strategy_id', '—')} | {s.get('direction', '—')} | "
            f"{s.get('status', '—')} | {s.get('reject_reason') or '—'} |"
        )
    return "\n".join(lines), {"n": len(signals), "by_status": by_status, "by_reason": by_reason}


def build_strategy_config_context(
    strategy_id: str, params: dict, spec_excerpt: str | None = None
) -> tuple[str, dict]:
    """
    A strategy's live parameters, optionally against its own spec document.

    The spec excerpt is what makes this worth asking about: "are these
    parameters sane" is a much weaker question than "do these parameters match
    what the strategy's own specification says they should be", and the second
    is answerable only if the spec is in the context.
    """
    lines = [_section(f"Strategy configuration — {strategy_id}")]
    lines.append("| parameter | value |")
    lines.append("|---|---|")
    for k in sorted(params):
        v = params[k]
        if isinstance(v, (dict, list)):
            v = json.dumps(v)[:200]
        lines.append(f"| `{k}` | {v} |")

    if spec_excerpt:
        lines.append("")
        lines.append(_section("Specification (excerpt)"))
        lines.append(spec_excerpt[:MAX_SPEC_CHARS])

    return "\n".join(lines), {
        "strategy_id": strategy_id,
        "n_params": len(params),
        "has_spec": bool(spec_excerpt),
    }


def build_trade_chart_context(group: dict) -> tuple[str, dict]:
    """
    One trade, with the chart markings the strategy emitted when it fired.

    This is the context behind "was this strategy implemented correctly on this
    trade". The markings are the strategy's own record of what it measured
    (see strategies/core/markings.py) — role-grouped so the model can tell a
    required trigger apart from background context, which is exactly the
    distinction a human reads off the chart.
    """
    lines = [_section(f"Trade — {group.get('symbol', '?')} {group.get('direction', '')}")]
    lines.append(f"- Strategy: {group.get('strategy_name') or group.get('strategy_id', '—')}")
    lines.append(f"- Entry: {_fmt(group.get('entry_price'), 5)} at {group.get('entry_time', '—')}")
    lines.append(f"- Exit: {_fmt(group.get('exit_price'), 5)} at {group.get('exit_time', '—')}")
    lines.append(f"- Combined P&L: {_fmt(group.get('combined_pnl'))}")
    lines.append(f"- Realised RR: {_fmt(group.get('realized_rr'))}")
    lines.append(f"- Confluence score: {group.get('confluence_score', '—')}")

    subs = group.get("sub_trades") or []
    if subs:
        lines.append("")
        lines.append("| leg | TP | volume | exit reason | pnl |")
        lines.append("|---|---|---|---|---|")
        for i, t in enumerate(subs, 1):
            lines.append(
                f"| {i} | {_fmt(t.get('take_profit'), 5)} | {_fmt(t.get('volume'), 2)} | "
                f"{t.get('exit_reason', '—')} | {_fmt(t.get('pnl'))} |"
            )

    smc = group.get("smc_data") or {}
    markings = (smc.get("boxes") or []) + (smc.get("lines") or []) + (smc.get("markers") or [])
    if markings:
        by_role: dict[str, list] = {}
        for m in markings:
            by_role.setdefault(m.get("role", "context"), []).append(m)
        lines.append("")
        lines.append(_section("What the strategy measured"))
        # trigger first: the conditions that actually fired the entry.
        for role in ("trigger", "confluence", "invalidation", "context"):
            items = by_role.get(role)
            if not items:
                continue
            lines.append(f"\n**{role.title()}**")
            for m in items:
                geom = (
                    f"{_fmt(m.get('bottom'), 5)}–{_fmt(m.get('top'), 5)}"
                    if m.get("top") is not None and m.get("bottom") != m.get("top")
                    else _fmt(m.get("price") or m.get("top"), 5)
                )
                detail = m.get("detail") or {}
                extra = ", ".join(f"{k}={v}" for k, v in list(detail.items())[:6] if v is not None)
                lines.append(
                    f"- `{m.get('kind') or m.get('type')}` {m.get('label', '')} "
                    f"[{m.get('timeframe', '?')}] @ {geom}" + (f" — {extra}" if extra else "")
                )
    else:
        lines.append("")
        lines.append(
            "_No chart markings on this trade. Either it predates the marking "
            "pipeline, or the strategy did not emit any — worth flagging._"
        )

    return "\n".join(lines), {
        "symbol": group.get("symbol"),
        "pnl": group.get("combined_pnl"),
        "n_markings": len(markings),
        "legs": len(subs),
    }


def build_orderflow_context(symbol: str, flow: dict) -> tuple[str, dict]:
    """
    CVD / order-flow imbalance for one symbol.

    Carries the proxy caveat into the prompt deliberately: MT5 CFD ticks have no
    aggressor flag, so CVD here is inferred from price relative to bid/ask. An
    analysis that treats it as a true tape read would be overconfident, and the
    model can only know that if the context says so.
    """
    lines = [_section(f"Order flow — {symbol}")]
    lines.append(
        "_Source caveat: MT5 CFD tick data carries no aggressor flag and no true "
        "exchange volume. CVD below is INFERRED from tick price relative to "
        "bid/ask — a proxy, not a tape read._\n"
    )
    for k, v in flow.items():
        if isinstance(v, (int, float)):
            lines.append(f"- {k}: {_fmt(v, 4)}")
        elif isinstance(v, (str, bool)):
            lines.append(f"- {k}: {v}")
    return "\n".join(lines), {"symbol": symbol, "keys": sorted(flow)}


# ─────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a quantitative trading analyst reviewing output from AlgoEdge, \
an algorithmic trading system running six ICT/SMC-style strategies on MT5.

How to be useful here:

- **Lead with what the numbers actually support.** State sample sizes. If a \
metric rests on fewer than ~30 trades, say so before interpreting it, and do not \
build an argument on it.
- **Separate mechanism from correlation.** "Win rate fell in the second half" is \
an observation; "the trailing stop is exiting before TP2 because the activation \
RR sits below the median MFE" is a mechanism. Aim for the second, and say which \
one you have.
- **Prefer the rejection funnel and realised-risk figures over the headline \
ratios** when explaining a disappointing result. Most surprises live there.
- **Be specific about the fix.** Name the parameter, the file if you can infer \
it, and the direction of change — not "consider tuning the stop".
- **Say when you cannot tell.** If the data given does not distinguish two \
explanations, name both and say what additional data would separate them. Do not \
resolve it by guessing.

Keep the response tight. No preamble, no restating the numbers back before \
analysing them."""


DEFAULT_QUESTIONS = {
    "backtest": "Analyse this backtest run. What is working, what is not, and what "
                "specifically should change? Prioritise by expected impact.",
    "portfolio": "Analyse this portfolio backtest. Cover per-leg contribution, whether "
                 "the legs are diversifying or duplicating each other, and which legs "
                 "should be resized, re-parameterised, or dropped.",
    "trades": "Analyse these live trades. Identify recurring failure patterns and "
              "whether execution matched the strategies' intent.",
    "signals": "Analyse this signal flow. Which gate is rejecting the most, and is "
               "each rejection defensible or over-tight?",
    "logs": "Analyse these logs. Identify errors, their likely root cause, and "
            "anything that looks wrong but is not raising.",
}


def build_prompt(context_text: str, question: str | None, target_type: str) -> str:
    q = question or DEFAULT_QUESTIONS.get(target_type) or "Analyse the data below."
    return f"{context_text}\n\n---\n\n## Question\n\n{q}\n"
