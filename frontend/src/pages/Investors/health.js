/**
 * Three states, not two, because "nothing has checked this" and "something is
 * wrong" are different things and must not look the same.
 *
 * The reconciliation used to report one boolean. Once the broker's own books
 * became part of the comparison, a fund with no MT5 account attached started
 * reporting `healthy: false` — truthfully, since nothing outside the ledger
 * had confirmed a figure, but indistinguishable on screen from money actually
 * going missing. A permanent red banner is a banner people stop reading, which
 * is exactly how the $2,269 funding difference stayed unnoticed for a week.
 *
 *   verified   the arithmetic agrees AND it was checked against a broker
 *   unchecked  the arithmetic agrees, but nothing independent confirmed it
 *   broken     a line does not balance, or an investor's cache has drifted
 *
 * `lines_agree` is the internal check; `healthy` additionally requires the
 * broker side. See backend/investor/reconcile.py::Report.
 */

export const VERIFIED = 'verified';
export const UNCHECKED = 'unchecked';
export const BROKEN = 'broken';

export function healthState(r) {
  if (!r) return UNCHECKED;
  // `lines_agree` is absent on an older payload; fall back to `healthy` so a
  // stale cached response degrades to the previous behaviour rather than
  // claiming everything is fine.
  const agree = r.lines_agree !== undefined ? r.lines_agree : r.healthy;
  if (!agree) return BROKEN;
  return r.healthy ? VERIFIED : UNCHECKED;
}

export const HEALTH_STYLE = {
  [VERIFIED]: { colour: 'var(--green)', border: 'var(--green-dim)' },
  [UNCHECKED]: { colour: 'var(--warn, #d29922)', border: 'var(--warn, #d29922)' },
  [BROKEN]: { colour: 'var(--red)', border: 'var(--red)' },
};

export const HEALTH_TITLE = {
  [VERIFIED]: 'Everything agrees, and the broker confirms it',
  [UNCHECKED]: 'The books agree with themselves — nothing has confirmed them',
  [BROKEN]: 'Something does not add up',
};

export const HEALTH_SHORT = {
  [VERIFIED]: 'Books reconcile',
  [UNCHECKED]: 'Not yet checked against a broker',
  [BROKEN]: 'Reconciliation needs attention',
};

/** The first thing to do about it, or '' when there is nothing to do. */
export function healthAdvice(r) {
  const state = healthState(r);
  if (state === VERIFIED) return '';
  const missing = (r && r.broker_missing) || [];
  if (missing.length) return missing[0];
  if (state === UNCHECKED) {
    return 'mark an MT5 account as holding investor capital in the Journal';
  }
  return '';
}
