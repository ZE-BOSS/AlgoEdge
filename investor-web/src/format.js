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
