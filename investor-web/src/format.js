// Money and units arrive as decimal STRINGS and are formatted as strings —
// never parsed to floats — so the screen shows exactly the figure on the ledger.

const group = (i) => i.replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function split(v) {
  const s = String(v);
  const neg = s.startsWith('-');
  const [i, f = ''] = (neg ? s.slice(1) : s).split('.');
  return { neg, i: i || '0', f };
}

export function money(v, { sign = false } = {}) {
  if (v === null || v === undefined || v === '') return '—';
  const { neg, i, f } = split(v);
  const body = `$${group(i)}.${(f + '00').slice(0, 2)}`;
  const zero = /^0*$/.test(i + f);
  if (neg && !zero) return `−${body}`;
  return sign && !zero ? `+${body}` : body;
}

export function units(v, dp = 4) {
  if (v === null || v === undefined || v === '') return '—';
  const { neg, i, f } = split(v);
  return `${neg ? '−' : ''}${group(i)}.${(f + '0'.repeat(dp)).slice(0, dp)}`;
}

/** The fund's change since launch, from its unit price: it opens at 100, so a
 *  price of 104.20 means $100 invested at launch is worth $104.20 (+4.20%).
 *  Display only — nothing is computed from this string. */
export function sinceLaunch(navPerUnit) {
  if (navPerUnit === null || navPerUnit === undefined || navPerUnit === '') return '—';
  const pct = Number(navPerUnit) - 100;
  const s = Math.abs(pct).toFixed(2);
  return pct > 0.004 ? `+${s}%` : pct < -0.004 ? `−${s}%` : '0.00%';
}

export const isNeg = (v) => typeof v === 'string' && v.startsWith('-') && !/^-0*(\.0*)?$/.test(v);

export function day(v) {
  if (!v) return '—';
  const d = new Date(String(v).length === 10 ? `${v}T12:00:00Z` : `${v}Z`);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** A decimal string a user typed, cleaned: "1,500" -> "1500". null if not a number. */
export function cleanAmount(text) {
  const t = String(text || '').replace(/[,\s$]/g, '');
  return /^\d+(\.\d{1,2})?$/.test(t) && Number(t) > 0 ? t : null;
}

export const STATE_LABEL = {
  claimed_sent: 'Waiting for us to confirm', requested: 'Requested', confirmed: 'Received',
  rejected: 'Not received', exception_pending: 'Under review', approved: 'Approved — payment on its way',
  paid: 'Paid', declined: 'Declined',
};

/** A short dollar figure for tiles and chart labels: $12.5k, $1.2m, $950.
 *  Display only. */
export function compact(v) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  const a = Math.abs(n);
  const sign = n < 0 ? '−' : '';
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(a >= 1e7 ? 0 : 1)}m`;
  if (a >= 1e4) return `${sign}$${(a / 1e3).toFixed(0)}k`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}k`;
  return `${sign}$${a.toFixed(a < 10 ? 2 : 0)}`;
}

/** "+4.20%" / "−1.05%" from a decimal string. */
export function pct(v, { sign = true } = {}) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  const body = `${Math.abs(n).toFixed(2)}%`;
  if (n < 0) return `−${body}`;
  return sign && n > 0 ? `+${body}` : body;
}

export function monthName(key, { short = false } = {}) {
  const [y, m] = String(key).split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, 15)).toLocaleDateString(undefined,
    { month: short ? 'short' : 'long', year: short ? '2-digit' : 'numeric', timeZone: 'UTC' });
}

export function greeting(name) {
  const h = new Date().getHours();
  const part = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  const first = String(name || '').split(' ')[0];
  return first ? `${part}, ${first}` : part;
}
