import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Copy, Trash2 } from 'lucide-react';
import SchemaForm from './SchemaForm';
import { SLOT_RISK_KEYS, SLOT_RISK_SECTIONS, STRATEGY_GROUP, STRATEGY_LABEL, STRATEGY_OPTIONS, slotSummary } from './slotSpec';
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

  // Risk rows, re-based on the account's value so a field shows what this slot
  // will actually use and "changed" means "this slot overrides the default".
  const riskRowsBySection = useMemo(() => {
    const byName = Object.fromEntries(
      (schema || [])
        .filter(r => r.group === 'risk')
        .map(r => [r.key.split('.').slice(1).join('.'), r]),
    );
    return SLOT_RISK_SECTIONS.map(([title, keys]) => [
      title,
      keys.map(k => byName[k]).filter(Boolean).map(r => {
        const name = r.key.split('.').slice(1).join('.');
        const acct = accountRisk?.[name];
        return { ...r, group: 'slot_risk', default: acct === undefined || acct === null ? r.default : acct };
      }),
    ]).filter(([, rows]) => rows.length);
  }, [schema, accountRisk]);

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
