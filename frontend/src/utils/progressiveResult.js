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
 * React sees the change) with extra fields:
 *   _trades_loading  still fetching pages
 *   _trades_loaded   groups received so far
 *   _trades_total    groups in the run
 *   _trades_error    set when pages stopped (after retries); the groups
 *                    received so far stay, and `resume` continues from them
 * Updates are coalesced to at most one per `minUpdateMs`.
 *
 * Only a failed SUMMARY rejects. A failed page resolves with `_trades_error`,
 * because the run itself loaded and its headline numbers are worth showing.
 */

export const TRADE_PAGE_SIZE = 100;
const RETRY_DELAYS_MS = [1000, 3000, 6000];

const retriable = (e) => {
  const status = e?.response?.status;
  return !status || status >= 500 || status === 408 || status === 429;
};

async function withRetry(fn, isCancelled) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      if (attempt >= RETRY_DELAYS_MS.length || !retriable(e) || isCancelled()) throw e;
      await new Promise(r => setTimeout(r, RETRY_DELAYS_MS[attempt]));
      if (isCancelled()) throw e;
    }
  }
}

export async function loadResultProgressively({
  fetchSummary,
  fetchPage,
  onUpdate,
  isCancelled = () => false,
  pageSize = TRADE_PAGE_SIZE,
  minUpdateMs = 300,
  describeError = (e) => e?.message || 'request failed',
  resume = null,
}) {
  let summary;
  let groups;
  if (resume) {
    ({ summary } = resume);
    groups = resume.groups || [];
  } else {
    summary = await fetchSummary();
    if (isCancelled() || !summary || !Object.keys(summary).length) return null;
    // An older backend returns the whole run in one payload.
    if (!summary.trades_paged) {
      const whole = { ...summary, _trades_loading: false };
      onUpdate(whole);
      return whole;
    }
    groups = [];
  }

  const total = summary.trade_groups_total || 0;
  const snapshot = (loading, error = null) => ({
    ...summary,
    grouped_trades: groups,
    _trades_loading: loading,
    _trades_loaded: groups.length,
    _trades_total: total,
    _trades_error: error,
  });

  onUpdate(snapshot(groups.length < total));
  if (groups.length >= total) return snapshot(false);

  let lastEmit = Date.now();
  let offset = groups.length;
  while (offset < total) {
    let page;
    try {
      page = await withRetry(() => fetchPage(offset, pageSize), isCancelled);
    } catch (e) {
      if (isCancelled()) return null;
      const stopped = snapshot(false, describeError(e));
      onUpdate(stopped);
      return stopped;
    }
    if (isCancelled()) return null;
    const rows = page?.groups || [];
    if (!rows.length) break;
    groups = groups.concat(rows);
    // The server may cut a page short to keep it small; continue where it stopped.
    offset = Number.isFinite(page?.next_offset) ? page.next_offset : offset + rows.length;
    if (groups.length >= total) break;
    if (Date.now() - lastEmit >= minUpdateMs) {
      lastEmit = Date.now();
      onUpdate(snapshot(true));
    }
  }
  const final = snapshot(false);
  onUpdate(final);
  return final;
}
