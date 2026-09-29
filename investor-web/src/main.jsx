import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>);

// Installable app shell (iOS "Add to Home Screen", Android install). Production
// only: in development a service worker would serve stale code between edits.
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js'));
}
