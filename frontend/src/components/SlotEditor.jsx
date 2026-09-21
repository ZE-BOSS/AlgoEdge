import { useMemo, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Copy, Trash2 } from 'lucide-react';
import SchemaForm from './SchemaForm';
import { ACCOUNT_ONLY_KEYS, SLOT_RISK_KEYS, SLOT_RISK_SECTIONS, STRATEGY_GROUP, STRATEGY_LABEL, STRATEGY_OPTIONS, TP_LEVEL_OF, slotSummary } from './slotSpec';
import SymbolPicker from './SymbolPicker';

/**
 * One SLOT = one symbol + one strategy, with ITS OWN strategy parameters and ITS
 * OWN risk. The same editor is used everywhere a slot is configured — Settings
 * (what the live bot trades), the single backtest, and each portfolio row — so
 * "the settings" means one thing in all three.
 *
 * Why it exists: the strategy blocks used to be global. ORB on gold and ORB on
 * GBPJPY shared one set of parameters, so a range or timeframe that suits gold
 * could not be set without moving GBPJPY too, and every screen stacked all nine
 * strategies' parameter cards whether or not they were being used.
 *
 * Fields are generated from the backend schema (`/config/parameter_schema`,
 * built from the dataclasses), so a parameter added to a strategy appears here
 * with its real default and its own docstring — no hand-mirrored form to drift.
 */

export default function SlotEditor({
  slot,
  schema,
  accountRisk = {},
  strategyDefaults = {},
  // The strategy's MEASURED exits, and whether the account is using them.
  // resolve_slot_risk_config lays these over the account default for any field
  // this slot has not set itself, so they are what the run will use.
  measuredExits = {},
  symbols = [],
  onChange,
  onRemove,
  onDuplicate,
  showEnabled = true,
  showSymbol = true,
  defaultTab = 'strategy',
  collapsible = false,
  defaultOpen = true,
}) {
  const [tab, setTab] = useState(defaultTab);
  // A book of slots stays readable when each is one line until you open it.
  const [open, setOpen] = useState(defaultOpen);
  const isOpen = collapsible ? open : true;
  const group = STRATEGY_GROUP[slot.strategy_id] || 'apa';
  const set = (patch) => onChange({ ...slot, ...patch });

  // Strategy rows for THIS strategy only, re-based on the saved account value
  // for that parameter so the field shows what this slot would actually run
  // with, and "changed" means "this slot differs from the account's value".
  const strategyRows = useMemo(
    () => (schema || []).filter(r => r.group === group).map(r => {
      const name = r.key.split('.').slice(1).join('.');
      const saved = strategyDefaults?.[name];
      return saved === undefined || saved === null ? r : { ...r, default: saved };
    }),
    [schema, group, strategyDefaults],
  );

  // Risk rows, re-based on what this slot would actually run with, so
  // "changed" means "this slot overrides it". The order matches the backend
  // resolver: account default, then the strategy's measured exits, then the
  // slot's own value (which SchemaForm shows on top of these).
  const riskRowsBySection = useMemo(() => {
    const tpCount = Number(
      slot.risk?.tp_count ?? measuredExits?.tp_count ?? accountRisk?.tp_count ?? 3,
    ) || 3;
    const byName = Object.fromEntries(
      (schema || [])
        .filter(r => r.group === 'risk')
        .map(r => [r.key.split('.').slice(1).join('.'), r]),
    );
    const sections = SLOT_RISK_SECTIONS.map(([title, keys]) => [
      title,
      keys.map(k => byName[k]).filter(Boolean).filter(r => {
        // a slot taking N targets is not asked about TP N+1
        const level = TP_LEVEL_OF(r.key.split('.').slice(1).join('.'));
        return level === null || level <= tpCount;
      }).map(r => {
        const name = r.key.split('.').slice(1).join('.');
        const acct = accountRisk?.[name];
        const base = acct === undefined || acct === null ? r.default : acct;
        const measured = measuredExits?.[name];
        return measured === undefined || measured === null
          ? { ...r, group: 'slot_risk', default: base }
          : { ...r, group: 'slot_risk', default: measured, measuredBy: slot.strategy_id };
      }),
    ]);

    // Everything else the engine reads off a slot's risk config. Rendered last,
    // so a field added to RiskParams is reachable the day it ships instead of
    // waiting for someone to remember this file.
    const named = new Set(SLOT_RISK_KEYS);
    const rest = Object.entries(byName)
      .filter(([name]) => !named.has(name) && !ACCOUNT_ONLY_KEYS.includes(name))
      .map(([name, r]) => {
        const acct = accountRisk?.[name];
        const base = acct === undefined || acct === null ? r.default : acct;
        return { ...r, group: 'slot_risk', default: base };
      });
    sections.push(['Advanced', rest]);
    return sections.filter(([, rows]) => rows.length);
  }, [schema, accountRisk, measuredExits, slot.strategy_id, slot.risk]);

  // Named so the risk tab can say which fields the strategy decided.
  const measuredNames = useMemo(
    () => riskRowsBySection.flatMap(([, rows]) => rows)
      .filter(r => r.measuredBy)
      .map(r => r.label),
    [riskRowsBySection],
  );

  // What this slot would actually run with: its own value, else the account's
  // (risk) or the strategy's (parameters), else the schema default.
  const effective = (rows, values, name) => {
    const own = values?.[name];
    if (own !== undefined && own !== null) return own;
    const row = rows.find(r => r.key.split('.').slice(1).join('.') === name);
    return row ? row.default : undefined;
  };

  // Settings that make signals impossible, not merely rare. Both are strategy
  // guardrails checked BEFORE a signal is formed, so the run reports "no signals
  // at all" and the reason is invisible until you read the gate breakdown.
  const blockers = useMemo(() => {
    const out = [];
    const flatRisk = riskRowsBySection.flatMap(([, rows]) => rows);
    const riskPct = Number(effective(flatRisk, slot.risk, 'risk_per_trade_pct'));
    const dailyCap = effective(strategyRows, slot.strategy_params, 'max_daily_risk_pct');
    if (dailyCap !== undefined && Number.isFinite(riskPct) && riskPct > Number(dailyCap)) {
      out.push(`Risk per trade (${riskPct}%) is above this strategy's own daily risk cap `
        + `(${dailyCap}%), so it will refuse every bar before forming a signal. `
        + `Raise "Max daily risk pct" on the Strategy tab, or lower risk per trade.`);
    }
    const tradeCap = effective(strategyRows, slot.strategy_params, 'max_trades_per_day');
    if (tradeCap !== undefined && Number(tradeCap) <= 0) {
      out.push('This strategy\'s "Max trades per day" is 0, so it will never enter.');
    }
    return out;
  }, [riskRowsBySection, strategyRows, slot.risk, slot.strategy_params]);

  const tabs = [
    ['strategy', `Strategy${strategyRows.length ? ` (${strategyRows.length})` : ''}`],
    ['risk', `Risk & exits${Object.keys(slot.risk || {}).filter(k => SLOT_RISK_KEYS.includes(k)).length
      ? ` (${Object.keys(slot.risk || {}).filter(k => SLOT_RISK_KEYS.includes(k)).length})` : ''}`],
  ];

  return (
    <div className="slot-editor">
      <div className="slot-editor__head">
        {showEnabled && (
          <label className="slot-editor__enable" title={slot.enabled === false ? 'Disabled' : 'Enabled — the bot scans this slot'}>
            <input type="checkbox" checked={slot.enabled !== false} onChange={e => set({ enabled: e.target.checked })} />
          </label>
        )}
        {showSymbol && (
          <SymbolPicker
            className="slot-editor__symbol"
            value={slot.symbol || ''}
            onChange={val => set({ symbol: val })}
            options={symbols || []}
            placeholder="Symbol"
          />
        )}
        <select className="input input-sm slot-editor__strategy" value={slot.strategy_id} onChange={e => set({ strategy_id: e.target.value })}>
          {STRATEGY_OPTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <span className="slot-editor__summary">{slotSummary(slot, accountRisk)}</span>
        {blockers.length > 0 && (
          <span className="slot-editor__blocked" title={blockers.join(' ')}>
            <AlertTriangle size={11} /> cannot trade
          </span>
        )}
        <div className="slot-editor__actions">
          {collapsible && (
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setOpen(o => !o)}
              title={isOpen ? 'Hide these settings' : 'Edit this slot: strategy parameters and risk'}
            >
              {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>
          )}
          {onDuplicate && (
            <button className="btn btn-secondary btn-sm" onClick={onDuplicate} title="Another strategy on this symbol">
              <Copy size={13} />
            </button>
          )}
          {onRemove && (
            <button className="btn btn-secondary btn-sm" onClick={onRemove} title="Remove this slot">
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>

      {isOpen && (<div className="slot-editor__tabs">
        {tabs.map(([id, label]) => (
          <button key={id} className={`slot-editor__tab${tab === id ? ' is-active' : ''}`} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>)}

      {isOpen && (<div className="slot-editor__body">
        {blockers.map((msg, i) => (
          <p key={i} className="slot-editor__warn"><AlertTriangle size={12} /> {msg}</p>
        ))}
        {tab === 'strategy' ? (
          <>
            <label className="slot-editor__measured" title="On: this slot runs the settings measured for this symbol. Off: the parameters below apply exactly as entered.">
              <input
                type="checkbox"
                checked={slot.use_measured_params !== false}
                onChange={e => set({ use_measured_params: e.target.checked })}
              />
              Use the measured settings for {slot.symbol || 'this symbol'}
            </label>
            <p className="slot-editor__note">
              These parameters belong to {slot.symbol || 'this symbol'} on {STRATEGY_LABEL[slot.strategy_id] || slot.strategy_id} only.
              The same strategy on another symbol keeps its own.
            </p>
            <SchemaForm
              schema={strategyRows}
              group={group}
              values={slot.strategy_params || {}}
              onChange={(next, replace) => set({ strategy_params: replace ? next : next })}
              showFilter={strategyRows.length > 10}
              minWidth={190}
            />
          </>
        ) : (
          <>
            <p className="slot-editor__note">
              This slot trades on its own risk engine: these limits count its trades and nothing
              else&apos;s. A field left at the default follows the account default shown under it.
            </p>
            {measuredNames.length > 0 && (
              <p className="slot-editor__note slot-editor__note--measured">
                {STRATEGY_LABEL[slot.strategy_id] || slot.strategy_id} has measured exits, and the
                account is set to use them, so they replace the account default for:{' '}
                <strong>{measuredNames.join(', ')}</strong>. The values shown are what this slot
                will run. Type your own into any of them to override it, or turn the measured
                exits off in <em>Settings &gt; Defaults</em>.
              </p>
            )}
            {riskRowsBySection.map(([title, rows]) => (
              <div key={title} className="slot-editor__section">
                <SchemaForm
                  schema={rows}
                  group="slot_risk"
                  title={title}
                  values={slot.risk || {}}
                  onChange={(next, replace) => set({ risk: replace ? next : next })}
                  showFilter={false}
                  minWidth={175}
                />
              </div>
            ))}
          </>
        )}
      </div>)}
    </div>
  );
}
