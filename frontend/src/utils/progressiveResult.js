/**
 * progressiveResult.js
 *
 * Load a finished backtest in pieces: the summary (headline numbers, equity
 * curve, diagnostics) first, then the trade groups a page at a time.
 *
 * One response carrying every trade group was 3-14 MB for a portfolio run: slow
 * over a remote link, parsed in one go on the main thread, and one timeout lost
 * all of it. Paging keeps each request small, shows the results as soon as the
 * summary lands, and fills the trade list in behind it.
 *
 * `onUpdate` receives a complete result object every time (a new object, so
 * React sees the change) with three extra fields:
 *   _trades_loading  still fetching pages
 *   _trades_loaded   groups received so far
 *   _trades_total    groups in the run
 * Updates are coalesced to at most one per `minUpdateMs`, so a 3,000-group run
 * re-renders the results a handful of times, not once per page.
 */

export const TRADE_PAGE_SIZE = 250;

export async function loadResultProgressively({
  fetchSummary,
  fetchPage,
  onUpdate,
  isCancelled = () => false,
  pageSize = TRADE_PAGE_SIZE,
  minUpdateMs = 300,
}) {
  const summary = await fetchSummary();
  if (isCancelled() || !summary || !Object.keys(summary).length) return null;

  // An older backend returns the whole run in one payload.
  if (!summary.trades_paged) {
    const whole = { ...summary, _trades_loading: false };
    onUpdate(whole);
    return whole;
  }

  const total = summary.trade_groups_total || 0;
  const snapshot = (groups, loading) => ({
    ...summary,
    grouped_trades: groups,
    _trades_loading: loading,
    _trades_loaded: groups.length,
    _trades_total: total,
  });

  let groups = [];
  onUpdate(snapshot(groups, total > 0));
  if (total === 0) return snapshot(groups, false);

  let lastEmit = Date.now();
  for (let offset = 0; offset < total; offset += pageSize) {
    const page = await fetchPage(offset, pageSize);
    if (isCancelled()) return null;
    const rows = page?.groups || [];
    groups = groups.concat(rows);
    const done = rows.length === 0 || groups.length >= total;
    if (done) break;
    if (Date.now() - lastEmit >= minUpdateMs) {
      lastEmit = Date.now();
      onUpdate(snapshot(groups, true));
    }
  }
  const final = snapshot(groups, false);
  onUpdate(final);
  return final;
}
