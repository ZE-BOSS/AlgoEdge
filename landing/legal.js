/* Fills the published policies with the legal identity an admin has entered.
 *
 * WHY THE POLICIES ARE NOT REGENERATED
 * privacy.html and terms.html are static files with no build step, and the
 * entity name, registered address and governing jurisdiction live in a database
 * row so they can be changed without touching the site. Rather than rebuild the
 * HTML on every edit, the pages carry `data-legal` placeholders and this fills
 * them from GET /api/public/legal.
 *
 * DEGRADING HONESTLY IS THE WHOLE DESIGN
 * If the API is unreachable, or JavaScript is off, or no profile has been saved
 * yet, every placeholder keeps the wording already written into the HTML. A
 * policy must never render as half a sentence or an empty blank — the fallback
 * text is a true, if less specific, statement, and the specific one replaces it
 * only when there is something specific to say.
 *
 * Elements:
 *   <span data-legal="legal_entity_name">Alphavantiq Capital</span>
 *   <span data-legal="postal_address" data-legal-wrap=" of {} ">...</span>
 *       — `{}` is where the value goes; the whole element is removed when the
 *         value is empty, so "registered at ." cannot happen.
 */
(function () {
  const API = (window.AVQ && window.AVQ.api) || '';
  const nodes = document.querySelectorAll('[data-legal]');
  if (!nodes.length || !API) return;

  fetch(API + '/api/public/legal', { headers: { Accept: 'application/json' } })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (!d) return;
      nodes.forEach((el) => {
        const key = el.getAttribute('data-legal');
        const value = (d[key] || '').trim();
        const wrap = el.getAttribute('data-legal-wrap');
        if (!value) {
          // Nothing specific to say. An element that only exists to carry an
          // optional clause is removed; one with its own fallback wording keeps it.
          if (wrap) el.remove();
          return;
        }
        if (el.tagName === 'A' && key.endsWith('email')) {
          el.textContent = value;
          el.setAttribute('href', 'mailto:' + value);
          return;
        }
        el.textContent = wrap ? wrap.replace('{}', value) : value;
      });
    })
    .catch(() => {
      /* offline: the wording written into the page stands, which is the point */
    });
})();
