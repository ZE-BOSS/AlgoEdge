import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Upload } from 'lucide-react';
import { inv } from '../../services/api';
import { ActionForm, Empty, ErrorLine, QueryState } from './shared';
import { fmtDate, useInvAction } from './format';

function size(bytes) {
  return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} kB`;
}

function UploadForm({ nextCode }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [notes, setNotes] = useState('');
  const [progress, setProgress] = useState(null);
  const up = useInvAction((form) => inv.uploadRelease(form, setProgress), {
    onSuccess: () => { setFile(null); setName(''); setCode(''); setNotes(''); setProgress(null); },
  });
  const submit = (e) => {
    e.preventDefault();
    const f = new FormData();
    f.append('file', file); f.append('version_name', name); f.append('version_code', code || String(nextCode));
    if (notes) f.append('notes', notes);
    f.append('make_current', 'true');
    up.mutate(f);
  };
  return (
    <form onSubmit={submit} style={{ display: 'grid', gap: 12, maxWidth: 560 }}>
      <div>
        <label>APK file (from the EAS build)</label>
        <input type="file" accept=".apk,application/vnd.android.package-archive" required
               onChange={(e) => setFile(e.target.files?.[0] || null)} />
      </div>
      <div className="grid-2">
        <div><label>Version name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="1.0.0" required /></div>
        <div><label>Version code</label>
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder={String(nextCode)} inputMode="numeric" />
          <div className="inv-hint">Must be higher than every earlier release — Android refuses to install a lower one. Use the build's <code>versionCode</code>.</div></div>
      </div>
      <div><label>What changed (shown to investors)</label>
        <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
      <div className="inv-actions" style={{ alignItems: 'center' }}>
        <button className="btn btn-primary btn-sm" disabled={!file || !name || up.isPending}>
          <Upload size={12} /> {up.isPending ? `Uploading${progress != null ? ` ${progress}%` : '…'}` : 'Upload and publish'}
        </button>
        {up.isSuccess && <span className="badge badge-green">published</span>}
      </div>
      <ErrorLine error={up.error} />
    </form>
  );
}

export default function Releases() {
  const q = useQuery({ queryKey: ['inv', 'releases'], queryFn: inv.releases });
  const current = useInvAction((id) => inv.setCurrentRelease(id));
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="card">
        <div className="card-header"><span className="card-title">Publish a new Android build</span></div>
        <p className="inv-hint" style={{ marginTop: 0 }}>The website's download button and the app's update prompt
          both follow the current release, so shipping an update is an upload, not a deploy. Its SHA-256 is shown
          next to the download so a careful investor can check the file.</p>
        <QueryState q={q}>
          {(rows) => <UploadForm nextCode={(rows[0]?.version_code || 0) + 1} />}
        </QueryState>
      </div>
      <div className="card">
        <div className="card-header"><span className="card-title">Releases</span></div>
        <QueryState q={q}>
          {(rows) => rows.length === 0 ? <Empty>No builds uploaded yet.</Empty> : (
            <div className="table-wrapper">
              <table>
                <thead><tr><th>Version</th><th className="num">Code</th><th>Uploaded</th><th className="num">Size</th>
                  <th>SHA-256</th><th>Notes</th><th /></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td><strong>{r.version_name}</strong> {r.is_current && <span className="badge badge-green">current</span>}</td>
                      <td className="num">{r.version_code}</td>
                      <td>{fmtDate(r.created_at)}</td>
                      <td className="num">{size(r.size_bytes)}</td>
                      <td><code className="inv-json">{r.sha256.slice(0, 16)}…</code></td>
                      <td style={{ maxWidth: 260 }}>{r.notes}</td>
                      <td>{!r.is_current && (
                        <ActionForm label="Make current" compact onSubmit={() => current.mutateAsync(r.id)} />
                      )}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </div>
    </div>
  );
}
