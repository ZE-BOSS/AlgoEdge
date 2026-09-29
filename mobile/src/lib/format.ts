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

export const STATE_LABEL: Record<string, string> = {
  claimed_sent: 'Waiting for us to confirm', requested: 'Requested', confirmed: 'Received',
  rejected: 'Not received', exception_pending: 'Under review', approved: 'Approved — payment on its way',
  paid: 'Paid', declined: 'Declined',
};
