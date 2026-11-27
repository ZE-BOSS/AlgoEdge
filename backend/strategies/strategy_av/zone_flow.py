"""
backend/strategies/strategy_av/zone_flow.py

Order-flow zone reversal (AlphaVantiq research, 27 Nov 2026).

Zones
  origin  the base candle before an impulse (close >= impulse_atr ATR beyond the
          next bar's open within 3 bars, every candle of it in the impulse's
          direction): demand below an up-move, supply above a down-move. Its
          strength = volume of the base and the first impulse bar vs the 50-bar
          average; its big trades = their big-trade volume vs the 50-bar average.
  hvn     the previous UTC day's high-volume nodes (peaks of the volume-by-price
          profile, at least half the top peak), +/- 0.25 ATR; support when price
          comes from above, resistance from below.
  A zone lives zone_max_age_days and dies when a bar closes 0.1 ATR through
  its far side. It is not traded on the bar that made it.

Trade (one at a time)
  a bar that trades into a live zone coming from the right side (the previous
  bar entirely on that side) -> the preset's candle, flow, context and session
  tests -> entry at the next bar, stop beyond the zone or the bar's extreme plus
  stop_buffer_atr (at least min_risk_atr), target an R multiple or the next
  opposing zone; closed after max_hold_hours.

Order flow comes from the candle frame's columns (Databento, marketdata/source.py).
On MT5-only bars there is none and a preset that needs it does not trade.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.registry import register_strategy
from backend.strategies.strategy_av import params as P
from backend.strategies.strategy_av.common import AVReplayBase, candle_ok, double_at, flow_ok, plan, session_ok


HVN_SLOTS = 6


def hvn_by_day(bars) -> dict[int, list[float]]:
    """Fallback without a minute profile: each UTC day's volume nodes from bar VWAP x
    volume, keyed (YYYYMMDD) by the NEXT calendar day, the day they are traded on."""
    out: dict[int, list[float]] = {}
    vw, vol = bars.ind["vwap"], bars.ind["of_vol"]
    vw = np.where(np.isfinite(vw), vw, (bars.h + bars.l + bars.c) / 3.0)
    for d in np.unique(bars.utc_day):
        m = (bars.utc_day == d) & (vol > 0)
        if m.sum() < 12:
            continue
        px, v = vw[m], vol[m]
        step = max(float(np.median(px)) * 0.0004, 1e-9)
        prof = pd.Series(v).groupby((pd.Series(px) / step).round() * step).sum().sort_index()
        sm = prof.rolling(5, center=True, min_periods=1).mean().to_numpy()
        idx = prof.index.to_numpy()
        peaks = [float(idx[i]) for i in range(1, len(sm) - 1)
                 if sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] >= 0.5 * sm.max()]
        nxt = pd.Timestamp(str(int(d))) + pd.Timedelta(days=1)
        out[int(nxt.strftime("%Y%m%d"))] = peaks[:HVN_SLOTS]
    return out


def hvn_arrays(bars) -> dict[str, np.ndarray]:
    """The nodes as per-bar columns hvn0..hvn5 (NaN = none), so window slices carry them.
    From the minute profile when the frame brought it (Databento: pd_hvn0..5, the
    research's rule); otherwise from the bars' own VWAP x volume."""
    if "pd_hvn0" in bars.ind:
        return {f"hvn{i}": np.asarray(bars.ind[f"pd_hvn{i}"], dtype=float) for i in range(HVN_SLOTS)}
    per_day = hvn_by_day(bars)
    cols = {f"hvn{i}": np.full(len(bars), np.nan) for i in range(HVN_SLOTS)}
    for d, peaks in per_day.items():
        m = bars.utc_day == d
        for i, px in enumerate(peaks):
            cols[f"hvn{i}"][m] = px
    return cols


class AVZoneFlow(AVReplayBase):
    PARAMS_ATTR = ""
    PARAMS_CLS = P.AVZoneFlowParams
    TEMPLATE_MINUTES = 1

    @property
    def ANCHOR_DAYS(self) -> int:  # noqa: N802
        return int(np.ceil(self.params.zone_max_age_days)) + 1

    @property
    def WINDOW(self) -> int:  # noqa: N802
        per_day = {"M1": 1440, "M5": 288, "M15": 96, "M30": 48, "H1": 24}.get(self.params.timeframe, 288)
        return int(per_day * (self.ANCHOR_DAYS + 3))

    def prepare(self, bars) -> None:
        super().prepare(bars)
        bars.ind.update(hvn_arrays(bars))

    def init_state(self) -> dict:
        return {"zones": [], "day": 0}

    def step(self, st, k, b, ctx) -> None:
        p = self.params
        self._time_stop(k, b, ctx)
        a = b.ind["atr"][k]
        if not np.isfinite(a) or a <= 0:
            return
        o, h, l, c, t = b.o, b.h, b.l, b.c, b.t  # noqa: E741
        vol, vavg = b.ind["of_vol"], b.ind["vol_avg"]
        bb, bs, bavg = b.ind["big_buy"], b.ind["big_sell"], b.ind["big_avg"]
        zones = st["zones"]
        # -- an impulse that finished on this bar makes an origin zone -----------
        for n in (1, 2, 3):
            j = k - n
            if j < 1:
                break
            up = c[k] - o[j + 1]
            if up >= p.impulse_atr * a and all(c[x] >= o[x] for x in range(j + 1, k + 1)):
                lo, hi = min(l[j], l[j + 1]), max(o[j], c[j])
                if hi - lo <= p.max_zone_width_atr * a:
                    zones.append(self._zone("origin", 1, lo, hi, t[k], vol, vavg, bb, bs, bavg, j, k))
                break
            dn = o[j + 1] - c[k]
            if dn >= p.impulse_atr * a and all(c[x] <= o[x] for x in range(j + 1, k + 1)):
                lo, hi = min(o[j], c[j]), max(h[j], h[j + 1])
                if hi - lo <= p.max_zone_width_atr * a:
                    zones.append(self._zone("origin", -1, lo, hi, t[k], vol, vavg, bb, bs, bavg, j, k))
                break
        # -- the previous day's volume nodes, at the day's first bar ---------------
        day = int(b.utc_day[k])
        if day != st["day"]:
            st["day"] = day
            for px in (b.ind[f"hvn{i}"][k] for i in range(HVN_SLOTS)):
                if not np.isfinite(px):
                    continue
                zones.append({"kind": "hvn", "side": 0, "lo": px - 0.25 * a, "hi": px + 0.25 * a, "born": int(t[k]),
                              "touches": 0, "strength": np.nan, "bigs": np.nan})
        # -- expire and invalidate ------------------------------------------------
        max_age = p.zone_max_age_days * 86400
        keep = []
        for z in zones:
            if t[k] - z["born"] > max_age:
                continue
            if z["side"] == 1 and c[k] < z["lo"] - 0.1 * a:
                continue
            if z["side"] == -1 and c[k] > z["hi"] + 0.1 * a:
                continue
            keep.append(z)
        st["zones"] = zones = keep[-80:]
        if k < 1:
            return
        # -- candidates on this bar -------------------------------------------------
        chosen = None
        for z in zones:
            if z["born"] >= t[k - 1]:
                continue
            if not (l[k] <= z["hi"] and h[k] >= z["lo"]):
                continue
            prev_above, prev_below = l[k - 1] > z["hi"], h[k - 1] < z["lo"]
            if z["side"] == 1 or (z["side"] == 0 and prev_above):
                side = 1
                if not prev_above:
                    continue
            elif z["side"] == -1 or (z["side"] == 0 and prev_below):
                side = -1
                if not prev_below:
                    continue
            else:
                continue
            z["touches"] += 1
            if chosen is not None or ctx.position() or ctx.pending is not None:
                continue
            if self._passes(z, side, k, b, a):
                chosen = (z, side)
        if chosen is None:
            return
        z, side = chosen
        entry = float(c[k])
        stop = (min(l[k], z["lo"]) - p.stop_buffer_atr * a) if side == 1 else (max(h[k], z["hi"]) + p.stop_buffer_atr * a)
        opp = [(zz["lo"] if side == 1 else zz["hi"]) for zz in zones if zz is not z and
               ((side == 1 and zz["side"] in (-1, 0) and zz["lo"] > entry)
                or (side == -1 and zz["side"] in (1, 0) and zz["hi"] < entry))]
        pl = plan(side, entry, stop, a, p, opp)
        if pl is None:
            return
        sl, tp = pl
        meta = {"zone": z["kind"], "zone_lo": round(float(z["lo"]), 6), "zone_hi": round(float(z["hi"]), 6),
                "touch": int(z["touches"]), "rl_rr": round(float((tp - entry) / (entry - sl)), 4)}     # the app's TP = this R (risk/multi_tp.py)
        (ctx.buy if side == 1 else ctx.sell)(sl=sl, tp=tp, **meta)

    @staticmethod
    def _zone(kind, side, lo, hi, born, vol, vavg, bb, bs, bavg, j, k) -> dict:
        strength = (vol[j] + vol[j + 1]) / (2 * vavg[k]) if np.isfinite(vavg[k]) and vavg[k] else np.nan
        bigs = (bb[j] + bs[j] + bb[j + 1] + bs[j + 1]) / (2 * bavg[k]) if np.isfinite(bavg[k]) and bavg[k] else np.nan
        return {"kind": kind, "side": side, "lo": float(lo), "hi": float(hi), "born": int(born), "touches": 0,
                "strength": strength, "bigs": bigs}

    def _passes(self, z, side, k, b, a) -> bool:
        p = self.params
        o, h, l, c = b.o, b.h, b.l, b.c  # noqa: E741
        if not self.gate("session", session_ok(p.session, int(b.utc_hhmm[k]))):
            return False
        dbl = double_at(side, h, l, k, a) if p.candle in ("double", "rejection_double") else False
        if not self.gate("candle", candle_ok(p.candle, side, o, h, l, c, k, dbl)):
            return False
        if not self.gate("order_flow", flow_ok(p.flow, side, b.ind, k, o, h, l, c)):
            return False
        return self.gate("context", self._context(p.context, z, side, k, b, a))

    @staticmethod
    def _context(kind, z, side, k, b, a) -> bool:
        if kind == "none":
            return True
        if kind == "fresh_zone":
            return z["touches"] <= 1
        if kind == "strong_zone":
            return bool(np.isfinite(z["strength"]) and z["strength"] >= 1.5)
        if kind == "big_trades_in_zone":
            return bool(np.isfinite(z["bigs"]) and z["bigs"] >= 1.5)
        if kind == "origin_zone":
            return z["kind"] == "origin"
        if kind == "hvn_zone":
            return z["kind"] == "hvn"
        if kind == "big_move_in":
            lo = max(0, k - 12)
            ext = (b.h[lo:k].max() - b.l[k]) if side == 1 else (b.h[k] - b.l[lo:k].min())
            return bool(ext / a >= 3.0)
        return False


def _preset(sid: str, attr: str, cls):
    @register_strategy(sid)
    class _S(AVZoneFlow):
        strategy_id = sid
        PARAMS_ATTR = attr
        PARAMS_CLS = cls
    _S.__name__ = _S.__qualname__ = sid.replace("_v1", "")
    return _S


AVGoldenEngulfFlip = _preset("AV_GoldenEngulfFlip_v1", "av_golden_engulf_flip", P.AVGoldenEngulfFlipParams)
AVGoldenTwinAbsorb = _preset("AV_GoldenTwinAbsorb_v1", "av_golden_twin_absorb", P.AVGoldenTwinAbsorbParams)
AVNasdaqRejectionFlip = _preset("AV_NasdaqRejectionFlip_v1", "av_nasdaq_rejection_flip", P.AVNasdaqRejectionFlipParams)
AVSPFreshDoji = _preset("AV_SPFreshDoji_v1", "av_sp_fresh_doji", P.AVSPFreshDojiParams)
AVSPTwinRejection = _preset("AV_SPTwinRejection_v1", "av_sp_twin_rejection", P.AVSPTwinRejectionParams)
AVSilverAbsorbFlip = _preset("AV_SilverAbsorbFlip_v1", "av_silver_absorb_flip", P.AVSilverAbsorbFlipParams)
AVDowBigTradePin = _preset("AV_DowBigTradePin_v1", "av_dow_big_trade_pin", P.AVDowBigTradePinParams)
AVDowAbsorptionZone = _preset("AV_DowAbsorptionZone_v1", "av_dow_absorption_zone", P.AVDowAbsorptionZoneParams)

ZONE_PRESETS = {
    "AV_GoldenEngulfFlip_v1": ("av_golden_engulf_flip", P.AVGoldenEngulfFlipParams, "Golden Engulf Flip (gold, order flow)"),
    "AV_GoldenTwinAbsorb_v1": ("av_golden_twin_absorb", P.AVGoldenTwinAbsorbParams, "Golden Twin Absorb (gold, order flow)"),
    "AV_NasdaqRejectionFlip_v1": ("av_nasdaq_rejection_flip", P.AVNasdaqRejectionFlipParams, "Nasdaq Rejection Flip (order flow)"),
    "AV_SPFreshDoji_v1": ("av_sp_fresh_doji", P.AVSPFreshDojiParams, "S&P Fresh Doji (order flow)"),
    "AV_SPTwinRejection_v1": ("av_sp_twin_rejection", P.AVSPTwinRejectionParams, "S&P Twin Rejection (order flow)"),
    "AV_SilverAbsorbFlip_v1": ("av_silver_absorb_flip", P.AVSilverAbsorbFlipParams, "Silver Absorb Flip (order flow)"),
    "AV_DowBigTradePin_v1": ("av_dow_big_trade_pin", P.AVDowBigTradePinParams, "Dow Big-Trade Pin (US30, order flow)"),
    "AV_DowAbsorptionZone_v1": ("av_dow_absorption_zone", P.AVDowAbsorptionZoneParams, "Dow Absorption Zone (US30, order flow)"),
}
