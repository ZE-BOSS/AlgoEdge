/**
 * Scene3D: a tiny, dependency-free 3D renderer on a 2D canvas.
 *
 * Built for live "what is happening" visuals (training, backtests, forecasts,
 * live trading) that must never slow the app down:
 *   - no WebGL / three.js (zero bundle cost beyond this file);
 *   - geometry is built ONCE per data change (`scene` prop), and each frame
 *     only rotates + projects it;
 *   - animates only while the canvas is on screen AND the tab is visible
 *     (IntersectionObserver + visibilitychange) - offscreen it costs nothing;
 *   - capped at 30 fps and 1.5x device pixels;
 *   - prefers-reduced-motion -> one static frame;
 *   - drag to rotate; it eases back into auto-rotation.
 *
 * `scene` = { lines: [{ pts: [[x,y,z],...], color, width, alpha, dash }],
 *             dots:  [{ p: [x,y,z], r, color, alpha }],
 *             labels:[{ p: [x,y,z], text, color }] }
 * in normalised space (about -1..1 on each axis; y is up).
 * `overlay(t)` (optional) returns extra { lines, dots } per frame - for moving
 * things like a playhead or orbiting particles - and must be cheap.
 */
import { useEffect, useRef } from 'react';

const FPS = 30;
const DPR_CAP = 1.5;

function prefersReducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

export default function Scene3D({
  scene, overlay, height = 220, autoRotate = 10, tilt = 0.32, grid = true,
  background = 'transparent', title, className, style,
  // 'spin' turns all the way round (activity visuals). 'sway' rocks
  // +-swayDeg around a readable angle: a chart seen edge-on is unreadable, so
  // data scenes never spin past it.
  motion = 'spin', swayDeg = 32, baseYaw = 0.45,
}) {
  const canvasRef = useRef(null);
  const state = useRef({ yaw: baseYaw, pitch: tilt, drag: null, lastUser: 0, phase: 0, offset: 0 });
  const sceneRef = useRef(scene);
  const overlayRef = useRef(overlay);
  const kickRef = useRef(null);
  useEffect(() => {
    sceneRef.current = scene;
    overlayRef.current = overlay;
    if (kickRef.current) kickRef.current();
  }, [scene, overlay]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');
    let raf = 0;
    let visible = false;
    let last = 0;
    const reduced = prefersReducedMotion();
    const st = state.current;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
      const w = canvas.clientWidth || 300;
      const h = canvas.clientHeight || height;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const project = (p, cw, ch, cy, sy, cp, sp) => {
      // yaw around y, then pitch around x, then perspective
      const x1 = p[0] * cy - p[2] * sy;
      const z1 = p[0] * sy + p[2] * cy;
      const y1 = p[1] * cp - z1 * sp;
      const z2 = p[1] * sp + z1 * cp;
      const d = 3.2;
      // Separate horizontal and vertical scale: a wide card (1000 x 240) is
      // filled across its width instead of being held to its height, and
      // never clipped top or bottom. Capped at 2.2:1 so it does not smear.
      const persp = d / (z2 + d) / 1.05;
      const fy = ch * 0.4 * persp;
      const fx = Math.min(cw * 0.42 * persp, fy * 2.2);
      return [cw / 2 + x1 * fx, ch / 2 - y1 * fy, z2];
    };

    const drawLines = (lines, P) => {
      for (const ln of lines || []) {
        const pts = ln.pts;
        if (!pts || pts.length < 2) continue;
        ctx.beginPath();
        let p = P(pts[0]);
        ctx.moveTo(p[0], p[1]);
        for (let i = 1; i < pts.length; i += 1) {
          p = P(pts[i]);
          ctx.lineTo(p[0], p[1]);
        }
        ctx.globalAlpha = ln.alpha ?? 1;
        ctx.strokeStyle = ln.color || '#58a6ff';
        ctx.lineWidth = ln.width || 1.5;
        ctx.setLineDash(ln.dash || []);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    };

    const drawDots = (dots, P) => {
      for (const d of dots || []) {
        const p = P(d.p);
        const r = Math.max(0.6, (d.r || 2.5) * (3.2 / (p[2] + 3.2)));
        ctx.globalAlpha = d.alpha ?? 1;
        ctx.fillStyle = d.color || '#3fb950';
        ctx.beginPath();
        ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
        ctx.fill();
        if (d.glow) {
          ctx.globalAlpha = (d.alpha ?? 1) * 0.25;
          ctx.beginPath();
          ctx.arc(p[0], p[1], r * 2.6, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
    };

    const frame = (now) => {
      raf = 0;
      if (!visible || document.hidden) return;
      if (now - last < 1000 / FPS && !reduced) {
        raf = requestAnimationFrame(frame);
        return;
      }
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
      last = now;
      if (!st.drag && !reduced && now - st.lastUser > 1500) {
        if (motion === 'sway') {
          st.phase += (autoRotate / 40) * dt;
          st.offset *= 0.97;          // ease back from a drag to the readable angle
          st.yaw = baseYaw + st.offset + (swayDeg * Math.PI / 180) * Math.sin(st.phase);
        } else {
          st.yaw += (autoRotate * Math.PI / 180) * dt;
        }
      }
      const cw = canvas.clientWidth;
      const ch = canvas.clientHeight;
      ctx.clearRect(0, 0, cw, ch);
      if (background !== 'transparent') {
        ctx.fillStyle = background;
        ctx.fillRect(0, 0, cw, ch);
      }
      const cy = Math.cos(st.yaw);
      const sy = Math.sin(st.yaw);
      const cp = Math.cos(st.pitch);
      const sp = Math.sin(st.pitch);
      const P = (p) => project(p, cw, ch, cy, sy, cp, sp);
      if (grid) {
        const g = [];
        for (let k = -1; k <= 1.001; k += 0.5) {
          g.push({ pts: [[-1.1, -1, k], [1.1, -1, k]], color: '#8b949e', width: 0.6, alpha: 0.18 });
          g.push({ pts: [[k, -1, -1.1], [k, -1, 1.1]], color: '#8b949e', width: 0.6, alpha: 0.18 });
        }
        drawLines(g, P);
      }
      const sc = sceneRef.current || {};
      drawLines(sc.lines, P);
      drawDots(sc.dots, P);
      const ov = overlayRef.current ? overlayRef.current(now / 1000) : null;
      if (ov) {
        drawLines(ov.lines, P);
        drawDots(ov.dots, P);
      }
      const labels = [...(sc.labels || []), ...((ov && ov.labels) || [])];
      if (labels.length) {
        ctx.font = '10px system-ui, sans-serif';
        for (const l of labels) {
          const p = P(l.p);
          ctx.fillStyle = l.color || '#8b949e';
          ctx.fillText(l.text, p[0] + 4, p[1] - 4);
        }
      }
      if (!reduced) raf = requestAnimationFrame(frame);
    };

    const kick = () => {
      if (!raf && visible && !document.hidden) raf = requestAnimationFrame(frame);
    };

    kickRef.current = () => { last = 0; kick(); };
    resize();
    const ro = new ResizeObserver(() => { resize(); kick(); });
    ro.observe(canvas);
    const io = new IntersectionObserver((entries) => {
      visible = entries.some((e) => e.isIntersecting);
      if (visible) { last = 0; kick(); }
    }, { threshold: 0.05 });
    io.observe(canvas);
    const onVis = () => { if (!document.hidden) { last = 0; kick(); } };
    document.addEventListener('visibilitychange', onVis);

    const down = (e) => { st.drag = { x: e.clientX, y: e.clientY, yaw: st.yaw, pitch: st.pitch }; };
    const move = (e) => {
      if (!st.drag) return;
      st.yaw = st.drag.yaw + (e.clientX - st.drag.x) * 0.01;
      st.offset = st.yaw - baseYaw - (swayDeg * Math.PI / 180) * Math.sin(st.phase);
      st.pitch = Math.max(-0.2, Math.min(1.2, st.drag.pitch + (e.clientY - st.drag.y) * 0.006));
      st.lastUser = performance.now();
      kick();
    };
    const up = () => { st.drag = null; st.lastUser = performance.now(); };
    canvas.addEventListener('pointerdown', down);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);

    return () => {
      kickRef.current = null;
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      document.removeEventListener('visibilitychange', onVis);
      canvas.removeEventListener('pointerdown', down);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, [height, autoRotate, grid, background, motion, swayDeg, baseYaw]);

  return (
    <div className={className} style={{ position: 'relative', ...style }}>
      {title && (
        <div style={{ position: 'absolute', left: 10, top: 6, fontSize: 11, color: 'var(--text-muted, #8b949e)',
                      pointerEvents: 'none', letterSpacing: 0.3 }}>{title}</div>
      )}
      <canvas ref={canvasRef} style={{ width: '100%', height, display: 'block', cursor: 'grab', touchAction: 'pan-y' }} />
    </div>
  );
}
