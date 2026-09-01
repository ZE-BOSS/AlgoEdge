/**
 * resultCache.js
 *
 * Persistence for the last backtest result.
 *
 * This used to be `localStorage.setItem('algoedge_bt_result', ...)`. That has a
 * ~5 MB quota which the browser bills in UTF-16 — so a 2.5 MB JSON string can
 * exhaust it — and a finished run with ~1,300 grouped trades is far past that
 * even after stripping chart data. The old code coped by shedding fields until
 * something fit, which in practice meant caching the headline numbers and NO
 * trades at all. That made the cache useless for its main job: having the full
 * run on hand.
 *
 * IndexedDB has no meaningful practical limit for this (browsers allow a large
 * fraction of free disk), it stores structured clones rather than strings so
 * there is no stringify pass on the main thread, and it is async so the write
 * never blocks paint. The whole result goes in, unmodified.
 *
 * Everything here fails soft: private windows, blocked site data and browsers
 * without IndexedDB all just behave as "no cache", which is the pre-existing
 * behaviour when the quota was blown anyway.
 */

const DB_NAME = 'algoedge';
const DB_VERSION = 1;
const STORE = 'backtest_result';
const KEY = 'last';

function openDb() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB unavailable'));
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    // Fires when another tab holds an older version open. Treat as unavailable
    // rather than hanging the promise forever.
    req.onblocked = () => reject(new Error('IndexedDB blocked'));
  });
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const store = t.objectStore(STORE);
    const req = fn(store);
    t.oncomplete = () => resolve(req ? req.result : undefined);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

/** Store the complete result. Resolves false if caching is unavailable. */
export async function saveCachedResult(result) {
  try {
    const db = await openDb();
    // Structured clone can't take undefined-valued keys or functions, and a
    // result that came back from the server is plain JSON — but a result built
    // locally may carry undefined values, so round-trip through JSON to be safe.
    await tx(db, 'readwrite', s => s.put(JSON.parse(JSON.stringify(result)), KEY));
    db.close();
    return true;
  } catch {
    return false;
  }
}

/** Read the complete result, or null if there isn't one / caching is off. */
export async function loadCachedResult() {
  try {
    const db = await openDb();
    const value = await tx(db, 'readonly', s => s.get(KEY));
    db.close();
    return value || null;
  } catch {
    return null;
  }
}

/** Drop the cached result (dismiss / save-and-clear). */
export async function clearCachedResult() {
  try {
    const db = await openDb();
    await tx(db, 'readwrite', s => s.delete(KEY));
    db.close();
    return true;
  } catch {
    return false;
  }
}

/**
 * Download the full result as a .json file.
 *
 * The reason this exists: the cache was being used as an export mechanism —
 * "the only way I can show you the trades is if they are cached". Reading a
 * cache out of devtools is a bad way to hand someone a run, and it was silently
 * truncated. This hands over the complete in-memory result, which is strictly
 * more than any cache ever held.
 */
export function downloadResult(result, filename) {
  const name = filename
    || `backtest_${result?.params_snapshot?.symbol || 'run'}_${(result?.backtest_id || '').slice(0, 8)}.json`
       .replace(/\s+/g, '_');
  const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoke on the next tick — revoking synchronously can cancel the download
  // in some browsers before it has started reading the blob.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}
