"""Load every debug/*/*.json backtest run into a leg-level and group-level frame.

True R is recomputed against ENTRY-TIME risk (|entry - initial_stop_loss|), never
against the mutated stop. pip_size is derived empirically per symbol from the
recorded extreme price vs mfe_pips, so no MT5 connection is needed.
"""
import json, glob, os, math
from collections import defaultdict
import pandas as pd

ROOT = "/home/user/AlgoEdge"

def load():
    legs, groups, runs = [], [], []
    for path in sorted(glob.glob(os.path.join(ROOT, "debug/*/*.json"))):
        d = json.load(open(path))
        if not isinstance(d, dict) or "trades" not in d:
            continue
        snap = d.get("params_snapshot") or {}
        run = {
            "run": os.path.relpath(path, ROOT),
            "strategy": snap.get("strategy_id") or "?",
            "symbol": snap.get("symbol") or "?",
            "start": snap.get("start_date"), "end": snap.get("end_date"),
            "candles": snap.get("candle_count"),
            "bal0": d.get("initial_balance"), "bal1": d.get("final_balance"),
            "n_signals": d.get("total_signals"), "n_invalid": d.get("invalid_signals"),
        }
        runs.append(run)
        for t in d.get("trades") or []:
            t = dict(t); t["run"] = run["run"]; legs.append(t)
        for g in d.get("grouped_trades") or []:
            g = dict(g); g["run"] = run["run"]; g["strategy"] = g.get("strategy_id") or run["strategy"]
            groups.append(g)
    return pd.DataFrame(legs), pd.DataFrame(groups), pd.DataFrame(runs)


def derive_pip_sizes(legs: pd.DataFrame) -> dict:
    """pip_size = best-price excursion in price / mfe_pips, taken as the per-symbol median."""
    ratios = defaultdict(list)
    for r in legs.itertuples():
        mfe = getattr(r, "mfe_pips", None)
        if not mfe or mfe <= 0:
            continue
        ep = r.entry_price
        if r.direction == "BUY":
            ext = getattr(r, "highest_price", None)
            dist = (ext - ep) if ext is not None and not pd.isna(ext) else None
        else:
            ext = getattr(r, "lowest_price", None)
            dist = (ep - ext) if ext is not None and not pd.isna(ext) else None
        if dist is None or dist <= 0:
            continue
        ratios[r.symbol].append(dist / mfe)
    out = {}
    for sym, vals in ratios.items():
        vals = sorted(vals)
        med = vals[len(vals) // 2]
        # snap to the nearest sane pip size
        cands = [0.00001, 0.0001, 0.001, 0.01, 0.1, 1.0, 10.0]
        out[sym] = min(cands, key=lambda c: abs(math.log10(c) - math.log10(med)))
    return out


def enrich(legs: pd.DataFrame, pips: dict) -> pd.DataFrame:
    L = legs.copy()
    L["pip"] = L["symbol"].map(pips)
    init_sl = L["initial_stop_loss"].fillna(L["stop_loss"])
    L["risk_price"] = (L["entry_price"] - init_sl).abs()
    L = L[L["risk_price"] > 0].copy()
    sgn = L["direction"].map({"BUY": 1.0, "SELL": -1.0})
    L["realised_R"] = (L["exit_price"] - L["entry_price"]) * sgn / L["risk_price"]
    L["mfe_R"] = L["mfe_pips"] * L["pip"] / L["risk_price"]
    L["mae_R"] = L["mae_pips"] * L["pip"] / L["risk_price"]
    L["entry_dt"] = pd.to_datetime(L["entry_time_iso"], format="mixed", utc=True)
    L["hour"] = L["entry_dt"].dt.hour
    L["dow"] = L["entry_dt"].dt.dayofweek
    L["month"] = L["entry_dt"].dt.to_period("M").astype(str)
    return L


def group_view(L: pd.DataFrame, G: pd.DataFrame) -> pd.DataFrame:
    """Volume-weighted true R per trade group, plus the group's excursion envelope."""
    agg = L.groupby("group_id").apply(lambda g: pd.Series({
        "strategy": g["strategy_id"].iloc[0],
        "symbol": g["symbol"].iloc[0],
        "direction": g["direction"].iloc[0],
        "run": g["run"].iloc[0],
        "entry_dt": g["entry_dt"].min(),
        "hour": g["hour"].iloc[0],
        "month": g["month"].iloc[0],
        "n_legs": len(g),
        "volume": g["volume"].sum(),
        "pnl": g["pnl"].sum(),
        "true_R": (g["realised_R"] * g["volume"]).sum() / g["volume"].sum(),
        "mfe_R": g["mfe_R"].max(),
        "mae_R": g["mae_R"].max(),
        "risk_price": g["risk_price"].iloc[0],
        "exit_reasons": ",".join(sorted(set(g["exit_reason"].dropna()))),
        "first_exit": g.sort_values("exit_time")["exit_reason"].iloc[0],
        "confluence": g["confluence_score"].iloc[0],
        "session": g["entry_session"].iloc[0],
    }), include_groups=False).reset_index()
    return agg


def build():
    legs, groups, runs = load()
    pips = derive_pip_sizes(legs)
    L = enrich(legs, pips)
    Gv = group_view(L, groups)
    return L, Gv, runs, pips

if __name__ == "__main__":
    L, G, R, pips = build()
    print("pip sizes:", pips)
    print("legs", len(L), "groups", len(G), "runs", len(R))
