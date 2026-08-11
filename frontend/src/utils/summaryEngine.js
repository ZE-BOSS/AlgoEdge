/**
 * summaryEngine.js
 * 
 * Pure-function utility module for calculating backtest cumulative statistics.
 * These metrics perfectly mirror the backend calculations in metrics.py and reports.py.
 */

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
        _source_symbol: bt.symbol,
        _source_strategy: bt.strategy_id,
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
 * Both the absolute ($) and percentage (%) drawdown are anchored from
 * `initialBalance` (capital) — matching backend metrics.py and the user's
 * explicit rule: "drawdown is calculated from capital."
 * The rolling-peak is still used to find the worst trough relative to any
 * previous high, but the percentage is always expressed as a fraction of
 * the starting capital, not of the peak itself.
 */
export function maxDrawdown(equityCurve, initialBalance) {
  if (!equityCurve || equityCurve.length === 0) return { maxDdPct: 0, maxDdAbs: 0 };

  const capital = initialBalance !== undefined ? initialBalance : equityCurve[0];
  let peak = equityCurve[0];
  let maxDdAbs = 0;
  let maxDdPct = 0;

  for (const val of equityCurve) {
    if (val > peak) peak = val;
    const ddAbs = peak - val;          // absolute $ drawdown from rolling peak
    const ddPct = capital > 0 ? ddAbs / capital : 0;  // % anchored to initial capital

    if (ddAbs > maxDdAbs) maxDdAbs = ddAbs;
    if (ddPct > maxDdPct) maxDdPct = ddPct;
  }

  return { maxDdPct, maxDdAbs };
}

/**
 * Annualized Sharpe ratio
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
  return (mean / std) * Math.sqrt(252);
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
  return (mean / downStd) * Math.sqrt(252);
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
 * Compute detailed period stats for a given set of trades.
 * Mirrors backend compute_portfolio_stats and RiskReport logic.
 */
export function computePeriodStats(trades, initialBalance = 10000) {
  if (!trades || trades.length === 0) {
    return {
      totalTrades: 0, wins: 0, losses: 0, winRate: 0,
      pnl: 0, grossProfit: 0, grossLoss: 0, profitFactor: 0,
      avgWin: 0, avgLoss: 0, bestTrade: 0, worstTrade: 0,
      maxDdPct: 0, maxDdAbs: 0, sharpe: 0, sortino: 0,
      expectancyR: 0, avgDurationMin: 0,
      maxConsecWins: 0, maxConsecLosses: 0, calmar: 0
    };
  }

  let wins = 0;
  let losses = 0;
  let pnl = 0;
  let grossProfit = 0;
  let grossLoss = 0;
  
  let pnlRs = [];
  let winRs = [];
  let lossRs = [];
  
  const returns = [];
  const equityCurve = [initialBalance];
  let currentBal = initialBalance;
  
  let bestTrade = -Infinity;
  let worstTrade = Infinity;
  let totalDuration = 0;
  let durationCount = 0;
  
  const isWin = [];
  const isLoss = [];

  // Assuming trades are already sorted by entry_time chronologically
  for (const t of trades) {
    const tpnl = t.pnl || 0;
    pnl += tpnl;
    
    // Track wins/losses
    if (tpnl > 0) {
      wins++;
      grossProfit += tpnl;
      isWin.push(true);
      isLoss.push(false);
    } else {
      losses++;
      grossLoss += Math.abs(tpnl);
      isWin.push(false);
      isLoss.push(true);
    }
    
    bestTrade = Math.max(bestTrade, tpnl);
    worstTrade = Math.min(worstTrade, tpnl);
    
    // Balance for this trade
    const balBefore = t.balance_before || currentBal;
    returns.push(balBefore > 0 ? tpnl / balBefore : 0);
    
    // Equity
    currentBal += tpnl;
    equityCurve.push(currentBal);
    
    // R calculations
    const entry = t.entry_price || 0;
    const sl = t.stop_loss || 0;
    const exit = t.exit_price || 0;
    const riskDistance = Math.abs(entry - sl);
    
    let rVal = 0;
    if (riskDistance > 0 && entry > 0) {
      rVal = t.direction === 'BUY' ? (exit - entry) / riskDistance : (entry - exit) / riskDistance;
    }
    pnlRs.push(rVal);
    if (rVal > 0) winRs.push(rVal);
    else lossRs.push(rVal);
    
    // Duration
    if (t.entry_time && t.exit_time) {
      const ms = new Date(t.exit_time) - new Date(t.entry_time);
      if (ms > 0) {
        totalDuration += (ms / 60000);
        durationCount++;
      }
    }
  }

  const winRate = wins / trades.length;
  const avgWin = wins > 0 ? grossProfit / wins : 0;
  const avgLoss = losses > 0 ? grossLoss / losses : 0;
  const avgWinR = winRs.length > 0 ? winRs.reduce((a,b)=>a+b,0) / winRs.length : 0;
  const avgLossR = lossRs.length > 0 ? lossRs.reduce((a,b)=>a+b,0) / lossRs.length : 0;
  
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : 999.0;
  const expectancyR = (winRate * avgWinR) - ((1 - winRate) * Math.abs(avgLossR));
  
  const { maxDdPct, maxDdAbs } = maxDrawdown(equityCurve, initialBalance);
  const sh = sharpe(returns);
  const so = sortino(returns);
  
  const totalReturnPct = initialBalance > 0 ? (currentBal - initialBalance) / initialBalance : 0;
  const calmar = maxDdPct > 0 ? totalReturnPct / maxDdPct : 999.0;

  return {
    totalTrades: trades.length,
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
    sharpe: sh,
    sortino: so,
    expectancyR,
    calmar,
    maxConsecWins: maxConsecutive(isWin),
    maxConsecLosses: maxConsecutive(isLoss),
    avgDurationMin: durationCount > 0 ? totalDuration / durationCount : 0
  };
}

/**
 * Computes stats for each symbol + strategy combination present in the trades.
 */
export function computePerSymbolStats(trades) {
  const symbolMap = new Map();
  for (const t of trades) {
    const key = `${t._source_symbol} (${t._source_strategy})`;
    if (!symbolMap.has(key)) symbolMap.set(key, []);
    symbolMap.get(key).push(t);
  }
  
  const result = {};
  for (const [key, symTrades] of symbolMap.entries()) {
    result[key] = computePeriodStats(symTrades, symTrades[0]._initial_balance || 10000);
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
    result[s].pnl += (t.pnl || 0);
    if ((t.pnl || 0) > 0) result[s].wins++;
  }
  
  // Calculate rates
  for (const k of Object.keys(result)) {
    result[k].winRate = result[k].trades > 0 ? result[k].wins / result[k].trades : 0;
  }
  
  return result;
}

/**
 * Build a merged cumulative equity curve.
 */
export function buildEquityCurve(trades, initialBalance = 10000) {
  const curve = [];
  let balance = initialBalance;
  
  for (let i = 0; i < trades.length; i++) {
    const t = trades[i];
    balance += (t.pnl || 0);
    curve.push({
      index: i,
      date: t.entry_time,
      symbol: t._source_symbol,
      equity: balance
    });
  }
  
  return curve;
}

/**
 * Process a grouped map of buckets into the final matrix for the table.
 */
export function computePeriodSymbolMatrix(bucketsMap, initialBalance = 10000) {
  // Sort keys chronologically
  const keys = Array.from(bucketsMap.keys()).sort((a, b) => a.localeCompare(b));
  
  const matrix = [];
  let cumulativePnl = 0;
  
  for (const period of keys) {
    const trades = bucketsMap.get(period);
    const periodStats = computePeriodStats(trades, initialBalance + cumulativePnl);
    
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
        symPnl += (t.pnl || 0);
        if ((t.pnl || 0) > 0) { w++; gp += t.pnl; }
        else { l++; gl += Math.abs(t.pnl); }
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
      endBalance: initialBalance + cumulativePnl
    });
  }
  
  // Return descending order (newest first)
  return matrix.reverse();
}
