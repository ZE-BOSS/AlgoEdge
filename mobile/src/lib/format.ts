// Money and units arrive from the API as decimal STRINGS and are formatted as
// strings — never parsed to floats — so the screen shows the ledger's figure.

const group = (i: string) => i.replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function split(v: string | number) {
  const s = String(v);
  const neg = s.startsWith('-');
  const [i, f = ''] = (neg ? s.slice(1) : s).split('.');
  return { neg, i: i || '0', f };
}

export function money(v: string | null | undefined, opts: { sign?: boolean } = {}): string {
  if (v === null || v === undefined || v === '') return '—';
  const { neg, i, f } = split(v);
  const body = `$${group(i)}.${(f + '00').slice(0, 2)}`;
  const zero = /^0*$/.test(i + f);
  if (neg && !zero) return `−${body}`;
  return opts.sign && !zero ? `+${body}` : body;
}

export function units(v: string | null | undefined, dp = 4): string {
  if (v === null || v === undefined || v === '') return '—';
  const { neg, i, f } = split(v);
  return `${neg ? '−' : ''}${group(i)}.${(f + '0'.repeat(dp)).slice(0, dp)}`;
}

/** The fund's change since launch, from its unit price (it opens at 100, so
 *  104.20 means $100 at launch is worth $104.20: +4.20%). Display only. */
export function sinceLaunch(navPerUnit?: string | null): string {
  if (navPerUnit === null || navPerUnit === undefined || navPerUnit === '') return '—';
  const pct = Number(navPerUnit) - 100;
  const s = Math.abs(pct).toFixed(2);
  return pct > 0.004 ? `+${s}%` : pct < -0.004 ? `−${s}%` : '0.00%';
}

export const isNeg = (v?: string | null) => !!v && v.startsWith('-') && !/^-0*(\.0*)?$/.test(v);

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-09-29" or an ISO timestamp -> "29 Sep 2026". Parsed by hand: Hermes'
 *  Intl date support varies between Android versions. */
export function day(v?: string | null): string {
  if (!v) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
  if (!m) return v;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

export function monthLabel(year: number, month: number): string {
  return `${['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
    'October', 'November', 'December'][month - 1]} ${year}`;
}

/** A decimal string a person typed, cleaned: "1,500" -> "1500"; null if not a number. */
export function cleanAmount(text: string): string | null {
  const t = String(text || '').replace(/[,\s$]/g, '');
  return /^\d+(\.\d{1,2})?$/.test(t) && Number(t) > 0 ? t : null;
}

/** A short dollar figure for tiles and chart labels: $12.5k, $1.2m. Display only. */
export function compact(v: string | number | null | undefined): string {
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
export function pct(v: string | number | null | undefined, opts: { sign?: boolean } = { sign: true }): string {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  const body = `${Math.abs(n).toFixed(2)}%`;
  if (n < 0) return `−${body}`;
  return opts.sign !== false && n > 0 ? `+${body}` : body;
}

/** "2026-07" -> "Jul 26" (short) or "July 2026". */
export function monthName(key: string, short = false): string {
  const [y, m] = key.split('-').map(Number);
  return short ? `${MONTHS[m - 1]} ${String(y).slice(2)}` : monthLabel(y, m);
}

export function greeting(name?: string | null): string {
  const h = new Date().getHours();
  const part = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  const first = String(name || '').split(' ')[0];
  return first ? `${part}, ${first}` : part;
}

export const STATE_LABEL: Record<string, string> = {
  claimed_sent: 'Waiting for us to confirm', requested: 'Requested', confirmed: 'Received',
  rejected: 'Not received', exception_pending: 'Under review', approved: 'Approved — payment on its way',
  paid: 'Paid', declined: 'Declined',
};
