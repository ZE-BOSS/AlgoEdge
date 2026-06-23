# Frontend_PWA_LLM_Spec.md
## AlgoEdge — Frontend (PWA) & LLM Integration Specification
### Progressive Web App | Push Notifications | Offline Mode | AI Trade Analysis

---

## Part 1: Frontend Architecture

### 1.1 Technology Stack

| Layer | Technology | Reason |
|-------|-----------|--------|
| Framework | React 18 + Vite | Fastest build, HMR, modern JSX |
| PWA Layer | `vite-plugin-pwa` + Workbox | Zero-config service worker generation |
| Charts | TradingView Lightweight Charts v5 | Financial-grade, 60fps, open source |
| State | Zustand | Lighter than Redux, async-friendly |
| WebSocket | Native browser WebSocket | Zero dependency, maximum speed |
| Styling | Tailwind CSS v3 | Utility-first, mobile-first responsive |
| Notifications | Web Push API + VAPID | True background push, bypasses closed tab |
| HTTP Client | Axios + React Query | Caching, retry, stale-while-revalidate |
| Routing | React Router v6 | SPA routing |
| Icons | Lucide React | Consistent, lightweight |
| Forms | React Hook Form | Performant, validation |
| Charts/Analytics | Recharts + D3 | Equity curves, histograms, heatmaps |

### 1.2 Hosting Architecture

```
                  ┌────────────────────────────────────────┐
                  │    WEB HOST (Vercel / Netlify / VPS)   │
                  │                                         │
                  │    Frontend React PWA (HTTPS)           │
                  │    - Static assets (JS, CSS, images)    │
                  │    - Service Worker (sw.js)             │
                  │    - Web App Manifest (manifest.json)   │
                  └─────────────┬───────────────────────────┘
                                │ HTTPS (REST + WSS)
                                │ user's browser connects
                  ┌─────────────▼───────────────────────────┐
                  │  LOCAL DESKTOP (User's PC)               │
                  │                                          │
                  │  FastAPI Backend  ←→  MT5 Terminal       │
                  │  (localhost:8000)     (running broker)   │
                  │                                          │
                  │  Redis  ←→  PostgreSQL                       │
                  └──────────────────────────────────────────┘
```

**Connection flow:**
1. Frontend hosted at `https://algoedge.yourdomain.com` (permanent URL)
2. User opens it — gets the cached shell immediately (offline-capable)
3. Frontend tries to connect to `http://[local_ip]:8000/api/health`
4. If backend responds → full dashboard loads with live data
5. If backend is offline → shows all historical/saved data from cache + "Backend Offline" status
6. When backend comes online → auto-reconnects WebSocket, shows "Connected" toast + notification

**Why local IP instead of localhost:**
The frontend is served from a web host but connects to the user's local network. Backend must be reachable via the local network IP (e.g., `192.168.1.100:8000`). Users configure their local IP in Settings on first setup.

---

## Part 2: Progressive Web App (PWA) Implementation

### 2.1 What PWA Enables

| Feature | PWA Capability |
|---------|---------------|
| Install to home screen | ✅ Android, iOS 16.4+, Desktop Chrome/Edge |
| Works offline | ✅ Cached dashboard shell loads instantly |
| Background sync | ✅ Syncs latest trade data when reconnected |
| Push notifications | ✅ Even when browser is closed (Android/Desktop) |
| App-like experience | ✅ Full-screen mode, splash screen, no browser chrome |
| No app store needed | ✅ Direct install from browser |

### 2.2 Web App Manifest (`manifest.json`)

```json
{
  "name": "AlgoEdge Trading Bot",
  "short_name": "AlgoEdge",
  "description": "SMC Algorithmic Trading Dashboard",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0d1117",
  "theme_color": "#1a7f5c",
  "orientation": "portrait-primary",
  "icons": [
    { "src": "/icons/icon-72.png",   "sizes": "72x72",   "type": "image/png" },
    { "src": "/icons/icon-192.png",  "sizes": "192x192", "type": "image/png", "purpose": "any maskable" },
    { "src": "/icons/icon-512.png",  "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
  ],
  "screenshots": [
    { "src": "/screenshots/dashboard.png", "sizes": "1280x720", "type": "image/png", "form_factor": "wide" },
    { "src": "/screenshots/mobile.png",    "sizes": "390x844",  "type": "image/png", "form_factor": "narrow" }
  ],
  "categories": ["finance", "productivity"],
  "shortcuts": [
    {
      "name": "Live Dashboard",
      "url": "/dashboard",
      "icons": [{ "src": "/icons/shortcut-dashboard.png", "sizes": "96x96" }]
    },
    {
      "name": "Trade Journal",
      "url": "/journal",
      "icons": [{ "src": "/icons/shortcut-journal.png", "sizes": "96x96" }]
    }
  ]
}
```

### 2.3 Service Worker Strategy

Uses Workbox (via `vite-plugin-pwa`) with:

```javascript
// vite-plugin-pwa config in vite.config.js
VitePWA({
  registerType: 'autoUpdate',
  workbox: {
    // Cache app shell — serves instantly offline
    globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2}'],
    
    // Runtime caching: API responses (stale-while-revalidate)
    runtimeCaching: [
      {
        urlPattern: /\/api\/trades/,
        handler: 'StaleWhileRevalidate',
        options: { cacheName: 'trades-cache', expiration: { maxAgeSeconds: 3600 } }
      },
      {
        urlPattern: /\/api\/stats/,
        handler: 'StaleWhileRevalidate',
        options: { cacheName: 'stats-cache', expiration: { maxAgeSeconds: 1800 } }
      },
      {
        urlPattern: /\/api\/trades\/.*\/snapshot/,
        handler: 'CacheFirst',
        options: { cacheName: 'snapshots-cache', expiration: { maxEntries: 500 } }
      },
    ],
  },
  manifest: { /* manifest.json contents above */ },
  devOptions: { enabled: true }
})
```

### 2.4 Backend Connection Detection

The frontend **constantly monitors** backend connectivity:

```javascript
// hooks/useBackendConnection.js
const BACKEND_URL = localStorage.getItem('backend_url') || 'http://192.168.1.100:8000';
const HEALTH_INTERVAL_MS = 5000;  // check every 5 seconds

export function useBackendConnection() {
  const [status, setStatus] = useState('CHECKING');  // CHECKING | ONLINE | OFFLINE
  const [lastSeen, setLastSeen] = useState(null);
  const wsRef = useRef(null);

  // Polling health check
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/health`, { signal: AbortSignal.timeout(3000) });
        if (res.ok) {
          if (status !== 'ONLINE') {
            setStatus('ONLINE');
            setLastSeen(new Date());
            connectWebSocket();  // auto-reconnect WS when backend comes back
            showToast('🟢 Backend connected — live data loading', 'success');
          }
        } else {
          setStatus('OFFLINE');
        }
      } catch {
        if (status === 'ONLINE') {
          setStatus('OFFLINE');
          showToast('🔴 Backend disconnected — showing cached data', 'warning');
        }
      }
    }, HEALTH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [status]);
  
  return { status, lastSeen };
}
```

### 2.5 Offline Mode Behavior

When backend is offline, the app:
1. Shows a **prominent banner**: `🔴 Backend Offline — Showing Cached Data`
2. Still displays all historical trades, backtest results, and analytics from cache
3. All interactive elements are disabled except Settings and Journal viewing
4. Continues polling for backend — auto-reconnects silently when found
5. Shows last-seen time: `Last connected: 3 minutes ago`

---

## Part 3: Push Notification System

### 3.1 Architecture (Web Push + VAPID)

```
Backend (FastAPI)                 Browser (Service Worker)
      │                                     │
      │  VAPID Private Key                  │  VAPID Public Key
      │                                     │
      │──── Push Server (browser vendor) ───│
      │     (Google FCM / Apple APNs /      │
      │      Mozilla AutoPush)              │
      │                                     │
      └─────── Encrypted Payload ──────────►│
                                            │ Wakes SW even when tab closed
                                            │ Shows OS-native notification
```

**VAPID keys** (generated once, stored in backend):
```bash
# Generate on server first time:
pip install pywebpush
python -c "from pywebpush import webpush; print(webpush.generate_vapid_keys())"
```

### 3.2 Backend Push Service

```python
# backend/services/push_notifications.py
from pywebpush import webpush, WebPushException
import json

class PushNotificationService:
    def __init__(self, vapid_private_key: str, vapid_claims: dict):
        self.private_key = vapid_private_key
        self.claims = vapid_claims

    async def send_notification(
        self,
        subscription: dict,      # stored user push subscription
        title: str,
        body: str,
        icon: str = "/icons/icon-192.png",
        tag: str = "algoedge",   # groups/replaces notifications of same tag
        data: dict = None,
        urgency: str = "high",   # "very-low" | "low" | "normal" | "high"
    ):
        payload = json.dumps({
            "title": title,
            "body": body,
            "icon": icon,
            "tag": tag,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        })
        try:
            webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=self.private_key,
                vapid_claims=self.claims,
                headers={"Urgency": urgency},
            )
        except WebPushException as e:
            if "410" in str(e):  # Subscription expired
                await self.remove_subscription(subscription["endpoint"])
```

### 3.3 Notification Types & Triggers

| Notification | Trigger | Urgency | Tag |
|-------------|---------|---------|-----|
| 🟢 Trade Opened | New position placed | `high` | `trade-open` |
| 🔴 Trade Closed (TP hit) | TP1/TP2/TP3 hit | `high` | `trade-close-tp` |
| ⚠️ Trade Closed (SL hit) | Stop loss hit | `high` | `trade-close-sl` |
| 🎯 Trade BE Applied | Break-even triggered | `normal` | `trade-be` |
| 📉 Daily Loss Limit | Circuit breaker hit | `high` | `risk-alert` |
| ⚠️ Consecutive Losses | Streak breaker | `high` | `risk-alert` |
| 🔍 Signal Detected | New SMC signal | `normal` | `signal` |
| 🔌 Backend Online | Backend reconnected | `normal` | `system` |
| 📊 Daily Summary | End of day report | `low` | `report` |
| 🤖 LLM Analysis Ready | AI analysis complete | `normal` | `llm-analysis` |

### 3.4 Service Worker Push Handler (`sw.js`)

```javascript
// public/sw.js (generated by Workbox, extended manually)
self.addEventListener('push', (event) => {
  if (!event.data) return;
  
  const payload = event.data.json();
  
  const options = {
    body:    payload.body,
    icon:    payload.icon || '/icons/icon-192.png',
    badge:   '/icons/badge-96.png',
    tag:     payload.tag || 'algoedge',
    data:    payload.data,
    vibrate: [200, 100, 200],   // vibration pattern (mobile)
    actions: getActionsForType(payload.data?.type),
    timestamp: payload.timestamp,
    requireInteraction: ['trade-open', 'trade-close-sl', 'risk-alert'].includes(payload.tag),
  };
  
  event.waitUntil(
    self.registration.showNotification(payload.title, options)
  );
});

// Handle notification click → open/focus the app
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = getUrlForNotificationType(event.notification.data?.type);
  
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((windowClients) => {
        const existing = windowClients.find(c => c.url.includes(url));
        if (existing) return existing.focus();
        return clients.openWindow(url);
      })
  );
});

function getActionsForType(type) {
  if (type === 'signal') return [
    { action: 'view', title: '👀 View Signal', icon: '/icons/action-view.png' },
    { action: 'dismiss', title: '✖ Dismiss' },
  ];
  if (type === 'trade_closed') return [
    { action: 'journal', title: '📓 View Trade', icon: '/icons/action-journal.png' },
  ];
  return [];
}
```

### 3.5 iOS Installation Notice

iOS requires the PWA to be installed to Home Screen before push notifications work:

```jsx
// components/IOSInstallPrompt.jsx
export function IOSInstallPrompt() {
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches;
  
  if (!isIOS || isStandalone) return null;
  
  return (
    <div className="ios-install-banner">
      <p>📲 To receive trade alerts on iPhone:</p>
      <ol>
        <li>Tap the Share button (↑) in Safari</li>
        <li>Select <strong>Add to Home Screen</strong></li>
        <li>Open AlgoEdge from your home screen</li>
        <li>Allow notifications when prompted</li>
      </ol>
    </div>
  );
}
```

---

## Part 4: LLM Integration System

### 4.1 Supported Providers

| Provider | Models Available | Best For |
|----------|-----------------|----------|
| **Anthropic Claude** | Sonnet 4.6 (default), Haiku 4.5 (fast) | Deep analysis, nuanced explanations |
| **OpenAI** | GPT-4o (default), GPT-4o-mini (fast) | General analysis, fast responses |
| **Google Gemini** | Gemini 1.5 Pro (default), Flash (fast) | Large context, cost-effective |

User selects provider + model in Settings. API key stored securely (AES-256 encrypted in PostgreSQL).

### 4.2 LLM Settings Panel (UI)

```
┌─── AI ANALYSIS SETTINGS ──────────────────────────────────────────────────┐
│                                                                             │
│  Primary AI Provider:  ◉ Claude  ○ OpenAI  ○ Gemini                        │
│  Model:                ◉ Sonnet 4.6 (Recommended)  ○ Haiku 4.5 (Faster)   │
│                                                                             │
│  API Keys:                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ Anthropic Claude:  [sk-ant-•••••••••••••••••]  [Test] [Clear]       │  │
│  │ OpenAI:            [sk-•••••••••••••••••••••]  [Test] [Clear]       │  │
│  │ Google Gemini:     [AIza•••••••••••••••••••]  [Test] [Clear]        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  Auto-Analysis:                                                             │
│  ☑ Analyze each live trade automatically when closed                        │
│  ☑ Generate weekly summary report                                           │
│  ☐ Analyze each backtest when saved                                         │
│                                                                             │
│  Notification: ☑ Notify me when AI analysis is ready                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 LLM Backend Service

```python
# backend/services/llm_service.py

from dataclasses import dataclass
from typing import Optional, Literal
import anthropic
from openai import AsyncOpenAI
import google.generativeai as genai

@dataclass
class LLMAnalysisRequest:
    context_type: Literal["single_trade", "trade_series", "backtest_summary", "live_session"]
    trade_data: dict
    user_question: Optional[str] = None   # user's custom question

class LLMService:
    def __init__(self, provider: str, api_key: str, model: str):
        self.provider = provider
        self.model    = model
        self._init_client(provider, api_key)

    def _init_client(self, provider: str, api_key: str):
        if provider == "claude":
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
        elif provider == "openai":
            self.client = AsyncOpenAI(api_key=api_key)
        elif provider == "gemini":
            genai.configure(api_key=api_key)
            self.client = genai.GenerativeModel(self.model)

    async def analyze(self, request: LLMAnalysisRequest) -> str:
        system = self._build_system_prompt(request.context_type)
        user   = self._build_user_prompt(request)

        if self.provider == "claude":
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text

        elif self.provider == "openai":
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                max_tokens=2000,
            )
            return response.choices[0].message.content

        elif self.provider == "gemini":
            response = await self.client.generate_content_async(f"{system}\n\n{user}")
            return response.text

    def _build_system_prompt(self, context_type: str) -> str:
        return """You are an expert algorithmic trading analyst specializing in Smart Money Concepts (SMC) and institutional trading strategies. You analyze trade data objectively and provide actionable, data-driven insights.

Your analysis should:
- Be specific to the data provided (mention exact prices, times, R-multiples)
- Identify what went well and what could be improved
- Reference SMC concepts (BOS, ChoCH, OB, FVG, liquidity) in your explanations
- Provide specific, actionable recommendations
- Be concise but thorough (target 200–400 words per analysis)
- Flag any risk management issues clearly
- Avoid generic advice — every insight must be grounded in the actual trade data"""

    def _build_user_prompt(self, request: LLMAnalysisRequest) -> str:
        data = request.trade_data

        if request.context_type == "single_trade":
            prompt = f"""Analyze this completed trade:

**Trade Summary:**
- Symbol: {data.get('symbol')}  |  Direction: {data.get('direction')}
- Entry: {data.get('entry_price')} at {data.get('entry_time')}
- Exit: {data.get('exit_price')} at {data.get('exit_time')}
- Exit Reason: {data.get('exit_reason')} (TP{data.get('tp_level_hit', '?')} / SL / Trail)
- Stop Loss: {data.get('stop_loss')}
- TP1: {data.get('tp1')}  TP2: {data.get('tp2')}  TP3: {data.get('tp3', 'N/A')}

**Results:**
- Realized P&L: {data.get('pnl')} ({data.get('pnl_r', '?')}R)
- Planned RR: 1:{data.get('planned_rr')}  |  Realized RR: 1:{data.get('realized_rr')}
- Duration: {data.get('duration_hours')} hours
- Break-Even Applied: {data.get('be_applied', 'No')}
- Trailing Stop Used: {data.get('trail_method', 'None')}

**SMC Context:**
- HTF Bias: {data.get('htf_bias')}
- Signal Type: {data.get('signal_type')}  (OB zone: {data.get('ob_zone')})
- FVG Confluence: {data.get('has_fvg', False)}
- Liquidity Sweep Confirmed: {data.get('liquidity_swept', False)}
- Session: {data.get('session')}
- Confluence Score: {data.get('confluence_score')}/100

**Risk Metrics:**
- MAE (Max Adverse Excursion): {data.get('mae_pips')} pips
- MFE (Max Favorable Excursion): {data.get('mfe_pips')} pips

Please provide:
1. **Entry Quality Analysis** — Was the setup valid? What SMC confluences were present/missing?
2. **Trade Management Review** — Did BE/trailing perform optimally? Could exits be improved?
3. **Key Lesson** — One specific, actionable takeaway from this trade.
4. **Risk Assessment** — Any risk management concerns?"""

        elif request.context_type == "backtest_summary":
            prompt = f"""Analyze this backtest summary and identify patterns:

**Backtest: {data.get('symbol')} — {data.get('strategy')} — {data.get('period')}**
- Total Trades: {data.get('total_trades')}  |  Win Rate: {data.get('win_rate')}%
- Profit Factor: {data.get('profit_factor')}  |  Sharpe Ratio: {data.get('sharpe_ratio')}
- Total P&L: {data.get('total_pnl')} ({data.get('total_pnl_r')}R)
- Max Drawdown: {data.get('max_drawdown_pct')}%  |  Duration: {data.get('max_drawdown_days')} days
- TP1 hit rate: {data.get('tp1_rate')}%  |  TP2: {data.get('tp2_rate')}%  |  TP3: {data.get('tp3_rate')}%  |  SL: {data.get('sl_rate')}%
- Best Trade: +{data.get('best_trade_r')}R  |  Worst: {data.get('worst_trade_r')}R
- Max Consecutive Losses: {data.get('max_consec_losses')}
- London Win Rate: {data.get('london_wr')}%  |  NY: {data.get('ny_wr')}%

**Parameters Used:** {data.get('params_summary')}

Please provide:
1. **Overall Assessment** — Is this strategy viable? What does the data say?
2. **Strength Analysis** — What's working well (sessions, setups, exit methods)?
3. **Weakness Identification** — Where is the strategy losing edge?
4. **Parameter Recommendations** — What specific parameters would you adjust and why?
5. **Risk Management Feedback** — Are the current TP/SL/trailing settings optimal?"""

        elif request.context_type == "trade_series":
            prompt = f"""Analyze this series of {data.get('count')} recent trades and identify patterns:

{data.get('trades_summary')}

Overall stats: Win rate {data.get('win_rate')}%, 
{data.get('consecutive_losses')} recent consecutive losses,
Best performing session: {data.get('best_session')},
Worst symbol: {data.get('worst_symbol')}

Please identify:
1. **Patterns** — What patterns do you see across these trades?
2. **Session Performance** — Which sessions are performing best/worst?
3. **Entry Quality Trend** — Are confluence scores correlating with outcomes?
4. **Recommendations** — What specific changes would improve results?"""

        if request.user_question:
            prompt += f"\n\n**Additional question from trader:** {request.user_question}"

        return prompt
```

### 4.4 Trade Analysis UI — Journal Integration

**On the Trade Journal page**, every trade row has an AI analysis button:

```
┌─── TRADE DETAIL: EURUSD BUY — June 14, 2026 ──────────────────────────────┐
│                                                                             │
│  Entry: 1.08504  |  TP1: 1.08989 (hit ✅)  |  TP2: 1.09295 (hit ✅)       │
│  TP3: 1.09601 (trailing stopped at 1.09401)  |  SL: 1.08100              │
│  Duration: 14h 32m  |  P&L: +$284.50  |  RR Realized: 1:7.1  |  3.2R     │
│  Confluence Score: 87/100  |  Session: London Kill Zone                    │
│                                                                             │
│  ┌── Entry Snapshot ──────────────┐  ┌── Exit Snapshot ────────────────┐  │
│  │  [Chart Image]                 │  │  [Chart Image]                  │  │
│  └────────────────────────────────┘  └─────────────────────────────────┘  │
│                                                                             │
│  ┌── AI ANALYSIS ──────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  [🤖 Analyze with Claude ▼]  [Ask a Question...]                     │  │
│  │                                                                       │  │
│  │  ┌─ Analysis Result ────────────────────────────────────────────┐   │  │
│  │  │  **Entry Quality: Excellent (87/100)**                        │   │  │
│  │  │                                                               │   │  │
│  │  │  This BUY on EURUSD was a textbook SMC setup. The H4         │   │  │
│  │  │  bullish bias was confirmed with two consecutive BOS...       │   │  │
│  │  │  [Read more ▼]                                                │   │  │
│  │  └──────────────────────────────────────────────────────────────┘   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.5 Backtest LLM Analysis Flow

```
Backtest completes
        │
        ▼
  Save Results? ──No──► Discard
        │ Yes
        ▼
  Saved to DB
        │
        ▼
  ┌─────────────────────────────────┐
  │ Would you like AI analysis?     │
  │                                 │
  │ [🤖 Analyze with Claude]         │
  │ [Skip for now]                  │
  └─────────────────────────────────┘
        │ Analyze
        ▼
  LLM analyses aggregate stats + worst trades
        │
        ▼
  Push notification: "📊 Backtest analysis ready"
        │
        ▼
  Viewable in Backtest History alongside results
```

### 4.6 API Key Security

```python
# API keys encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256)
from cryptography.fernet import Fernet

class APIKeyStore:
    def __init__(self, master_key: bytes):
        self.cipher = Fernet(master_key)

    def store_key(self, provider: str, api_key: str, user_id: str):
        encrypted = self.cipher.encrypt(api_key.encode())
        # Store encrypted bytes in DB — never store plaintext
        db.execute(
            "INSERT OR REPLACE INTO api_keys (user_id, provider, encrypted_key) VALUES (?,?,?)",
            (user_id, provider, encrypted)
        )

    def retrieve_key(self, provider: str, user_id: str) -> str:
        row = db.fetchone("SELECT encrypted_key FROM api_keys WHERE user_id=? AND provider=?",
                          (user_id, provider))
        if not row:
            raise ValueError(f"No API key configured for {provider}")
        return self.cipher.decrypt(row[0]).decode()
```

---

## Part 5: Frontend Page Structure (Updated)

```
/ Dashboard          ← Live chart + positions + risk status widget
/ Journal            ← All trades (live + backtest), expandable rows, AI analysis
/ Backtester         ← Run backtests, configure params, view/save results
/ Analytics          ← Equity curves, metrics, heatmaps, session breakdown
/ Signals            ← All signals generated, acted/skipped with reason
/ Settings
  ├── Strategy       ← Symbol selection, timeframes, confluence threshold
  ├── Risk           ← Full risk control panel (TP/SL/trail/circuit breakers)
  ├── AI Models      ← LLM provider selection + API keys
  ├── Notifications  ← Push notification preferences
  └── Connection     ← Backend URL + MT5 account configuration
```

---

*Version 1.0 | AlgoEdge Frontend PWA & LLM Integration Specification | June 2026*
