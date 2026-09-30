import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { ago } from '../format';
import Icon from './Icons';

const KIND_ICON = {
  trade_opened: 'chart', trade_published: 'trades', deposit_claimed: 'arrowIn', deposit_confirmed: 'arrowIn',
  deposit_rejected: 'arrowIn', withdrawal_received: 'arrowOut', withdrawal_approved: 'arrowOut',
  withdrawal_paid: 'arrowOut', withdrawal_declined: 'arrowOut', fee_charged: 'fee', statement: 'doc',
  password_changed: 'shield', payout_changed: 'bank', closure_requested: 'user',
};
const POLL_MS = 45_000;

/** The bell in the header: unread count, and the feed in a panel. */
export default function Bell() {
  const [feed, setFeed] = useState({ unread: 0, items: [] });
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const go = useNavigate();

  const load = useCallback(() => api.notifications().then(setFeed).catch(() => {}), []);
  useEffect(() => {
    load();
    const id = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    return () => clearInterval(id);
  }, [load]);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [open]);

  const readAll = () => api.readNotifications().then(load).catch(() => {});
  const openItem = (n) => {
    if (!n.read) api.readNotifications([n.id]).then(load).catch(() => {});
    setOpen(false);
    go(n.link || '/');
  };

  return (
    <div className="bell" ref={box}>
      <button className="icon-btn" onClick={() => { setOpen((o) => !o); if (!open) load(); }}
              aria-expanded={open} aria-label={`Notifications${feed.unread ? `, ${feed.unread} unread` : ''}`}>
        <Icon name="bell" />
        {feed.unread > 0 && <span className="bell-dot">{feed.unread > 9 ? '9+' : feed.unread}</span>}
      </button>
      {open && (
        <div className="bell-panel" role="dialog" aria-label="Notifications">
          <div className="bell-head">
            <strong>Notifications</strong>
            {feed.unread > 0 && <button className="linkish small" onClick={readAll}>Mark all read</button>}
          </div>
          {feed.items.length === 0 ? (
            <div className="empty small"><Icon name="bell" size={28} /><p className="muted">Nothing yet. Trades, money
              moving in and out, and fees will show up here.</p></div>
          ) : (
            <ul className="bell-list">
              {feed.items.map((n) => (
                <li key={n.id}>
                  <button className={n.read ? '' : 'unread'} onClick={() => openItem(n)}>
                    <span className={`bell-ic k-${n.kind.split('_')[0]}`}><Icon name={KIND_ICON[n.kind] || 'info'} size={16} /></span>
                    <span className="bell-txt">
                      <strong>{n.title}</strong>
                      {n.body && <span className="muted small">{n.body}</span>}
                      <span className="muted tiny">{ago(n.created_at)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
