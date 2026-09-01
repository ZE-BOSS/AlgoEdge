/**
 * summaryEngine.js
 *
 * Pure-function utility module for calculating backtest cumulative statistics.
 * These metrics mirror the backend calculations in backend/analytics/metrics.py
 * and backend/analytics/reports.py — when changing anything here, check the
 * corresponding Python so the two don't drift apart.
 */

/** Coerce anything to a finite number, else `fallback`. */
function num(v, fallback = 0) {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * Milliseconds at which a trade's P&L is REALIZED. Equity moves at exit, not
 * at entry, so every equity-curve / drawdown calculation must order trades by
 * exit_time (this is what reports.py does: `sorted(trades, key=exit_time or
 * entry_time)`). With overlapping positions — which the portfolio engine
 * produces constantly — applying P&L in entry order builds an equity path the
 * account never actually walked, and max drawdown comes out wrong.
 */
function realizedAt(t) {
  const exit = t.exit_time ? new Date(t.exit_time).getTime() : NaN;
  if (Number.isFinite(exit)) return exit;
  const entry = t.entry_time ? new Date(t.entry_time).getTime() : NaN;
  return Number.isFinite(entry) ? entry : 0;
}

/**
 * Combine trades from multiple backtest runs.
 * @param {Array} backtestResults - Array of objects from /backtests/bulk
 * @returns {Array} Flat array of all trades, sorted by entry_time chronologically
 */
export function mergeTrades(backtestResults) {
  let allTrades = [];

  for (const bt of backtestResults) {
    for (const t of bt.trades || []) {
      allTrades.push({
        ...t,
        _source_id: bt.id,
        // Prefer the TRADE's own symbol/strategy over the run's. A portfolio
        // backtest run carries a single summary `symbol`/`strategy_id` while
        // its trades span several instruments, so keying off the run collapsed
        // every symbol in the run into one row of the per-symbol breakdown.
        _source_symbol: t.symbol || bt.symbol,
        _source_strategy: t.strategy_id || bt.strategy_id,
        _initial_balance: bt.initial_balance || 10000
      });
    }
  }

  // Sort by entry time
  allTrades.sort((a, b) => {
    const tA = new Date(a.entry_time || 0).getTime();
    const tB = new Date(b.entry_time || 0).getTime();
    return tA - tB;
  });

  return allTrades;
}

function getIsoWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
  return `${d.getUTCFullYear()}-W${weekNo.toString().padStart(2, '0')}`;
}

/**
 * Group trades into time buckets.
 * @param {Array} trades
 * @param {string} period - 'day', 'week', 'month'
 * @returns {Map<string, Array>} Map of bucketKey -> trades
 */
export function bucketByPeriod(trades, period) {
  const buckets = new Map();

  for (const t of trades) {
    if (!t.entry_time) continue;

    const d = new Date(t.entry_time);
    // An unparseable timestamp yields Invalid Date, whose getFullYear() etc.
    // are all NaN — that produced a real "NaN-NaN" bucket in the table.
    if (Number.isNaN(d.getTime())) continue;

    let key = '';

    if (period === 'day') {
      key = `${d.getFullYear()}-${(d.getMonth()+1).toString().padStart(2, '0')}-${d.getDate().toString().padStart(2, '0')}`;
    } else if (period === 'week') {
      key = getIsoWeek(d);
    } else if (period === 'month') {
      key = `${d.getFullYear()}-${(d.getMonth()+1).toString().padStart(2, '0')}`;
    }

    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(t);
  }

  return buckets;
}

/**
 * Calculate max drawdown given an equity curve (array of balances).
 *
 * The trough is measured against a rolling peak that starts at the curve's
 * FIRST point — so for a period-scoped curve the peak is seeded at that
 * period's opening balance, and any dip below it counts even if the period
 * never made a new high. The percentage is then expressed as a fraction of
 * `initialBalance` (the account's fixed starting capital), NOT of the rolling
 * peak, matching metrics.py::calculate_max_drawdown and the prop-firm rule
 * this codebase's own circuit breaker enforces: 5% of a $25,000 account is
 * breached the instant you're down $1,250, whether the account has since
 * grown to $56,000 or not.
 *
 * `maxDdPctOfPeak` is the companion figure, anchored to the rolling peak
 * instead (metrics.py::calculate_max_drawdown_of_peak). The capital basis is
 * only the right *comparable* when dollar risk per trade is constant — i.e.
 * under `sizing_basis: STATIC`. Once sizing compounds, risk grows with the
 * account and a fixed denominator inflates the number purely for having made
 * money: a run that went $10k -> $44k and gave back $11.2k reports 112% on
 * capital (reads like a blown account) and 25% against peak (what happened).
 * computePeriodStats picks between them from sizingBasis; both are returned
 * either way so no caller loses a figure it already had.
 */
export function maxDrawdown(equityCurve, initialBalance) {
  if (!equityCurve || equityCurve.length === 0) return { maxDdPct: 0, maxDdAbs: 0, maxDdPctOfPeak: 0 };

  const capital = num(initialBalance, 0) > 0 ? num(initialBalance) : num(equityCurve[0]);
  let peak = num(equityCurve[0]);
  let maxDdAbs = 0;
  let maxDdPct = 0;
  let maxDdPctOfPeak = 0;

  for (const raw of equityCurve) {
    const val = num(raw);
    if (val > peak) peak = val;
    const ddAbs = peak - val;                         // absolute $ drawdown from rolling peak
    const ddPct = capital > 0 ? ddAbs / capital : 0;  // % anchored to initial capital
    const ddPctPeak = peak > 0 ? ddAbs / peak : 0;    // % anchored to the rolling peak

    if (ddAbs > maxDdAbs) maxDdAbs = ddAbs;
    if (ddPct > maxDdPct) maxDdPct = ddPct;
    if (ddPctPeak > maxDdPctOfPeak) maxDdPctOfPeak = ddPctPeak;
  }

  if (maxDdAbs < 0) maxDdAbs = 0;
  if (maxDdPct < 0) maxDdPct = 0;
  if (maxDdPctOfPeak < 0) maxDdPctOfPeak = 0;

  return { maxDdPct, maxDdAbs, maxDdPctOfPeak };
}

/**
 * True when `sizing_basis` makes per-trade dollar risk scale with the account.
 * STATIC (and anything unrecognised, matching the backend's fallback in
 * position_sizer.resolve_sizing_base_balance) sizes off fixed capital.
 */
export function isCompoundingBasis(sizingBasis) {
  return sizingBasis === 'BALANCE' || sizingBasis === 'EQUITY';
}

/**
 * Annualized Sharpe ratio.
 *
 * Note that mean/std is scale-invariant, so it doesn't matter WHICH fixed
 * balance the caller divided P&L by to build `returns` — only that the
 * denominator matches how the account was actually sized. Under
 * `sizing_basis: STATIC` that means one fixed balance for every trade;
 * under BALANCE/EQUITY it means each trade's own pre-trade balance, because
 * risk really did scale with the account. Mixing the two is what breaks it
 * (see computePeriodStats).
 */
export function sharpe(returns) {
  if (returns.length < 2) return 0;
  const sum = returns.reduce((a, b) => a + b, 0);
  const mean = sum / returns.length;

  let varianceSq = 0;
  for (const r of returns) {
    varianceSq += Math.pow(r - mean, 2);
  }
  const variance = varianceSq / (returns.length - 1);
  const std = Math.sqrt(variance);

  if (std === 0) return 0;
  const val = (mean / std) * Math.sqrt(252);
  return Number.isFinite(val) ? val : 0;
}

/**
 * Annualized Sortino ratio
 */
export function sortino(returns) {
  if (returns.length < 2) return 0;
  const sum = returns.reduce((a, b) => a + b, 0);
  const mean = sum / returns.length;

  const downside = returns.filter(r => r < 0);
  if (downside.length < 2) return 999.0;

  const downMean = downside.reduce((a, b) => a + b, 0) / downside.length;
  let varianceSq = 0;
  for (const r of downside) {
    varianceSq += Math.pow(r - downMean, 2);
  }
  const downStd = Math.sqrt(varianceSq / (downside.length - 1));

  if (downStd === 0) return 999.0;
  const val = (mean / downStd) * Math.sqrt(252);
  return Number.isFinite(val) ? val : 999.0;
}

function maxConsecutive(boolArray) {
  let maxCount = 0;
  let current = 0;
  for (const val of boolArray) {
    if (val) {
      current++;
      maxCount = Math.max(maxCount, current);
    } else {
      current = 0;
    }
  }
  return maxCount;
}

/**
 * R-multiple for one grouped trade.
 *
 * Prefer the backend's `realized_rr` (or its `pnl_r` alias) when present: it
 * is VOLUME-WEIGHTED across every TP leg of the group (trade_grouper.py). The
 * price-derived fallback below can only use the group-level `exit_price`,
 * which trade_grouper sets to the BEST leg's exit — so for any trade that
 * scaled out at TP1 and then got stopped at break-even on the rest, deriving
 * from prices credits the whole position with the TP1 result and overstates R.
 *
 * The fallback is still needed because `realized_rr` is not persisted for
 * saved backtests (runner.py never populates the column), and it matches
 * reports.py's own price-derived formula exactly. `stop_loss` on a grouped
 * trade is the ENTRY-time stop, not the break-even/trailed one — trade_grouper
 * resolves that before it ever reaches us.
 */
function tradeR(t) {
  const recorded = t.realized_rr != null ? t.realized_rr : t.pnl_r;
  if (recorded != null && Number.isFinite(Number(recorded))) return Number(recorded);

  // [7.8/H3] initial_stop_loss (fill-anchored, Phase 2 §2.7) is the most
  // accurate entry-time risk reference; original_signal.stop_loss (pre-fill
  // theoretical) is the next best; t.stop_loss (possibly BE/trailing-mutated)
  // is the last resort. trade_grouper.py already applies this same chain
  // when building `stop_loss` on the grouped dict, so this is defense-in-depth
  // for any caller that bypasses it, not the primary fix.
  const entry = num(t.entry_price);
  const sl = num(t.initial_stop_loss ?? t.original_signal?.stop_loss ?? t.stop_loss);
  const exit = num(t.exit_price);
  const riskDistance = Math.abs(entry - sl);

  if (riskDistance > 0 && entry > 0) {
    return t.direction === 'BUY' ? (exit - entry) / riskDistance : (entry - exit) / riskDistance;
  }
  return 0;
}

/** Stable identity for a signal group, used to count signals vs. TP legs. */
function groupKey(t, i) {
  if (t.group_id) return t.group_id;
  const sym = t.symbol || t._source_symbol || 'UNKNOWN';
  // entry_time alone collided across symbols in a portfolio backtest (two
  // instruments entering on the same bar counted as one signal), and was
  // `undefined-<time>` entirely for callers that don't pass a symbol.
  return `${sym}|${t._source_strategy || ''}|${t.direction || ''}|${t.entry_time || i}`;
}

/**
 * Compute detailed period stats for a given set of trades.
 * Mirrors backend compute_portfolio_stats and RiskReport logic.
 *
 * @param {Array}  trades
 * @param {number} initialBalance        Opening balance of THIS window (period start).
 * @param {number} accountInitialBalance Account's fixed starting capital; drawdown %
 *                                       is anchored here. Defaults to initialBalance.
 * @param {string} sizingBasis           RiskParams.sizing_basis for the run
 *                                       ('STATIC' | 'BALANCE' | 'EQUITY'). Decides
 *                                       whether the fixed-capital or the
 *                                       compounding normaliser is the correct one
 *                                       for drawdown %, Sharpe/Sortino and Calmar.
 *                                       Defaults to STATIC, matching the backend.
 */
export function computePeriodStats(trades, initialBalance = 10000, accountInitialBalance = null, sizingBasis = 'STATIC') {
  if (!trades || trades.length === 0) {
    return {
      totalTrades: 0, totalGroups: 0, wins: 0, losses: 0, winRate: 0,
      pnl: 0, grossProfit: 0, grossLoss: 0, profitFactor: 0,
      avgWin: 0, avgLoss: 0, bestTrade: 0, worstTrade: 0,
      maxDdPct: 0, maxDdAbs: 0, maxDdPctOfCapital: 0, maxDdPctOfPeak: 0,
      sharpe: 0, sortino: 0,
      expectancyR: 0, avgWinR: 0, avgLossR: 0, avgDurationMin: 0,
      maxConsecWins: 0, maxConsecLosses: 0, calmar: 0, sizingBasis
    };
  }

  const startBalance = num(initialBalance, 10000);
  const compounding = isCompoundingBasis(sizingBasis);

  let wins = 0;
  let losses = 0;
  let pnl = 0;
  let grossProfit = 0;
  let grossLoss = 0;

  let winRs = [];
  let lossRs = [];

  let bestTrade = -Infinity;
  let worstTrade = Infinity;
  let totalDuration = 0;
  let durationCount = 0;

  const uniqueGroups = new Set();

  trades.forEach((t, i) => {
    const tpnl = num(t.pnl);
    pnl += tpnl;

    uniqueGroups.add(groupKey(t, i));

    if (tpnl > 0) {
      wins++;
      grossProfit += tpnl;
    } else {
      losses++;
      grossLoss += Math.abs(tpnl);
    }

    bestTrade = Math.max(bestTrade, tpnl);
    worstTrade = Math.min(worstTrade, tpnl);

    // R buckets follow reports.py: r > 0 is a win, r <= 0 is a loss (so a
    // trade with no usable risk distance contributes a 0R loss, not nothing).
    const rVal = tradeR(t);
    if (rVal > 0) winRs.push(rVal);
    else lossRs.push(rVal);

    if (t.entry_time && t.exit_time) {
      const ms = new Date(t.exit_time) - new Date(t.entry_time);
      if (ms > 0) {
        totalDuration += (ms / 60000);
        durationCount++;
      }
    }
  });

  // ── Equity curve, drawdown and streaks are all EXIT-ordered ──────────
  // The caller hands us entry-ordered trades (that's the order the tables
  // render in), but equity only moves when a position closes. Ordering the
  // curve by entry with overlapping positions produced drawdowns that never
  // happened. reports.py sorts by exit_time for exactly this reason.
  const byExit = [...trades].sort((a, b) => realizedAt(a) - realizedAt(b));

  const equityCurve = [startBalance];
  const returns = [];
  const isWin = [];
  const isLoss = [];
  let currentBal = startBalance;

  for (const t of byExit) {
    const tpnl = num(t.pnl);

    // Returns for Sharpe/Sortino. WHICH denominator is correct depends on
    // sizing_basis, because it decides what a trade's dollar P&L is a return ON.
    //
    // STATIC: dollar risk per trade is constant, so fixed capital is the right
    // normaliser — matching compute_portfolio_stats. Dividing by the trade's own
    // pre-trade balance here scales later trades down purely for having happened
    // after a winning streak, shrinking apparent volatility and inflating
    // Sharpe/Sortino over any profitable run. On the 58 saved runs in debug/ that
    // was enough to flip the SIGN of a run's Sharpe (apa/xauusd_session-filter_off:
    // reported +0.007 on a run whose true Sharpe is -0.069).
    //
    // BALANCE/EQUITY: risk genuinely scales with the account, so a $500 loss on
    // $50k IS the same risk event as $100 on $10k. Holding the denominator at the
    // opening balance makes later trades look progressively more volatile and
    // DEFLATES Sharpe — the same bug in the opposite direction.
    const denom = compounding ? currentBal : startBalance;
    returns.push(denom > 0 ? tpnl / denom : 0);

    currentBal += tpnl;
    equityCurve.push(currentBal);

    isWin.push(tpnl > 0);
    isLoss.push(tpnl <= 0);
  }

  const winRate = wins / trades.length;
  const avgWin = wins > 0 ? grossProfit / wins : 0;
  const avgLoss = losses > 0 ? grossLoss / losses : 0;
  const avgWinR = winRs.length > 0 ? winRs.reduce((a,b)=>a+b,0) / winRs.length : 0;
  const avgLossR = lossRs.length > 0 ? lossRs.reduce((a,b)=>a+b,0) / lossRs.length : 0;

  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : 999.0;
  // Expectancy = P(win)*avgWinR + P(loss)*avgLossR. avgLossR is already
  // negative (every member of lossRs is <= 0), so this ADDS a negative
  // number — same form as reports.py's expectancy_r.
  const expectancyR = (winRate * avgWinR) + ((1 - winRate) * avgLossR);

  const anchorBalance = accountInitialBalance != null && num(accountInitialBalance) > 0
    ? num(accountInitialBalance)
    : startBalance;
  const dd = maxDrawdown(equityCurve, anchorBalance);
  const maxDdAbs = dd.maxDdAbs;
  const maxDdPctOfCapital = dd.maxDdPct;
  const maxDdPctOfPeak = dd.maxDdPctOfPeak;
  // Headline drawdown: capital basis under STATIC (the prop-firm reading, and
  // correct while dollar risk is fixed), peak basis once sizing compounds.
  // Both are returned above so a consumer can show either.
  const maxDdPct = compounding ? maxDdPctOfPeak : maxDdPctOfCapital;
  const sh = sharpe(returns);
  const so = sortino(returns);

  const totalReturnPct = startBalance > 0 ? (currentBal - startBalance) / startBalance : 0;
  // Calmar rides the headline drawdown, or it silently keeps the basis the
  // rest of the card is no longer using.
  const calmar = maxDdPct > 0 ? totalReturnPct / maxDdPct : 999.0;

  return {
    totalTrades: trades.length,
    totalGroups: uniqueGroups.size,
    wins,
    losses,
    winRate,
    pnl,
    grossProfit,
    grossLoss,
    profitFactor,
    avgWin,
    avgLoss,
    bestTrade: bestTrade === -Infinity ? 0 : bestTrade,
    worstTrade: worstTrade === Infinity ? 0 : worstTrade,
    maxDdPct,
    maxDdAbs,
    maxDdPctOfCapital,
    maxDdPctOfPeak,
    sizingBasis,
    sharpe: sh,
    sortino: so,
    expectancyR,
    avgWinR,
    avgLossR,
    calmar,
    maxConsecWins: maxConsecutive(isWin),
    maxConsecLosses: maxConsecutive(isLoss),
    avgDurationMin: durationCount > 0 ? totalDuration / durationCount : 0
  };
}

/**
 * Computes stats for each symbol + strategy combination present in the trades.
 */
export function computePerSymbolStats(trades, sizingBasis = 'STATIC') {
  const symbolMap = new Map();
  for (const t of trades) {
    const key = `${t._source_symbol} (${t._source_strategy})`;
    if (!symbolMap.has(key)) symbolMap.set(key, []);
    symbolMap.get(key).push(t);
  }

  const result = {};
  for (const [key, symTrades] of symbolMap.entries()) {
    result[key] = computePeriodStats(symTrades, symTrades[0]._initial_balance || 10000, null, sizingBasis);
  }
  return result;
}

/**
 * Computes session win rates and stats
 */
export function computeSessionStats(trades) {
  const result = {
    LONDON: { trades: 0, wins: 0, pnl: 0 },
    NY: { trades: 0, wins: 0, pnl: 0 },
    OVERLAP: { trades: 0, wins: 0, pnl: 0 },
    ASIAN: { trades: 0, wins: 0, pnl: 0 },
    UNKNOWN: { trades: 0, wins: 0, pnl: 0 }
  };

  for (const t of trades) {
    let s = t.session || 'UNKNOWN';
    if (s === 'LONDON/NY') s = 'OVERLAP';
    if (!result[s]) s = 'UNKNOWN';

    result[s].trades++;
    result[s].pnl += num(t.pnl);
    if (num(t.pnl) > 0) result[s].wins++;
  }

  // Calculate rates
  for (const k of Object.keys(result)) {
    result[k].winRate = result[k].trades > 0 ? result[k].wins / result[k].trades : 0;
  }

  return result;
}

/**
 * Build a merged cumulative equity curve.
 *
 * Exit-ordered and anchored at the starting capital, so the plotted line
 * begins at the account's opening balance and every step is a realized close.
 */
export function buildEquityCurve(trades, initialBalance = 10000) {
  const start = num(initialBalance, 10000);
  const ordered = [...trades].sort((a, b) => realizedAt(a) - realizedAt(b));

  const curve = [{
    index: 0,
    date: ordered.length ? (ordered[0].entry_time || ordered[0].exit_time) : null,
    symbol: 'Start',
    equity: start
  }];

  let balance = start;
  for (let i = 0; i < ordered.length; i++) {
    const t = ordered[i];
    balance += num(t.pnl);
    curve.push({
      index: i + 1,
      // The point exists at the moment the trade CLOSED, so label it with the
      // exit timestamp (falling back to entry for records missing an exit).
      date: t.exit_time || t.entry_time,
      symbol: t._source_symbol,
      equity: balance
    });
  }

  return curve;
}

/**
 * Process a grouped map of buckets into the final matrix for the table.
 */
export function computePeriodSymbolMatrix(bucketsMap, initialBalance = 10000, sizingBasis = 'STATIC') {
  // Sort keys chronologically
  const keys = Array.from(bucketsMap.keys()).sort((a, b) => a.localeCompare(b));

  const start = num(initialBalance, 10000);
  const matrix = [];
  let cumulativePnl = 0;

  for (const period of keys) {
    const trades = bucketsMap.get(period);
    // Period-scoped stats: the equity curve opens at this period's starting
    // balance, while the drawdown % stays anchored to the account's fixed
    // capital (prop-firm basis) via the third argument.
    //
    // This used to pass a `fromStartOnly` flag that measured every trough
    // against the period's OPENING balance only, ignoring intra-period highs
    // — a month that ran +$5,000 and then gave back $4,000 reported 0%
    // drawdown. It also disagreed with Backtester.jsx's period table, which
    // called the same function WITHOUT the flag. Seeding the rolling peak at
    // the period's opening balance (what maxDrawdown already does) is both
    // the correct prop-firm reading and the consistent one.
    const periodStats = computePeriodStats(trades, start + cumulativePnl, start, sizingBasis);

    // Sub-group by symbol inside this period
    const symbolBreakdown = {};
    const symMap = new Map();
    for (const t of trades) {
      const sk = `${t._source_symbol} (${t._source_strategy})`;
      if (!symMap.has(sk)) symMap.set(sk, []);
      symMap.get(sk).push(t);
    }

    for (const [sk, symTrades] of symMap.entries()) {
      let w = 0, l = 0, symPnl = 0, gp = 0, gl = 0;
      for (const t of symTrades) {
        // num() guards a null/absent pnl — Math.abs(undefined) is NaN, which
        // used to poison this row's Avg Loss and render as "$NaN".
        const p = num(t.pnl);
        symPnl += p;
        if (p > 0) { w++; gp += p; }
        else { l++; gl += Math.abs(p); }
      }

      symbolBreakdown[sk] = {
        trades: symTrades.length,
        wins: w,
        losses: l,
        winRate: symTrades.length > 0 ? w / symTrades.length : 0,
        pnl: symPnl,
        avgWin: w > 0 ? gp / w : 0,
        avgLoss: l > 0 ? gl / l : 0
      };
    }

    cumulativePnl += periodStats.pnl;

    matrix.push({
      period,
      totalTrades: periodStats.totalTrades,
      wins: periodStats.wins,
      losses: periodStats.losses,
      winRate: periodStats.winRate,
      pnl: periodStats.pnl,
      maxDdPct: periodStats.maxDdPct,
      sharpe: periodStats.sharpe,
      symbols: symbolBreakdown,
      cumulativePnl: cumulativePnl,
      endBalance: start + cumulativePnl
    });
  }

  // Return descending order (newest first)
  return matrix.reverse();
}
