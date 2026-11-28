import { useRef, useState } from 'react';
import { Copy, Download, Upload, Loader2 } from 'lucide-react';
import { getConfigOn, updateConfigOn } from '../services/api';
import { activeServer, activeServerId, isSignedIn, listServers, LIVE } from '../services/servers';
import { mergeSlots, readSlotsFile, slotsFile, slotsPayload } from '../utils/slotTransfer';

/**
 * Copy this server's slots (per symbol, per strategy: parameters and risk) to
 * another server - demo -> live - without setting them up twice; or move them
 * by file. On the target, a pairing it already has takes the copied settings
 * and keeps its on/off switch; a new pairing is added SWITCHED OFF; its other
 * slots are not touched. Nothing starts trading because of a copy.
 *
 * `slots` are the SAVED slots of this server (what is copied is what trades
 * here). `onImported(nextSlots)` receives a merged list for a file import, which
 * the page then shows and saves the usual way.
 */
export default function SlotTransfer({ slots, dirty, onImported }) {
  const here = activeServer();
  const targets = listServers().filter(s => s.id !== activeServerId());
  const [target, setTarget] = useState(targets.find(s => s.role === LIVE)?.id || targets[0]?.id || '');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const fileRef = useRef(null);
  const tgt = targets.find(s => s.id === target);

  const say = (m) => { setMsg(m); setTimeout(() => setMsg(''), 12000); };

  const copy = async () => {
    if (!tgt) return;
    if (!isSignedIn(tgt.id)) {
      say(`Sign in to ${tgt.label} first (server menu, top right), then copy.`);
      return;
    }
    setBusy(true);
    try {
      const remote = (await getConfigOn(tgt.id)).data?.config || {};
      const { next, updated, added } = mergeSlots(remote.instrument_slots || [], slots);
      const live = tgt.role === LIVE;
      const ok = window.confirm(
        `Copy ${slots.length} slot(s) from ${here?.label || 'this server'} to ${tgt.label}`
        + `${live ? ' - a LIVE server trading real money' : ''}?\n\n`
        + `${updated} slot(s) ${tgt.label} already has take these parameters and risk settings`
        + `${live ? ' - if one is switched on, it trades with them from now on' : ''}.\n`
        + `${added} new slot(s) are added there switched OFF; turn them on in its Settings > Strategy.\n`
        + `${tgt.label}'s other slots are not changed.\n\n${tgt.baseUrl}`,
      );
      if (!ok) { setBusy(false); return; }
      await updateConfigOn(tgt.id, { config: slotsPayload(next) });
      say(`Copied to ${tgt.label}: ${updated} updated, ${added} added (off).`);
    } catch (e) {
      say(`Not copied: ${e?.response?.data?.detail || e?.message || e}`);
    }
    setBusy(false);
  };

  const download = () => {
    const blob = new Blob([JSON.stringify(slotsFile(slots, here?.label), null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `algoedge-slots-${(here?.label || 'server').toLowerCase().replace(/\W+/g, '-')}-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importFile = async (file) => {
    try {
      const incoming = readSlotsFile(JSON.parse(await file.text()));
      const { next, updated, added } = mergeSlots(slots, incoming);
      onImported(next);
      say(`Loaded ${incoming.length} slot(s): ${updated} updated, ${added} added (off). Review them, then Save Trading Book.`);
    } catch (e) {
      say(`Not loaded: ${e?.message || e}`);
    }
  };

  return (
    <div style={{
      marginTop: 12, padding: 10, background: 'var(--bg-tertiary)', border: '1px solid var(--border)',
      borderRadius: 'var(--radius-xs)', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', fontSize: '0.78rem',
    }}>
      <span style={{ color: 'var(--text-secondary)' }}>Copy these slots to</span>
      <select className="input" style={{ width: 'auto', minWidth: 140 }} value={target} onChange={e => setTarget(e.target.value)}
        disabled={!targets.length}>
        {!targets.length && <option value="">no other server added</option>}
        {targets.map(s => (
          <option key={s.id} value={s.id}>{s.label}{s.role === LIVE ? ' (live)' : ''}{isSignedIn(s.id) ? '' : ' - not signed in'}</option>
        ))}
      </select>
      <button className="btn btn-sm btn-primary" onClick={copy} disabled={!tgt || busy || !slots.length || dirty}
        title={dirty ? 'Save Trading Book first: the copy sends the saved slots' : 'Parameters and risk per slot; new pairings arrive switched off'}>
        {busy ? <Loader2 size={13} className="spin" /> : <Copy size={13} />} Copy
      </button>
      <span style={{ flex: 1 }} />
      <button className="btn btn-sm btn-ghost" onClick={download} disabled={!slots.length}><Download size={13} /> Download</button>
      <button className="btn btn-sm btn-ghost" onClick={() => fileRef.current?.click()}><Upload size={13} /> Import file</button>
      <input ref={fileRef} type="file" accept="application/json,.json" style={{ display: 'none' }}
        onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) importFile(f); }} />
      {dirty && <span style={{ width: '100%', color: 'var(--text-muted)' }}>Unsaved changes: save the Trading Book before copying it.</span>}
      {msg && (
        <span style={{ width: '100%', color: /^(Copied|Loaded)/.test(msg) ? 'var(--green, #22c55e)' : 'var(--red, #ef4444)' }}>{msg}</span>
      )}
    </div>
  );
}
