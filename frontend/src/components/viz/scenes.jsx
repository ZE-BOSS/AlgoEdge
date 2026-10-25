/**
 * Ready-made 3D scenes on Scene3D. Each builds its geometry once per data
 * change (useMemo) and animates only a cheap overlay, so they can sit on any
 * page without costing the page anything while off screen.
 */
import { useMemo } from 'react';

import Scene3D from './Scene3D';
import { thin } from './thin';

const GREEN = '#3fb950';
const RED = '#f85149';
const BLUE = '#58a6ff';
const PURPLE = '#bc8cff';
const AMBER = '#d29922';

function scaler(values) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }
  if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-12) { hi = lo + 1; }
  return (v) => ((v - lo) / (hi - lo)) * 1.7 - 0.85;
}

/* -- Kronos: the sampled futures fanning out of the price history -------- */

export function PathsFan3D({ history = [], paths = [], height = 260, title }) {
  const scene = useMemo(() => {
    const h = thin(history, 160);
    const all = [...h, ...paths.flat()];
    const y = scaler(all);
    const n = h.length;
    const H = paths[0]?.length || 0;
    const total = n + H;
    const x = (i) => (i / Math.max(1, total - 1)) * 2 - 1;
    const lines = [{ pts: h.map((v, i) => [x(i), y(v), 0]), color: BLUE, width: 2 }];
    const dots = [];
    const last = h[n - 1];
    paths.forEach((p, k) => {
      const z = paths.length > 1 ? (k / (paths.length - 1)) * 1.4 - 0.7 : 0;
      const up = p[p.length - 1] > last;
      const pts = [[x(n - 1), y(last), 0], ...p.map((v, i) => [x(n + i), y(v), z])];
      lines.push({ pts, color: up ? GREEN : RED, width: 1.6, alpha: 0.85 });
      dots.push({ p: pts[pts.length - 1], r: 3, color: up ? GREEN : RED, glow: true });
    });
    if (n) dots.push({ p: [x(n - 1), y(last), 0], r: 3.5, color: BLUE, glow: true });
    return { lines, dots };
  }, [history, paths]);
  return <Scene3D scene={scene} height={height} title={title} autoRotate={8} motion="sway" baseYaw={0.35} />;
}

/* -- equity: curve + underwater drawdown + trades + a sweeping playhead -- */

export function EquityRibbon3D({ equity = [], trades = [], height = 220, title, live = false }) {
  const scene = useMemo(() => {
    const e = thin(equity.filter(Number.isFinite), 320);
    if (e.length < 2) return { lines: [], dots: [] };
    const y = scaler(e);
    const x = (i) => (i / (e.length - 1)) * 2 - 1;
    let peak = -Infinity;
    const dd = e.map((v) => { peak = Math.max(peak, v); return peak > 0 ? (v - peak) / peak : 0; });
    const ddMin = Math.min(-1e-9, ...dd);
    const curve = e.map((v, i) => [x(i), y(v), -0.35]);
    // 0 drawdown sits at y=-0.2, the deepest drawdown of the run at y=-0.95
    const under = dd.map((d, i) => [x(i), -0.2 - 0.75 * (d / ddMin), 0.55]);
    const up = e[e.length - 1] >= e[0];
    const lines = [
      { pts: curve, color: up ? GREEN : RED, width: 2.2 },
      { pts: under, color: RED, width: 1.2, alpha: 0.55 },
    ];
    // vertical "curtain" every few points, joining curve to its drawdown
    for (let i = 0; i < e.length; i += Math.max(1, Math.round(e.length / 40))) {
      lines.push({ pts: [curve[i], under[i]], color: '#8b949e', width: 0.6, alpha: 0.25 });
    }
    const dots = [];
    const tt = thin(trades, 120);
    tt.forEach((t) => {
      const i = Math.min(e.length - 1, Math.round(((t.i ?? 0) / Math.max(1, t.n || equity.length)) * (e.length - 1)));
      dots.push({ p: [x(i), y(e[i]) + 0.06, -0.35], r: 2.2, color: (t.r ?? t.pnl ?? 0) >= 0 ? GREEN : RED, alpha: 0.9 });
    });
    return { lines, dots, curve };
  }, [equity, trades]);
  const overlay = useMemo(() => {
    const c = scene.curve;
    if (!c || c.length < 2) return null;
    return (t) => {
      const i = Math.floor(((t * (live ? 0.25 : 0.12)) % 1) * (c.length - 1));
      return { dots: [{ p: c[i], r: 4, color: live ? AMBER : BLUE, glow: true }] };
    };
  }, [scene, live]);
  return <Scene3D scene={scene} overlay={overlay} height={height} title={title} autoRotate={6} motion="sway" baseYaw={0.4} />;
}

/* -- activity: anything running - training, a backtest, a job, the bot --- */

export function ActivityPulse3D({ pct = null, active = true, label = '', height = 180,
                                  color = PURPLE, intensity = 1, title }) {
  const ring = useMemo(() => {
    const pts = [];
    for (let k = 0; k <= 72; k += 1) {
      const a = (k / 72) * Math.PI * 2;
      pts.push([Math.cos(a) * 0.9, -0.2, Math.sin(a) * 0.9]);
    }
    return pts;
  }, []);
  const scene = useMemo(() => {
    const lines = [{ pts: ring, color: '#8b949e', width: 1, alpha: 0.35 }];
    if (pct != null && Number.isFinite(+pct)) {
      const n = Math.max(2, Math.round((Math.min(100, Math.max(0, +pct)) / 100) * 72));
      lines.push({ pts: ring.slice(0, n + 1), color, width: 3 });
    }
    return { lines, dots: [], labels: label ? [{ p: [-0.9, 1.0, 0], text: label, color: '#c9d1d9' }] : [] };
  }, [ring, pct, color, label]);
  const overlay = useMemo(() => {
    const n = Math.round(14 + 26 * Math.min(1, intensity));
    return (t) => {
      const dots = [];
      const speed = active ? 1 : 0.15;
      for (let k = 0; k < n; k += 1) {
        const a = t * speed * (0.6 + (k % 5) * 0.12) + (k * 2.399);
        const rr = 0.35 + ((k * 37) % 50) / 100;
        const yy = Math.sin(t * speed * 1.3 + k) * 0.35 + 0.15;
        dots.push({ p: [Math.cos(a) * rr, yy, Math.sin(a) * rr], r: 1.8 + (k % 3), color: k % 4 ? color : BLUE,
                    alpha: active ? 0.85 : 0.35, glow: k % 6 === 0 });
      }
      dots.push({ p: [0, 0.15, 0], r: active ? 6 + Math.sin(t * 3) * 1.5 : 4, color, glow: true });
      return { dots };
    };
  }, [active, color, intensity]);
  return <Scene3D scene={scene} overlay={overlay} height={height} title={title} autoRotate={active ? 14 : 4} grid={false} />;
}
