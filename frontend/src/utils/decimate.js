/**
 * Evenly thin a series down to at most `limit` points, always keeping the last.
 *
 * Charts cannot show more points than the canvas has pixels, but Recharts will
 * happily try: an undecimated 72,578-point equity curve allocates 72k objects
 * and 72k SVG path segments per render, which is what made the Analytics page
 * hang on large backtests. 500 points is well past the resolution of any chart
 * we draw, so nothing visible is lost.
 *
 * Shared between Backtester and Analytics so the two cannot drift — Analytics
 * previously had no decimation at all.
 */
export function decimate(series, limit = 500) {
  if (!Array.isArray(series) || series.length <= limit) return series || [];
  const step = Math.ceil(series.length / limit);
  const out = [];
  for (let i = 0; i < series.length; i += step) out.push(series[i]);
  if (out[out.length - 1] !== series[series.length - 1]) out.push(series[series.length - 1]);
  return out;
}

export default decimate;
