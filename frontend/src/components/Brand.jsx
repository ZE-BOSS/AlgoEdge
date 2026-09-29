/**
 * Brand.jsx — the Alphavantiq Capital monogram and lockup.
 *
 * Inline SVG rather than <img src="/mark.svg"> so the mark inherits currentColor
 * for the wordmark, costs no extra request, and cannot flash in late on a cold
 * load — which on the login screen is the first thing anyone sees.
 *
 * Every id is namespaced with `useId`. Two copies of the same gradient or
 * clipPath id on one page is a real bug, not a tidiness point: the second
 * element silently reuses the first one's definition, and loses its fill
 * entirely if the first unmounts. The sidebar and a dialog both showing the mark
 * is exactly that case.
 *
 * Source artwork and the full asset set live in /brand.
 */
import { useId } from 'react';

// Tapered planes, not uniform strokes, with a counter whose base rises left to
// right. Kept identical to brand/mark.svg — if one changes, change both.
const GLYPH = 'M32 4.5 L57.5 59 L47.4 59 L42.9 48.4 L21.1 50.6 L16.6 59 L6.5 59 Z '
            + 'M26.1 41.9 L38.2 39.7 L32 24.6 Z';

export function Mark({ size = 32, className = '', title = 'Alphavantiq Capital' }) {
  const uid = useId().replace(/:/g, '');
  const lit = `${uid}-lit`, shade = `${uid}-shade`;
  const glyph = `${uid}-g`, clipL = `${uid}-l`, clipR = `${uid}-r`;
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={className}
         role="img" aria-label={title}>
      <defs>
        {/* left plane shadowed, right plane lit: one folded surface catching
            light, rather than a flat letter */}
        <linearGradient id={lit} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#F8E6B4" />
          <stop offset="0.55" stopColor="#E4C273" />
          <stop offset="1" stopColor="#C9963A" />
        </linearGradient>
        <linearGradient id={shade} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#CFA245" />
          <stop offset="0.55" stopColor="#A97E28" />
          <stop offset="1" stopColor="#835F1C" />
        </linearGradient>
        <path id={glyph} fillRule="evenodd" d={GLYPH} />
        <clipPath id={clipL}><rect x="0" y="0" width="32" height="64" /></clipPath>
        <clipPath id={clipR}><rect x="32" y="0" width="32" height="64" /></clipPath>
      </defs>
      <use href={`#${glyph}`} fill={`url(#${shade})`} clipPath={`url(#${clipL})`} />
      <use href={`#${glyph}`} fill={`url(#${lit})`} clipPath={`url(#${clipR})`} />
    </svg>
  );
}

/** Mark + wordmark. `size` drives the mark; the text scales with the container. */
export function Wordmark({ size = 30, className = '' }) {
  return (
    <span className={`brand-lockup ${className}`}>
      <Mark size={size} />
      <span className="brand-words">
        ALPHAVANTIQ
        <em>CAPITAL</em>
      </span>
    </span>
  );
}

export default Wordmark;
