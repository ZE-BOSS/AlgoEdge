/**
 * Canvas primitives for lightweight-charts.
 *
 * Canvas rather than DOM overlays throughout: a busy replay carries hundreds of
 * zones and markers, and DOM nodes start dropping frames long before canvas
 * does (Visualization plan §4.6).
 */

// ── Shared base ──────────────────────────────────────────────────────────
// Every primitive needs the same attach/detach/paneViews plumbing; only the
// renderer differs. Factoring it out keeps each primitive to its draw call.
class BasePrimitive {
  attached({ chart, series, requestUpdate }) {
    this.chart = chart;
    this.series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
  }

  updateAllViews() {
    if (this._requestUpdate) this._requestUpdate();
  }

  paneViews() {
    return [{ update() {}, renderer: () => this._renderer() }];
  }
}

// ── Rectangle (FVG / OB / zone / range boxes) ─────────────────────────────
class RectangleRenderer {
  constructor(source) { this._source = source; }

  draw(target) {
    target.useBitmapCoordinateSpace((scope) => {
      const s = this._source;
      if (!s.chart || !s.series) return;

      const ts = s.chart.timeScale();
      const x1 = ts.timeToCoordinate(s.p1.time);
      const y1 = s.series.priceToCoordinate(s.p1.price);
      const y2 = s.series.priceToCoordinate(s.p2.price);
      if (x1 === null || y1 === null || y2 === null) return;

      // An open-ended box (end_time null) extends to the right edge rather than
      // vanishing — that is what "this level is still in play" looks like, and
      // it is how the zone behaved for the strategy.
      let x2 = s.p2.time == null ? null : ts.timeToCoordinate(s.p2.time);
      if (x2 === null) x2 = scope.mediaSize.width;

      const ctx = scope.context;
      const left = Math.min(x1, x2) * scope.horizontalPixelRatio;
      const right = Math.max(x1, x2) * scope.horizontalPixelRatio;
      const top = Math.min(y1, y2) * scope.verticalPixelRatio;
      const bottom = Math.max(y1, y2) * scope.verticalPixelRatio;

      ctx.fillStyle = s.color;
      ctx.fillRect(left, top, right - left, bottom - top);

      if (s.borderColor) {
        ctx.strokeStyle = s.borderColor;
        ctx.lineWidth = Math.max(1, scope.verticalPixelRatio);
        ctx.strokeRect(left, top, right - left, bottom - top);
      }

      if (s.label) drawLabel(ctx, scope, s.label, left + 4 * scope.horizontalPixelRatio, top, s.labelColor);
    });
  }
}

export class RectanglePrimitive extends BasePrimitive {
  constructor(p1, p2, color, opts = {}) {
    super();
    this.p1 = p1;               // { time, price }
    this.p2 = p2;               // { time (nullable = extend right), price }
    this.color = color;
    this.borderColor = opts.borderColor || null;
    this.label = opts.label || null;
    this.labelColor = opts.labelColor || 'rgba(230,237,243,0.75)';
  }

  _renderer() { return new RectangleRenderer(this); }
}

// ── Horizontal level (key level / liquidity / band) ───────────────────────
class LevelRenderer {
  constructor(source) { this._source = source; }

  draw(target) {
    target.useBitmapCoordinateSpace((scope) => {
      const s = this._source;
      if (!s.series || !s.chart) return;

      const y = s.series.priceToCoordinate(s.price);
      if (y === null) return;

      const ctx = scope.context;
      const ts = s.chart.timeScale();
      // A level that names a start time is drawn from that bar onward, so it is
      // visible that the level did not exist before the strategy identified it.
      let x0 = s.startTime == null ? 0 : ts.timeToCoordinate(s.startTime);
      if (x0 === null) x0 = 0;
      let x1 = s.endTime == null ? scope.mediaSize.width : ts.timeToCoordinate(s.endTime);
      if (x1 === null) x1 = scope.mediaSize.width;

      const py = y * scope.verticalPixelRatio;
      ctx.save();
      ctx.strokeStyle = s.color;
      ctx.lineWidth = (s.lineWidth || 1) * scope.verticalPixelRatio;
      if (s.dashed) ctx.setLineDash([6 * scope.horizontalPixelRatio, 4 * scope.horizontalPixelRatio]);
      ctx.beginPath();
      ctx.moveTo(x0 * scope.horizontalPixelRatio, py);
      ctx.lineTo(x1 * scope.horizontalPixelRatio, py);
      ctx.stroke();
      ctx.restore();

      if (s.label) {
        drawLabel(ctx, scope, s.label, x0 * scope.horizontalPixelRatio + 4, py - 14 * scope.verticalPixelRatio, s.color);
      }
    });
  }
}

export class LevelPrimitive extends BasePrimitive {
  constructor(price, color, opts = {}) {
    super();
    this.price = price;
    this.color = color;
    this.startTime = opts.startTime ?? null;
    this.endTime = opts.endTime ?? null;
    this.dashed = opts.dashed ?? false;
    this.lineWidth = opts.lineWidth ?? 1;
    this.label = opts.label || null;
  }

  _renderer() { return new LevelRenderer(this); }
}

// ── Order-flow bubbles ────────────────────────────────────────────────────
class BubbleRenderer {
  constructor(source) { this._source = source; }

  draw(target) {
    target.useBitmapCoordinateSpace((scope) => {
      const s = this._source;
      if (!s.series || !s.chart || !s.points.length) return;

      const ctx = scope.context;
      const ts = s.chart.timeScale();
      // Radius scales with the square root of magnitude, not magnitude itself:
      // a bubble's *area* is what the eye reads as size, so linear radius
      // scaling exaggerates large prints by the square of their real weight.
      const maxMag = s.points.reduce((m, p) => Math.max(m, Math.abs(p.value)), 0) || 1;

      ctx.save();
      for (const p of s.points) {
        const x = ts.timeToCoordinate(p.time);
        const y = s.series.priceToCoordinate(p.price);
        if (x === null || y === null) continue;

        const r = (s.minRadius + (s.maxRadius - s.minRadius) * Math.sqrt(Math.abs(p.value) / maxMag))
          * scope.verticalPixelRatio;

        ctx.beginPath();
        ctx.arc(x * scope.horizontalPixelRatio, y * scope.verticalPixelRatio, r, 0, Math.PI * 2);
        ctx.fillStyle = p.value >= 0 ? s.buyColor : s.sellColor;
        ctx.fill();
      }
      ctx.restore();
    });
  }
}

export class BubblePrimitive extends BasePrimitive {
  /** points: [{ time, price, value }] — value signed by aggressor side. */
  constructor(points, opts = {}) {
    super();
    this.points = points || [];
    this.buyColor = opts.buyColor || 'rgba(63,182,139,0.35)';
    this.sellColor = opts.sellColor || 'rgba(248,81,73,0.35)';
    this.minRadius = opts.minRadius ?? 2;
    this.maxRadius = opts.maxRadius ?? 18;
  }

  setPoints(points) {
    this.points = points || [];
    this.updateAllViews();
  }

  _renderer() { return new BubbleRenderer(this); }
}

// ── Shared label helper ───────────────────────────────────────────────────
function drawLabel(ctx, scope, text, x, y, color) {
  const fontPx = 10 * scope.verticalPixelRatio;
  ctx.save();
  ctx.font = `${fontPx}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textBaseline = 'top';

  const padding = 3 * scope.horizontalPixelRatio;
  const w = ctx.measureText(text).width + padding * 2;
  const h = fontPx + padding * 2;

  // A translucent plate behind the text, because a label drawn straight onto
  // candles is unreadable wherever it overlaps a wick.
  ctx.fillStyle = 'rgba(13,17,23,0.72)';
  ctx.fillRect(x, y, w, h);

  ctx.fillStyle = color || 'rgba(230,237,243,0.85)';
  ctx.fillText(text, x + padding, y + padding);
  ctx.restore();
}
