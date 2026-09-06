/**
 * One place that knows which cached queries a config save invalidates.
 *
 * Saving risk or strategy settings only ever invalidated `['config']`. But the
 * same values are embedded in several other cached responses — `['dashboard']`
 * returns the config alongside stats and positions, `['live-account']` resolves
 * the risk percentage against the live balance, and `['stats']` is computed with
 * the saved sizing basis. Those kept serving their previous 30-second-stale
 * copies, so after changing a setting the UI went on showing numbers derived
 * from the old one, and the only way to see the change was a hard refresh.
 *
 * Anything that writes user config should call this rather than picking keys by
 * hand, so a new dependent query only has to be added in one place.
 */
export const CONFIG_DEPENDENT_KEYS = [
  ['config'],
  ['dashboard'],
  ['live-account'],
  ['stats'],
  ['broker-status'],
  ['telegram-status'],
];

export function invalidateConfigDependents(queryClient) {
  CONFIG_DEPENDENT_KEYS.forEach((queryKey) => queryClient.invalidateQueries({ queryKey }));
}

/**
 * What changes when the connected MT5 account changes, or when its state is
 * reset: everything above, plus the journal, the signal list and the summary —
 * all of which are per-account and were showing the previous account's rows.
 */
export const ACCOUNT_DEPENDENT_KEYS = [
  ...CONFIG_DEPENDENT_KEYS,
  ['trades'],
  ['trades-summary'],
  ['signals'],
  ['positions'],
  ['account-state'],
];

export function invalidateAccountDependents(queryClient) {
  ACCOUNT_DEPENDENT_KEYS.forEach((queryKey) => queryClient.invalidateQueries({ queryKey }));
}
