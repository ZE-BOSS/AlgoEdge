import { useEffect, useState } from 'react';

/**
 * A tab opened before a deploy keeps running the old code until it is reloaded:
 * the page looks the same, so fixes seem not to have landed. This checks which
 * build the server is serving now (the script index.html points at) every two
 * minutes and when the tab is focused, and offers a reload once it changed.
 * A lazy page whose old chunk was removed by the deploy reloads on its own.
 */
const scriptOf = html => (html.match(/\/assets\/index-[\w-]+\.js/) || [])[0] || null;
const current = () => {
  const s = Array.from(document.scripts).map(x => x.src).find(src => /\/assets\/index-[\w-]+\.js/.test(src));
  return s ? scriptOf(s) : null;
};

export default function UpdateBanner() {
  const [stale, setStale] = useState(false);
  useEffect(() => {
    const mine = current();
    if (!mine) return undefined; // dev server: nothing to compare
    let alive = true;
    const check = async () => {
      try {
        const html = await fetch('/index.html', { cache: 'no-store' }).then(r => r.text());
        const served = scriptOf(html);
        if (alive && served && served !== mine) setStale(true);
      } catch { /* offline: try again later */ }
    };
    const onFocus = () => { if (document.visibilityState === 'visible') check(); };
    const onPreload = (e) => { e.preventDefault(); window.location.reload(); };
    const t = setInterval(check, 120000);
    document.addEventListener('visibilitychange', onFocus);
    window.addEventListener('vite:preloadError', onPreload);
    return () => {
      alive = false;
      clearInterval(t);
      document.removeEventListener('visibilitychange', onFocus);
      window.removeEventListener('vite:preloadError', onPreload);
    };
  }, []);
  if (!stale) return null;
  return (
    <div style={{
      position: 'fixed', bottom: 16, right: 16, zIndex: 100001, display: 'flex', alignItems: 'center', gap: 12,
      background: '#1e293b', border: '1px solid #3b82f6', borderRadius: 8, padding: '10px 14px',
      fontSize: '0.85rem', boxShadow: '0 4px 18px rgba(0,0,0,0.4)',
    }}>
      <span>A new version was deployed. This tab is still running the old one.</span>
      <button type="button" className="btn btn-primary btn-sm" onClick={() => window.location.reload()}>Reload</button>
    </div>
  );
}
