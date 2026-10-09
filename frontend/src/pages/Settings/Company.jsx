import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Building2, Check, History } from 'lucide-react';
import { inv } from '../../services/api';

/**
 * The legal identity: entity name, address, jurisdiction, contacts.
 *
 * WHY THIS SCREEN EXISTS
 * Three outward-facing surfaces have to state who we are in law — commercial
 * email needs a physical postal address, the privacy policy needs to name the
 * controller, the terms need the governing jurisdiction — and none of them had
 * anywhere to get it from. Hard-coding would put one legal fact in three files
 * and let them drift, so it is a row, and this is where it is edited.
 *
 * IT IS BLANK ON PURPOSE, FOR NOW. There is no registered address yet. The
 * system behaves correctly while empty: email footers omit what they do not
 * have, no half-written legal notice is ever rendered, and the backend REFUSES
 * to send commercial email until the name and address are set. The banner below
 * says so rather than leaving it to be discovered.
 *
 * A save writes a NEW version — a policy is a statement made to a person on a
 * date, so the old wording has to stay answerable.
 */

const SECTIONS = [
  ['Identity', [
    ['legal_entity_name', 'Registered legal name', 'As it appears on the certificate of incorporation'],
    ['trading_name', 'Trading name', 'If you trade under a different name'],
    ['registration_number', 'Registration number'],
    ['tax_number', 'Tax number'],
    ['jurisdiction', 'Jurisdiction', 'The laws the Terms are governed by, e.g. "Nigeria"'],
  ]],
  ['Registered address', [
    ['address_line1', 'Address line 1'],
    ['address_line2', 'Address line 2'],
    ['city', 'City'],
    ['region', 'State or region'],
    ['postcode', 'Postcode'],
    ['country', 'Country'],
  ]],
  ['Contacts', [
    ['support_email', 'Support email'],
    ['privacy_email', 'Privacy email', 'Where data requests go'],
    ['dpo_contact', 'Data-protection contact', 'Required in some jurisdictions'],
    ['phone', 'Phone'],
  ]],
  ['Policy version', [
    ['policy_version', 'Version label', 'Shown on the privacy policy and terms, e.g. "2026-10"'],
    ['policy_changelog', 'What changed', 'Kept with this version so a past policy can be explained'],
  ]],
];

// Commercial email is blocked without these two. Named here so the banner can
// say which field is missing rather than "something is missing".
const REQUIRED_FOR_EMAIL = ['legal_entity_name', 'address_line1'];

// Field name -> the label the form shows, so the banner names what the person
// is looking at rather than the column it is stored in.
const LABELS = Object.fromEntries(
  SECTIONS.flatMap(([, rows]) => rows.map(([name, label]) => [name, label])),
);

export default function CompanySettings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['company'],
    queryFn: () => inv.company().then((r) => r.data),
  });

  const [form, setForm] = useState({});
  const [saved, setSaved] = useState(false);

  // Seed once from the server and then leave the form alone, so a refetch
  // cannot overwrite what is being typed.
  useEffect(() => {
    if (data?.current) {
      setForm(Object.fromEntries(
        Object.entries(data.current).map(([k, v]) => [k, v ?? '']),
      ));
    }
  }, [data?.current?.version]);

  const dirty = useMemo(() => {
    if (!data?.current) return {};
    return Object.fromEntries(
      Object.entries(form).filter(([k, v]) => (data.current[k] ?? '') !== v
        && SECTIONS.some(([, rows]) => rows.some(([name]) => name === k))),
    );
  }, [form, data]);

  const save = useMutation({
    mutationFn: (changes) => inv.saveCompany(changes),
    onSuccess: () => {
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      qc.invalidateQueries({ queryKey: ['company'] });
    },
  });

  if (isLoading) return <div className="card">Loading…</div>;

  const current = data?.current || {};
  const blocked = data?.blocks_commercial_email;
  const missing = REQUIRED_FOR_EMAIL.filter((f) => !(form[f] || '').trim());

  return (
    <>
      <div className="card" style={{ marginBottom: 16 }}>
        <h3><Building2 size={16} style={{ display: 'inline', marginRight: 6 }} />
          Who we are, in law</h3>
        <p className="muted" style={{ fontSize: '0.82rem', lineHeight: 1.6 }}>
          Email footers, the privacy policy and the Terms all render from this.
          Saving writes a new version and keeps the old one, so a policy as it
          stood on a past date stays answerable.
        </p>

        {blocked && (
          <div style={{
            marginTop: 12, padding: '10px 12px', borderRadius: 6,
            background: 'rgba(210,153,34,0.1)', border: '1px solid rgba(210,153,34,0.35)',
            fontSize: '0.8rem', lineHeight: 1.6,
          }}>
            <AlertTriangle size={14} style={{ display: 'inline', marginRight: 6, verticalAlign: -2 }} />
            <strong>Commercial email is currently blocked.</strong> A promotional
            email must carry a physical postal address, and the penalty is charged
            per message — so the backend refuses to send one until the registered
            name and address are set. Statements, deposits, withdrawals and every
            other account email are unaffected and still send.
            {missing.length > 0 && (
              <div style={{ marginTop: 6 }}>
                Still needed:{' '}
                {missing.map((f, i) => (
                  <span key={f}>
                    {i > 0 && ', '}
                    <code>{LABELS[f] || f}</code>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {current.version > 0 && (
          <p className="muted" style={{ fontSize: '0.75rem', marginTop: 10 }}>
            <History size={12} style={{ display: 'inline', marginRight: 4, verticalAlign: -1 }} />
            Version {current.version}
            {current.policy_updated_at
              ? ` · saved ${new Date(current.policy_updated_at).toLocaleString()}` : ''}
            {current.postal_address ? ` · ${current.postal_address}` : ''}
          </p>
        )}
      </div>

      {SECTIONS.map(([title, rows]) => (
        <div className="card" key={title} style={{ marginBottom: 16 }}>
          <h4 style={{ marginTop: 0 }}>{title}</h4>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 14 }}>
            {rows.map(([name, label, help]) => (
              <label key={name} style={{ display: 'block' }}>
                <div style={{ fontSize: '0.78rem', marginBottom: 4 }}>
                  {label}
                  {REQUIRED_FOR_EMAIL.includes(name) && (
                    <span style={{ color: 'var(--text-muted)' }}> · needed for commercial email</span>
                  )}
                </div>
                {name === 'policy_changelog' ? (
                  <textarea
                    rows={3} style={{ width: '100%' }}
                    value={form[name] ?? ''}
                    onChange={(e) => setForm({ ...form, [name]: e.target.value })}
                  />
                ) : (
                  <input
                    type="text" style={{ width: '100%' }}
                    value={form[name] ?? ''}
                    onChange={(e) => setForm({ ...form, [name]: e.target.value })}
                  />
                )}
                {help && (
                  <div className="muted" style={{ fontSize: '0.72rem', marginTop: 3 }}>{help}</div>
                )}
              </label>
            ))}
          </div>
        </div>
      ))}

      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button
          className="btn btn-primary"
          disabled={Object.keys(dirty).length === 0 || save.isPending}
          onClick={() => save.mutate(dirty)}
        >
          {save.isPending ? 'Saving…'
            : `Save as version ${(current.version || 0) + 1}`}
        </button>
        {Object.keys(dirty).length > 0 && (
          <span className="muted" style={{ fontSize: '0.78rem' }}>
            {Object.keys(dirty).length} field(s) changed
          </span>
        )}
        {saved && (
          <span style={{ color: 'var(--text-accent)', fontSize: '0.8rem' }}>
            <Check size={14} style={{ display: 'inline', verticalAlign: -2 }} /> Saved
          </span>
        )}
        {save.isError && (
          <span style={{ color: 'var(--danger, #f85149)', fontSize: '0.8rem' }}>
            {save.error?.response?.data?.detail || 'Could not save'}
          </span>
        )}
      </div>
    </>
  );
}
