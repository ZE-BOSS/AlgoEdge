import { useMutation, useQueryClient } from '@tanstack/react-query';

// Money and units arrive from the API as decimal STRINGS. They are formatted as
// strings too — never parsed to a float — so the screen shows exactly the figure
// the ledger holds.
function group(intPart) {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

export function fmtMoney(v, { sign = false } = {}) {
  if (v === null || v === undefined || v === '') return '—';
  const s = String(v);
  const neg = s.startsWith('-');
  const [i, f = ''] = (neg ? s.slice(1) : s).split('.');
  const body = `$${group(i)}.${(f + '00').slice(0, 2)}`;
  if (neg) return `-${body}`;
  return sign && s !== '0' && s !== '0.00' ? `+${body}` : body;
}

export function fmtUnits(v, dp = 4) {
  if (v === null || v === undefined || v === '') return '—';
  const s = String(v);
  const neg = s.startsWith('-');
  const [i, f = ''] = (neg ? s.slice(1) : s).split('.');
  return `${neg ? '-' : ''}${group(i)}${dp ? '.' + (f + '0'.repeat(dp)).slice(0, dp) : ''}`;
}

/** The fund's change since launch, read off the unit price: it opens at 100, so
 *  104.20 means $100 invested at launch is now $104.20 (+4.20%). Display only. */
export function fmtSinceLaunch(navPerUnit) {
  if (navPerUnit === null || navPerUnit === undefined || navPerUnit === '') return '—';
  const pct = Number(navPerUnit) - 100;
  const s = Math.abs(pct).toFixed(2);
  return pct > 0.004 ? `+${s}%` : pct < -0.004 ? `-${s}%` : '0.00%';
}

/** A unit price in dollars, to 4dp: the precision units are bought and sold at. */
export function fmtPrice(v) {
  if (v === null || v === undefined || v === '') return '—';
  return `$${fmtUnits(v, 4)}`;
}

export function fmtPct(v) {
  if (v === null || v === undefined || v === '') return '—';
  return `${v}%`;
}

export function fmtDate(v) {
  if (!v) return '—';
  return String(v).replace('T', ' ').slice(0, 16);
}

export function isNeg(v) {
  return typeof v === 'string' && v.startsWith('-');
}

export function isZero(v) {
  return v === null || v === undefined || /^-?0*(\.0*)?$/.test(String(v));
}

export function errorText(e) {
  const d = e?.response?.data?.detail;
  if (Array.isArray(d)) return d.map(x => `${(x.loc || []).slice(-1)[0] || ''} ${x.msg}`.trim()).join('; ');
  return d || e?.message || 'Request failed';
}

/** A mutation that refreshes every investor query on success, so queues,
 *  badges and statements never show a state the server has moved past. */
export function useInvAction(fn, { onSuccess } = {}) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (res, vars) => {
      qc.invalidateQueries({ queryKey: ['inv'] });
      onSuccess?.(res, vars);
    },
  });
}
