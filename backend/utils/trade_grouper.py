import numpy as np
import pandas as pd
from typing import Any
from backend.utils.timeutils import detect_session
from backend.utils.logger import get_logger
from datetime import datetime, timezone

logger = get_logger(__name__)

# [V1] Marking-kind routing. Imported rather than re-listed so adding a kind in
# strategies/core/markings.py cannot silently start dropping markings here.
from backend.strategies.core.markings import (  # noqa: E402
    BOX_KINDS as _BOX_KINDS,
    LINE_KINDS as _LINE_KINDS,
    POINT_KINDS as _POINT_KINDS,
)

def _to_epoch_seconds(val):
    """
    Robustly convert an epoch number, python/pandas datetime, or numpy scalar
    to epoch seconds (float). Mirrors backend/backtester/engine.py's helper —
    duplicated here (rather than imported) to avoid a circular import between
    engine.py and this module. Keep both in sync if either changes.
    """
    if val is None:
        return None
    if isinstance(val, (int, float, np.integer, np.floating)):
        return float(val)
    if isinstance(val, datetime):
        return val.timestamp()
    if hasattr(val, "timestamp"):
        try:
            return float(val.timestamp())
        except Exception:
            pass
    try:
        return pd.Timestamp(val).timestamp()
    except Exception:
        return None

def _epoch_to_iso(epoch_val) -> str:
    """Convert epoch timestamp (incl. numpy scalars) or datetime to ISO string."""
    if isinstance(epoch_val, str):
        return epoch_val
    secs = _to_epoch_seconds(epoch_val)
    if secs is None:
        return str(epoch_val)
    try:
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    except Exception:
        return str(epoch_val)

def _calc_duration_minutes(entry_time, exit_time) -> float:
    """Calculate trade duration in minutes, robust to numpy scalar / datetime / epoch inputs."""
    e = _to_epoch_seconds(entry_time)
    x = _to_epoch_seconds(exit_time)
    if e is None or x is None:
        return 0.0
    return (x - e) / 60.0

def _resolve_symbol_candles(candles: Any, symbol: str):
    """
    `candles` may be a single DataFrame (single-symbol engine.py path) or a
    {symbol: DataFrame} dict (portfolio_engine.py path, which simulates many
    symbols on one global timeline and has no single DataFrame to hand over).
    Normalize to "the DataFrame relevant to this group's symbol", or None.
    """
    if candles is None:
        return None
    if isinstance(candles, dict):
        return candles.get(symbol)
    return candles

def group_trades(trades: list[dict], candles: Any = None, candles_m15: Any = None, candles_m5: Any = None) -> list[dict]:
    """Group trades by group_id for combined P&L display, extracting chart data for frontend."""
    from collections import OrderedDict
    groups = OrderedDict()
    for t in trades:
        gid = t.get("group_id", t.get("id", "unknown"))
        if gid not in groups:
            # ── ENTRY-TIME stop, not the mutated one (R-metric corruption fix) ──
            # `t["stop_loss"]` is mutated IN PLACE by the break-even and trailing
            # logic while the position is open (engine.py `pos["stop_loss"] =
            # action["new_sl"]`), so by the time a leg closes it holds the FINAL
            # stop, not the stop the trade was sized and risked against.
            #
            # Every R-multiple downstream divides by abs(entry - stop_loss):
            # realized_rr / max_rr_achieved / planned_rr / pnl_r below, and
            # expectancy_r / avg_win_r / best_trade_r / total_pnl_r in
            # analytics/reports.py (which reads this same group-level key).
            # Once BE has moved the stop to ~entry the risk distance collapses
            # toward zero and R explodes without bound.
            #
            # Measured over the 62 saved runs in debug/: 479 of 4380 groups
            # (10.9%) carried a moved stop here. Effect on the R distribution,
            # e.g. htffvgflip: avg win 12.10R -> 1.68R, max 239.2R -> 5.6R;
            # biaskeylevelifvg: avg win 8.92R -> 1.73R, max 190.9R -> 7.2R.
            # Dollar P&L is unaffected — this is purely a measurement defect,
            # but it is the one that makes every R-based conclusion fiction.
            #
            # [7.8/H3] Preference order, most-correct first:
            #   1. t["initial_stop_loss"] — the position's stop as actually
            #      opened, i.e. RE-ANCHORED TO THE FILL PRICE (Phase 2 §2.7)
            #      before any BE/trailing mutation. This is the real risk the
            #      trade was taken against.
            #   2. original_signal.stop_loss — the strategy's THEORETICAL
            #      pre-fill stop (kept for older records where
            #      initial_stop_loss doesn't exist yet, and for engines that
            #      never got the fill re-anchor). Measurably less accurate
            #      than #1 whenever fill slippage moved the actual stop.
            #   3. t["stop_loss"] — the mutated, current value; last resort.
            _init_sl = t.get("initial_stop_loss")
            _sig_sl = (t.get("original_signal") or {}).get("stop_loss")
            _entry_sl = _init_sl if _init_sl else (_sig_sl if _sig_sl else t.get("stop_loss", 0))
            groups[gid] = {
                "group_id": gid,
                "symbol": t.get("symbol", ""),
                "strategy_id": t.get("strategy_id", t.get("strategy", "UNKNOWN")),
                "direction": t.get("direction", ""),
                "entry_price": t.get("entry_price", 0),
                "entry_time": t.get("entry_time"),
                "entry_time_iso": t.get("entry_time_iso", ""),
                "entry_session": t.get("entry_session", "UNKNOWN"),
                "stop_loss": _entry_sl,
                # [7.8/H3] Also emitted explicitly at group level (not just
                # folded into "stop_loss") so any frontend consumer can apply
                # its own initial_stop_loss ?? original_signal?.stop_loss ??
                # stop_loss fallback chain without depending on this
                # function's own resolution order.
                "initial_stop_loss": _entry_sl,
                "sub_trades": [],
                "combined_pnl": 0,
                "combined_volume": 0,
                "tp_count": 0,
                "best_exit": None,
                "worst_exit": None,
                "entry_snapshot_b64": t.get("entry_snapshot_b64", ""),
                "entry_confirmations": t.get("entry_confirmations", []),
                "confluence_score": t.get("confluence_score", 0),
                "balance_before": t.get("balance_before", 0),
                "chart_data": [],
                "smc_data": {"boxes": [], "lines": [], "markers": []},
                "original_signal": t.get("original_signal", {}),
            }
        g = groups[gid]
        g["sub_trades"].append(t)
        g["combined_pnl"] += t.get("pnl", 0)
        g["combined_volume"] += t.get("volume", 0)
        g["tp_count"] += 1

        # Track best/worst exits
        if g["best_exit"] is None or t.get("pnl", 0) > g["best_exit"].get("pnl", 0):
            g["best_exit"] = t
        if g["worst_exit"] is None or t.get("pnl", 0) < g["worst_exit"].get("pnl", 0):
            g["worst_exit"] = t

    # Add summary fields
    for g in groups.values():
        sub_trades = g["sub_trades"]
        wins = sum(1 for t in sub_trades if t.get("pnl", 0) > 0)
        g["tp_wins"] = wins
        g["tp_losses"] = g["tp_count"] - wins
        g["is_net_winner"] = g["combined_pnl"] > 0
        
        # These aliases allow the group to be treated as a single trade by the reporting engine
        g["pnl"] = g["combined_pnl"]
        g["exit_price"] = g["best_exit"].get("exit_price", 0) if g["best_exit"] else 0
        g["exit_reason"] = g["best_exit"].get("exit_reason", "UNKNOWN") if g["best_exit"] else "UNKNOWN"

        # Exit-management flags, rolled up from the legs.
        #
        # These were never propagated, so the group — and therefore the saved
        # row, which reads `t.get("be_applied", False)` — always recorded False.
        # Across 691 analysed trades `be_applied` was False on every single one
        # while 106 of them had exit_reason "BE_SL", i.e. break-even demonstrably
        # fired and the flag denied it. Any analysis keyed on the flag silently
        # saw nothing, so what break-even actually costs was unmeasurable.
        #
        # ANY leg reaching break-even makes it true for the group: break-even is
        # a property of the position's management, and the legs share one stop.
        g["be_applied"] = any(bool(st.get("be_applied")) for st in sub_trades)
        g["trail_applied"] = any(bool(st.get("trail_applied")) for st in sub_trades)
        # The trailing method that actually ran, rather than whichever leg
        # happened to sort first.
        g["trail_method"] = next(
            (st.get("trail_method") for st in sub_trades
             if st.get("trail_applied") and st.get("trail_method")),
            (sub_trades[0].get("trail_method") if sub_trades else None),
        )
        # Propagate balance_before/after and net_pnl from sub_trades.
        # NOTE: raw trade dicts from portfolio_engine.py never carry a
        # "balance_before" key at all, so sub_trades[0].get("balance_before")
        # returns None here. Only overwrite the group's balance_before
        # (seeded from t.get("balance_before", 0) during group init above)
        # when a sub-trade actually has a real value — otherwise this line
        # was silently regressing a sane default back to None, which then
        # crashes downstream in compute_portfolio_stats' "> 0" check.
        sorted_subs = sorted(sub_trades, key=lambda x: x.get("exit_time", 0))
        first_balance_before = sub_trades[0].get("balance_before") if sub_trades else None
        if first_balance_before is not None:
            g["balance_before"] = first_balance_before
        g["balance_after"] = sorted_subs[-1].get("balance_after") if sorted_subs else None
        g["net_pnl"]        = sum(t.get("pnl", 0) for t in sub_trades)
        
        # Find the last exit time
        exit_times = [t.get("exit_time", 0) for t in g["sub_trades"] if t.get("exit_time")]
        if exit_times:
            g["exit_time"] = max(exit_times)
            g["exit_time_iso"] = _epoch_to_iso(max(exit_times))
            g["duration_minutes"] = _calc_duration_minutes(g["entry_time"], max(exit_times))
        # Detect exit session
        try:
            g["exit_session"] = detect_session(_to_epoch_seconds(g.get("exit_time", 0)) or 0)
        except Exception:
            g["exit_session"] = "UNKNOWN"

        # ── TP Prices & RR Metrics ──────────────────────────────────────
        # Extract TP prices from sub-trades keyed by tp_level
        entry_p = g.get("entry_price", 0)
        sl_p = g.get("stop_loss", 0)
        is_buy = g.get("direction", "").upper() in ("BUY", "BULLISH")
        risk_dist = abs(entry_p - sl_p) if (entry_p and sl_p) else 0

        for st in sub_trades:
            lvl = st.get("tp_level", 0)
            tp_price = st.get("take_profit", 0)
            if lvl and tp_price and 1 <= lvl <= 5:
                g[f"tp{lvl}_price"] = tp_price

        # Ensure tp1-tp5 keys always exist (null if not set)
        for i in range(1, 6):
            g.setdefault(f"tp{i}_price", None)

        # Compute per-sub-trade RR and find highest TP hit
        highest_tp_hit = 0
        sub_rrs = []
        for st in sub_trades:
            exit_p = st.get("exit_price", 0)
            exit_reason = st.get("exit_reason", "")
            vol = st.get("volume", 0)

            # Calculate realized RR for this sub-trade
            if risk_dist > 0 and exit_p and entry_p:
                if is_buy:
                    sub_rr = (exit_p - entry_p) / risk_dist
                else:
                    sub_rr = (entry_p - exit_p) / risk_dist
                sub_rrs.append((sub_rr, vol))
            else:
                sub_rrs.append((0.0, vol))

            # Track highest TP level that actually triggered
            lvl = st.get("tp_level", 0)
            if lvl and exit_reason and "TP" in exit_reason.upper():
                highest_tp_hit = max(highest_tp_hit, lvl)

        g["tp_level_hit"] = highest_tp_hit if highest_tp_hit > 0 else None

        # Volume-weighted realized RR across all sub-trades
        total_vol = sum(v for _, v in sub_rrs) or 1
        g["realized_rr"] = round(sum(rr * v for rr, v in sub_rrs) / total_vol, 3)
        g["max_rr_achieved"] = round(max((rr for rr, _ in sub_rrs), default=0), 3)

        # Planned RR: based on the highest TP target
        if risk_dist > 0:
            tp_prices = [g[f"tp{i}_price"] for i in range(1, 6) if g.get(f"tp{i}_price")]
            if tp_prices:
                best_tp = max(tp_prices) if is_buy else min(tp_prices)
                if is_buy:
                    g["planned_rr"] = round((best_tp - entry_p) / risk_dist, 3)
                else:
                    g["planned_rr"] = round((entry_p - best_tp) / risk_dist, 3)
            else:
                g["planned_rr"] = None
        else:
            g["planned_rr"] = None

        # PnL in R-multiples: net_pnl divided by risk_amount
        # risk_amount = volume × risk_dist × pip_value (approximate using first sub-trade's volume)
        if risk_dist > 0 and sub_trades:
            first_vol = sub_trades[0].get("volume", 0)
            if first_vol > 0:
                # Approximate: pnl_r = realized_rr (already volume-weighted)
                g["pnl_r"] = g["realized_rr"]
            else:
                g["pnl_r"] = None
        else:
            g["pnl_r"] = None

        # ── Extract Chart Data and SMC Zones ──
        # Resolve the right candle set for THIS group's symbol. For a
        # single-symbol backtest `candles` is already the one DataFrame we
        # need; for a portfolio backtest it's a {symbol: DataFrame} dict.
        group_candles = _resolve_symbol_candles(candles, g.get("symbol"))
        group_candles_m15 = _resolve_symbol_candles(candles_m15, g.get("symbol"))
        group_candles_m5 = _resolve_symbol_candles(candles_m5, g.get("symbol"))

        if group_candles is not None and not group_candles.empty and g.get("entry_time"):
            # Find entry index
            entry_time = g["entry_time"]
            exit_time = g.get("exit_time", entry_time)
            
            try:
                # Find indices for entry and exit times
                def _extract_chart_data(df, e_time, x_time, pad_start, pad_end):
                    if df is None or df.empty: return []
                    e_time_dt = pd.to_datetime(e_time, unit='s', utc=True) if isinstance(e_time, (int, float)) else pd.to_datetime(e_time)
                    x_time_dt = pd.to_datetime(x_time, unit='s', utc=True) if isinstance(x_time, (int, float)) else pd.to_datetime(x_time)
                    
                    if isinstance(df.index, pd.DatetimeIndex):
                        e_time_naive = e_time_dt.tz_localize(None) if getattr(e_time_dt, 'tzinfo', None) else e_time_dt
                        x_time_naive = x_time_dt.tz_localize(None) if getattr(x_time_dt, 'tzinfo', None) else x_time_dt
                        idx_naive = df.index.tz_localize(None) if getattr(df.index, 'tz', None) else df.index
                        
                        s_idx = max(0, idx_naive.searchsorted(e_time_naive, side='right') - 1 - pad_start)
                        e_idx = min(len(df), idx_naive.searchsorted(x_time_naive, side='left') + pad_end)
                    else:
                        s_idx = max(0, df['time'].searchsorted(e_time, side='right') - 1 - pad_start)
                        e_idx = min(len(df), df['time'].searchsorted(x_time, side='left') + pad_end)
                        
                    # CRITICAL FIX: Cap the slice to prevent MemoryError on massive multi-week trades
                    if e_idx - s_idx > 500:
                        e_idx = s_idx + 500
                        
                    slice_d = df.iloc[s_idx:e_idx]
                    c_data = []
                    for idx, row in slice_d.iterrows():
                        t = int(idx.timestamp()) if isinstance(idx, pd.Timestamp) else int(row.get("time", 0))
                        c_data.append({
                            "time": t,
                            "open": float(row.get("open", 0)),
                            "high": float(row.get("high", 0)),
                            "low": float(row.get("low", 0)),
                            "close": float(row.get("close", 0))
                        })
                    return c_data

                g["chart_data"] = _extract_chart_data(group_candles, entry_time, exit_time, 30, 15)
                g["chart_data_m15"] = _extract_chart_data(group_candles_m15, entry_time, exit_time, 20, 5)
                g["chart_data_m5"] = _extract_chart_data(group_candles_m5, entry_time, exit_time, 30, 10)
                
                # ── Chart markings -> smc_data (see strategies/core/markings.py) ──
                # [V1] Until 2026-08-23 this filter only knew "OB"/"FVG"/"STRUCTURE",
                # and no strategy wrote metadata["markings"] at all, so smc_data was
                # empty on every trade ever grouped. Both halves are fixed: the
                # strategies now emit markings, and the routing below covers the full
                # kind vocabulary rather than three of its eight members.
                sig = g.get("original_signal", {})
                markings = sig.get("metadata", {}).get("markings", []) or []

                # A marking's `end_time` is None when the strategy emitted it,
                # because at signal time the trade has not ended yet — and the
                # renderer reads None as "extend to the right edge of the chart".
                # The result was every level from every signal running the full
                # width and crossing over each other's setups.
                #
                # Here the exit IS known, so clamp to it: a level stops being
                # relevant the moment the trade it belongs to closes (TP, SL, BE,
                # trail or hard stop — whichever ended it). A small pad keeps the
                # line visibly reaching the exit marker rather than stopping just
                # short of it.
                _exit_epoch = None
                try:
                    _x = g.get("exit_time") or g.get("entry_time")
                    if _x is not None:
                        _exit_epoch = int(_x) if isinstance(_x, (int, float)) \
                            else int(pd.Timestamp(_x).timestamp())
                except Exception:
                    _exit_epoch = None

                _pad = 0
                if _exit_epoch and g.get("entry_time"):
                    try:
                        _e = g["entry_time"]
                        _entry_epoch = int(_e) if isinstance(_e, (int, float)) \
                            else int(pd.Timestamp(_e).timestamp())
                        # 3% of the trade's own duration, capped — proportional so
                        # it looks right on a 20-minute scalp and a 3-day swing.
                        _pad = min(max(int((_exit_epoch - _entry_epoch) * 0.03), 60), 3600)
                    except Exception:
                        _pad = 0

                boxes, lines, markers = [], [], []
                for m in markings:
                    if not isinstance(m, dict):
                        continue
                    m = dict(m)  # never mutate the signal's own metadata
                    if _exit_epoch and not m.get("end_time"):
                        m["end_time"] = _exit_epoch + _pad
                    elif _exit_epoch and m.get("end_time", 0) > _exit_epoch + _pad:
                        # A strategy that set its own end_time past the exit gets
                        # clamped too — same reasoning.
                        m["end_time"] = _exit_epoch + _pad
                    kind = m.get("type") or m.get("kind")
                    if kind in _BOX_KINDS:
                        boxes.append(m)
                    elif kind in _LINE_KINDS:
                        lines.append(m)
                    elif kind in _POINT_KINDS:
                        markers.append(m)
                g["smc_data"]["boxes"] = boxes
                g["smc_data"]["lines"] = lines
                g["smc_data"]["markers"] = markers
                # Role-keyed label index, so a trade row can answer "which
                # confluences did this entry require" without walking geometry.
                summary = sig.get("metadata", {}).get("confluence_summary")
                if summary:
                    g["smc_data"]["confluence_summary"] = summary
                
            except Exception as e:
                logger.warning(
                    f"[trade_grouper] chart_data extraction failed for group_id={g.get('group_id')} "
                    f"symbol={g.get('symbol')}: {e}",
                    exc_info=True,
                )
        else:
            # chart_data is left at its [] default here — log exactly why, since
            # silently-empty chart_data is otherwise indistinguishable from "this
            # symbol legitimately has no candle data" vs. a resolution bug (e.g.
            # candles_m15/candles_m5 weren't passed to the engine at all, or this
            # group's symbol string doesn't match a key in the {symbol: DataFrame}
            # dict for a portfolio run).
            if group_candles is None:
                logger.warning(
                    f"[trade_grouper] No candles resolved for symbol='{g.get('symbol')}' "
                    f"(group_id={g.get('group_id')}) — chart_data will be empty. For portfolio "
                    f"runs this means the symbol wasn't a key in the candles dict passed in."
                )
            elif group_candles.empty:
                logger.warning(
                    f"[trade_grouper] Candles for symbol='{g.get('symbol')}' resolved but were "
                    f"empty (group_id={g.get('group_id')}) — chart_data will be empty."
                )
            elif not g.get("entry_time"):
                logger.warning(
                    f"[trade_grouper] Group {g.get('group_id')} has no entry_time — chart_data will be empty."
                )

    return list(groups.values())