/**
 * frontend/src/hooks/useServerColour.js
 *
 * In its own file rather than beside `ServerSwitcher`, because a module that
 * exports both a component and a hook breaks fast refresh — the project's
 * lint says so, and `useAgents.js` was split out for the same reason.
 */

import { useEffect, useState } from 'react';
import { ROLE_COLOUR, activeServer } from '../services/servers';

/** The accent for the active server, for anything that wants to tint itself. */
export function useServerColour() {
  const read = () => ROLE_COLOUR[activeServer()?.role] || ROLE_COLOUR.unknown;
  const [colour, setColour] = useState(read);
  useEffect(() => {
    const sync = () => setColour(read());
    window.addEventListener('servers-changed', sync);
    return () => window.removeEventListener('servers-changed', sync);
    // `read` closes over nothing that changes; the event is the only input.
  }, []);
  return colour;
}
