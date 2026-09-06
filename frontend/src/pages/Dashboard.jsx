import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TrendingUp, TrendingDown, DollarSign, Target, Shield, Activity, AlertTriangle, Play, Square, Eye, Loader2, Terminal, Trash2 } from 'lucide-react';
import { useConnectionStore, useRiskStore, useAuthStore } from '../store';
import { getDashboardData, getChartData, startBot, stopBot, getBotLogs, forceCloseAll, getLiveAccount } from '../services/api';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';

// ── Category color mapping for activity log ───────────────────────────────
const CATEGORY_COLORS = {
  BOT: '#3fb68b',
  SCAN: '#58a6ff',
  SIGNAL: '#f0883e',
  DATA: '#8b949e',
  STRATEGY: '#bc8cff',
  BACKTEST: '#d29922',
  CONFIG: '#79c0ff',
  SYSTEM: '#8b949e',
  ERROR: '#f85149',
  WARN: '#d29922',
};

function getCategoryColor(evt) {
  if (evt.level === 'ERROR') return CATEGORY_COLORS.ERROR;
  if (evt.level === 'WARN') return CATEGORY_COLORS.WARN;
  if (evt.level === 'SIGNAL') return CATEGORY_COLORS.SIGNAL;
  return CATEGORY_COLORS[evt.category] || CATEGORY_COLORS.SYSTEM;
}

/**
 * The connected MT5 account, its live balance, and what the configured risk
 * percentage resolves to in money on THAT balance.
 *
 * Nothing on the dashboard previously showed the account balance at all
 * (`/broker/status` returns only a masked login and the server name), so after
 * switching MT5 logins there was no way to tell whether "1.8% risk" meant 1.8%
 * of the account now connected or of the previous, larger one. It also surfaces
 * a circuit-breaker pause with its reason, because a halted bot otherwise just
 * looks idle.
 */
function LiveAccountPanel() {
  const { data: acct } = useQuery({
    queryKey: ['live-account'],
    queryFn: () => getLiveAccount().then(r => r.data),
    refetchInterval: 10000,
  });

  if (!acct) return null;

  const money = (v) => (v == null ? '—' : `${acct.currency || '$'}${Number(v).toFixed(2)}`);
  const risk = acct.risk || {};
  const cb = acct.circuit_breaker;
  const mismatch = acct.account_matches_config === false;

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-header">
        <span className="card-title"><DollarSign size={14} /> Live MT5 Account</span>
        <span className={`badge ${acct.connected ? 'badge-green' : 'badge-red'}`}>
          {acct.connected ? `#${acct.login}` : 'Disconnected'}
        </span>
      </div>

      {mismatch && (
        <div className="offline-banner" style={{ marginBottom: 10 }}>
          <AlertTriangle size={16} />
          <span>
            The terminal is logged into #{acct.login} but this app is configured for
            #{acct.configured_account}. Risk is being sized against the account above.
          </span>
        </div>
      )}

      <div className="metrics-grid" style={{ marginBottom: 0 }}>
        <MetricCard label="Balance" value={money(acct.balance)} icon={DollarSign} subtext={acct.server || ''} />
        <MetricCard
          label="Equity"
          value={money(acct.equity)}
          color={acct.equity >= acct.balance ? 'green' : 'red'}
          icon={Activity}
          subtext={acct.balance ? `${(((acct.equity - acct.balance) / acct.balance) * 100).toFixed(2)}% floating` : ''}
        />
        <MetricCard
          label="Risk / Trade"
          value={risk.risk_per_trade_amount != null ? money(risk.risk_per_trade_amount) : '—'}
          icon={Shield}
          subtext={`${risk.risk_per_trade_pct ?? '—'}% of ${risk.sizing_base_label || 'balance'}`}
        />
        <MetricCard
          label="Daily Loss Limit"
          value={risk.max_daily_drawdown_amount != null ? money(risk.max_daily_drawdown_amount) : '—'}
          color="red"
          icon={TrendingDown}
          subtext={`${risk.max_daily_drawdown_pct ?? '—'}% — used ${cb ? money(Math.min(0, cb.daily_pnl)) : '—'}`}
        />
      </div>

      {cb?.is_paused && (
        <div className="offline-banner" style={{ marginTop: 12 }}>
          <AlertTriangle size={16} />
          <span>Trading paused: {cb.pause_reason}</span>
        </div>
      )}
      {cb && cb.account_id != null && acct.login != null && cb.account_id !== acct.login && (
        <div className="offline-banner" style={{ marginTop: 12 }}>
          <AlertTriangle size={16} />
          <span>
            Risk state still belongs to account #{cb.account_id}. Reset it in
            Settings → Broker so this account starts clean.
          </span>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, color = '', subtext = '', icon: Icon }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{Icon && <Icon size={12} style={{ marginRight: 4, display: 'inline' }} />}{label}</div>
      <div className={`metric-value ${color}`}>{value}</div>
      {subtext && <div className="metric-subtext">{subtext}</div>}
    </div>
  );
}

function LiveChart({ symbol = 'XAUUSD', timeframe = 'M15' }) {
  const chartRef = useRef(null);
  const containerRef = useRef(null);
  const seriesRef = useRef(null);

  const { data: chartData } = useQuery({
    queryKey: ['chart', symbol, timeframe],
    queryFn: () => getChartData(symbol, timeframe, 200).then(r => r.data),
    staleTime: 60000,
  });

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 400,
      layout: {
        background: { type: ColorType.Solid, color: '#161b22' },
        textColor: '#8b949e',
        fontFamily: 'Inter',
      },
      grid: {
        vertLines: { color: '#21283640' },
        horzLines: { color: '#21283640' },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#3fb68b',
      downColor: '#f85149',
      borderDownColor: '#f85149',
      borderUpColor: '#3fb68b',
      wickDownColor: '#f85149',
      wickUpColor: '#3fb68b',
    });

    chartRef.current = chart;
    seriesRef.current = candleSeries;

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.remove(); };
  }, []);

  useEffect(() => {
    if (seriesRef.current && chartData?.candles?.length) {
      const mapped = chartData.candles
        .map(c => {
          let t = c.time;
          if (typeof t === 'string') t = Math.floor(new Date(t).getTime() / 1000);
          return { time: t, open: c.open, high: c.high, low: c.low, close: c.close };
        })
        .sort((a, b) => a.time - b.time)
        .filter((c, i, arr) => i === 0 || c.time > arr[i - 1].time);
      if (mapped.length > 0) {
        seriesRef.current.setData(mapped);
        if (chartRef.current) chartRef.current.timeScale().fitContent();
      }
    }
  }, [chartData]);

  return <div ref={containerRef} className="chart-container" />;
}

function PositionCard({ position }) {
  const { trade, sub_positions } = position;
  if (!trade) return null;
  const isLong = trade.direction === 'BUY';
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className={`badge ${isLong ? 'badge-green' : 'badge-red'}`}>
            {isLong ? '▲ LONG' : '▼ SHORT'}
          </span>
          <strong>{trade.symbol}</strong>
        </div>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>@ {trade.entry_price}</span>
      </div>
      <div style={{ display: 'flex', gap: 16, fontSize: '0.8rem', color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
        <span>SL: {trade.stop_loss}</span>
        <span>Vol: {trade.volume}</span>
        {sub_positions?.map((sp, i) => (
          <span key={i} className={sp.status === 'CLOSED' ? 'badge badge-green' : 'badge badge-blue'}>
            TP{sp.tp_level}: {sp.take_profit} {sp.be_applied ? '(BE)' : ''}
          </span>
        ))}
      </div>
    </div>
  );
}

function BotControl() {
  const { status: connStatus } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const queryClient = useQueryClient();
  const [startError, setStartError] = useState(null);

  const { data: dashboardData } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => getDashboardData().then(r => r.data),
    refetchInterval: 5000,
    enabled: connStatus === 'ONLINE' && isAuthenticated,
  });

  const botStatus = dashboardData?.bot;
  const userConfig = dashboardData?.config;
  const brokerStatus = dashboardData?.broker;

  // Get symbols from config, or defaults
  const configSymbols = userConfig?.config?.watched_symbols || userConfig?.config?.symbols || ['XAUUSD', 'XAGUSD', 'XPTUSD', 'EURUSD', 'GBPUSD', 'USOIL', 'ETHUSD', 'GBPJPY'];

  const startMutation = useMutation({
    mutationFn: () => startBot({ symbols: configSymbols, scan_interval: 60 }),
    onSuccess: () => {
      setStartError(null);
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    },
    onError: (err) => {
      const detail = err?.response?.data?.detail;
      if (detail?.missing_brokers) {
        setStartError(detail.missing_brokers);
      } else {
        setStartError([typeof detail === 'string' ? detail : err.message || 'Failed to start bot']);
      }
    },
  });

  const stopMutation = useMutation({
    mutationFn: () => stopBot(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  });

  const isRunning = botStatus?.running === true;
  const isPending = startMutation.isPending || stopMutation.isPending;
  const hasStandard = brokerStatus?.standard?.configured;

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div className="card-header">
        <span className="card-title">Bot Control</span>
        <span className={`badge ${isRunning ? 'badge-green' : 'badge-red'}`}>
          {isRunning ? '● Running' : '○ Stopped'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button
          className="btn btn-primary btn-sm"
          onClick={() => startMutation.mutate()}
          disabled={isRunning || isPending || connStatus !== 'ONLINE'}
        >
          {startMutation.isPending ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
          Start Bot
        </button>
        <button
          className="btn btn-danger btn-sm"
          onClick={() => stopMutation.mutate()}
          disabled={!isRunning || isPending}
        >
          {stopMutation.isPending ? <Loader2 size={14} className="spin" /> : <Square size={14} />}
          Stop Bot
        </button>
      </div>

      {/* Broker connection status */}
      {brokerStatus && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 12, fontSize: '0.78rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: hasStandard ? 'var(--green)' : 'var(--red)', display: 'inline-block' }} />
            <span>MT5 {hasStandard ? `(${brokerStatus.standard.server || 'Connected'})` : '(Not configured)'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          </div>
        </div>
      )}

      {/* Start error with broker details */}
      {startError && (
        <div style={{ background: 'rgba(248,81,73,0.1)', border: '1px solid var(--red)', borderRadius: 'var(--radius-xs)', padding: '8px 12px', marginBottom: 12 }}>
          <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--red)', marginBottom: 4 }}>
            <AlertTriangle size={14} style={{ display: 'inline', marginRight: 4, verticalAlign: 'middle' }} />
            Cannot start bot
          </div>
          {startError.map((msg, i) => (
            <div key={i} style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', padding: '2px 0' }}>• {msg}</div>
          ))}
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>
            Go to Settings → Broker to configure your MT5 credentials.
          </div>
        </div>
      )}

      {botStatus && (
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          <div><strong>Symbols:</strong> {botStatus.symbols?.length ? botStatus.symbols.join(', ') : configSymbols.join(', ')}</div>
          {botStatus.last_scan && <div><strong>Last Scan:</strong> {new Date(botStatus.last_scan).toLocaleString()}</div>}
          {botStatus.total_signals_today != null && <div><strong>Signals Today:</strong> {botStatus.total_signals_today}</div>}
        </div>
      )}
    </div>
  );
}

// ── System-wide Activity Log ────────────────────────────────────────────────
function ActivityLog() {
  const { status: connStatus } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const logContainerRef = useRef(null);
  const [liveEvents, setLiveEvents] = useState([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState('ALL');

  // Poll backend logs (catches events from before WS connected)
  const { data: botLogs } = useQuery({
    queryKey: ['botLogs'],
    queryFn: () => getBotLogs(100).then(r => r.data),
    refetchInterval: 3000,
    enabled: connStatus === 'ONLINE' && isAuthenticated,
  });

  // Listen for real-time WebSocket events
  useEffect(() => {
    const handler = (e) => {
      const data = e.detail;
      if (data?.type === 'activity_log' && data?.event) {
        setLiveEvents(prev => {
          const next = [data.event, ...prev];
          // Keep max 300 in memory
          return next.slice(0, 300);
        });
      }
    };
    window.addEventListener('ws-message', handler);
    return () => window.removeEventListener('ws-message', handler);
  }, []);

  // Merge polled logs with live WS events, dedup by time+message
  const mergedEvents = useMemo(() => {
    const polled = botLogs?.events || [];
    const all = [...liveEvents, ...polled];
    // Dedup by time+message
    const seen = new Set();
    const deduped = [];
    for (const evt of all) {
      const key = `${evt.time}|${evt.message}`;
      if (!seen.has(key)) {
        seen.add(key);
        deduped.push(evt);
      }
    }
    // Sort newest first
    deduped.sort((a, b) => (b.time || '').localeCompare(a.time || ''));

    // Apply filter
    if (filter === 'ALL') return deduped;
    return deduped.filter(evt => {
      if (filter === 'ERRORS') return evt.level === 'ERROR' || evt.level === 'WARN';
      if (filter === 'SIGNALS') return evt.level === 'SIGNAL' || evt.category === 'SIGNAL';
      return (evt.category || '').toUpperCase() === filter;
    });
  }, [botLogs, liveEvents, filter]);

  // Auto-scroll to top when new events arrive
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = 0;
    }
  }, [mergedEvents.length, autoScroll]);

  const clearLive = () => setLiveEvents([]);

  const categories = ['ALL', 'BOT', 'SCAN', 'SIGNALS', 'BACKTEST', 'CONFIG', 'ERRORS'];

  return (
    <div className="card" style={{ marginTop: 20 }}>
      <div className="card-header">
        <span className="card-title">
          <Terminal size={12} style={{ marginRight: 4, display: 'inline' }} />
          System Activity Log
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            {mergedEvents.length} events
          </span>
          <button className="btn btn-secondary btn-sm" onClick={clearLive} title="Clear live events">
            <Trash2 size={10} />
          </button>
        </div>
      </div>

      {/* Filter tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 12, flexWrap: 'wrap' }}>
        {categories.map(cat => (
          <button
            key={cat}
            className={`btn btn-sm ${filter === cat ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setFilter(cat)}
            style={{ padding: '2px 8px', fontSize: '0.65rem' }}
          >
            {cat === 'ERRORS' ? '⚠ Errors' : cat === 'SIGNALS' ? '🎯 Signals' : cat}
          </button>
        ))}
      </div>

      {/* Log entries — terminal-style */}
      <div
        ref={logContainerRef}
        style={{
          maxHeight: 320,
          overflow: 'auto',
          background: '#0d1117',
          borderRadius: 'var(--radius-xs)',
          padding: '8px 12px',
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
          fontSize: '0.72rem',
          lineHeight: 1.7,
          border: '1px solid var(--border)',
        }}
      >
        {mergedEvents.length ? mergedEvents.map((evt, i) => {
          const color = getCategoryColor(evt);
          const time = evt.time ? new Date(evt.time).toLocaleTimeString() : '';
          const cat = evt.category || evt.level || 'SYS';
          return (
            <div
              key={`${evt.time}-${i}`}
              style={{
                padding: '2px 0',
                borderBottom: '1px solid #21262d',
                display: 'flex',
                gap: 8,
                alignItems: 'flex-start',
                opacity: evt.level === 'ERROR' ? 1 : 0.9,
              }}
            >
              <span style={{ color: '#484f58', flexShrink: 0, minWidth: 65 }}>{time}</span>
              <span style={{
                color,
                fontWeight: 600,
                flexShrink: 0,
                minWidth: 65,
                textTransform: 'uppercase',
                fontSize: '0.65rem',
              }}>
                [{cat}]
              </span>
              <span style={{
                color: evt.level === 'ERROR' ? '#f85149' : evt.level === 'SIGNAL' ? '#f0883e' : '#c9d1d9',
                wordBreak: 'break-word',
              }}>
                {evt.message}
              </span>
            </div>
          );
        }) : (
          <div style={{ padding: 20, textAlign: 'center', color: '#484f58' }}>
            <Terminal size={20} style={{ marginBottom: 8, opacity: 0.3 }} />
            <div>No activity yet. Start the bot or run a backtest to see live logs.</div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { status } = useConnectionStore();
  const { setStats } = useRiskStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const { data: dashboardData } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => getDashboardData().then(r => r.data),
    refetchInterval: 5000,
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  const statsData = dashboardData?.stats;
  const userConfig = dashboardData?.config;
  const positionsData = dashboardData?.positions;
  const compoundingData = dashboardData?.compounding;
  const propFirmStatus = dashboardData?.prop_firm_status;
  const mt5Sync = dashboardData?.mt5_sync;

  const configSymbols = userConfig?.config?.watched_symbols || userConfig?.config?.symbols || ['XAUUSD', 'XAGUSD', 'XPTUSD', 'EURUSD', 'GBPUSD', 'USOIL', 'ETHUSD', 'GBPJPY'];

  useEffect(() => {
    if (statsData) setStats(statsData);
  }, [statsData, setStats]);

  const [livePositions, setLivePositions] = useState({});
  const queryClient = useQueryClient();
  useEffect(() => {
    const handler = (e) => {
      if (e.detail?.type === 'trade_update') queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      if (e.detail?.type === 'live_mt5_positions') {
        const nextLive = {};
        e.detail.data.forEach(p => nextLive[p.ticket] = p);
        setLivePositions(nextLive);
      }
    };
    window.addEventListener('ws-message', handler);
    return () => window.removeEventListener('ws-message', handler);
  }, [queryClient]);

  const s = statsData || {};

  return (
    <>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2>Dashboard</h2>
          <p>Live trading overview and performance metrics</p>
        </div>
        {mt5Sync && (
          <div className={`badge ${mt5Sync.status === 'error' ? 'badge-red' : 'badge-green'}`} title={mt5Sync.reason || `Synced ${mt5Sync.synced_positions} positions`}>
            {mt5Sync.status === 'ok' ? '✓ MT5 Synced' : '! MT5 Sync Error'}
          </div>
        )}
      </div>

      {status === 'OFFLINE' && (
        <div className="offline-banner">
          <AlertTriangle size={18} />
          <span>Backend Offline — Showing cached data</span>
        </div>
      )}

      <LiveAccountPanel />

      <div className="metrics-grid">
        <MetricCard label="Total P&L" value={`$${(s.total_pnl || 0).toFixed(2)}`} color={s.total_pnl >= 0 ? 'green' : 'red'} icon={DollarSign} subtext={`${s.total_trades || 0} trades`} />
        <MetricCard label="Win Rate" value={`${((s.win_rate || 0) * 100).toFixed(1)}%`} color={s.win_rate >= 0.55 ? 'green' : 'yellow'} icon={Target} />
        <MetricCard label="Profit Factor" value={(s.profit_factor || 0).toFixed(2)} color={s.profit_factor >= 1.5 ? 'green' : 'yellow'} icon={TrendingUp} />
        <MetricCard label="Sharpe Ratio" value={(s.sharpe_ratio || 0).toFixed(2)} color="blue" icon={Activity} />
        {/* Capital basis divides by the STARTING balance, so a grown account
            can read >100% without ever nearing a blow-up. Show the
            peak-relative figure alongside it rather than leaving one number
            that looks catastrophic on a profitable account. */}
        <MetricCard
          label="Max Drawdown (of capital)"
          value={`${((s.max_drawdown_pct || s.max_drawdown || 0) * 100).toFixed(1)}%`}
          subtext={s.max_drawdown_pct_of_peak != null ? `${(s.max_drawdown_pct_of_peak * 100).toFixed(1)}% of peak equity` : ''}
          color="red"
          icon={TrendingDown}
        />
        <MetricCard label="TP1 Hit Rate" value={`${((s.tp1_hit_rate || 0) * 100).toFixed(0)}%`} color="green" icon={Shield} />
      </div>

      <div className="grid-2">
        <div>
          <div className="card">
            <div className="card-header">
              <span className="card-title">Live Chart</span>
              <span className="badge badge-green">{configSymbols[0] || 'XAUUSD'}</span>
            </div>
            <LiveChart symbol={configSymbols[0] || 'XAUUSD'} timeframe="M15" />
          </div>
        </div>

        <div>
          <BotControl />

          {compoundingData?.enabled && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div className="card-header">
                <span className="card-title">Compounding Progress</span>
                <span className="badge badge-green">Step {compoundingData.current_step}</span>
              </div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                <p>Risk per trade: <strong style={{ color: 'var(--green)' }}>${compoundingData.risk_amount}</strong></p>
                <p>Wins at level: {compoundingData.total_wins_at_level || 0}</p>
              </div>
              <div className="progress-bar">
                <div className="progress-fill" style={{ width: `${Math.min(100, (compoundingData.consecutive_wins || 0) * 25)}%` }} />
              </div>
            </div>
          )}

          {propFirmStatus && userConfig?.config?.prop_firm?.account_mode === 'prop_firm' && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div className="card-header">
                <span className="card-title">Prop Firm Challenge</span>
                {propFirmStatus.is_paused ? (
                  <span className="badge badge-red">Paused</span>
                ) : (
                  <span className="badge badge-green">Active</span>
                )}
              </div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span>EOD Baseline:</span>
                  <strong style={{ color: 'var(--text-primary)' }}>${propFirmStatus.eod_baseline?.toFixed(2)}</strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span>High Water Mark:</span>
                  <strong style={{ color: 'var(--text-primary)' }}>${propFirmStatus.high_water_mark?.toFixed(2)}</strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span>Total Profit:</span>
                  <strong style={{ color: propFirmStatus.total_profit >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    ${propFirmStatus.total_profit?.toFixed(2)}
                  </strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span>Trading Days:</span>
                  <strong style={{ color: 'var(--text-primary)' }}>{propFirmStatus.active_trading_days || 0}</strong>
                </div>
                {propFirmStatus.pause_reason && (
                  <div style={{ marginTop: 8, padding: 8, background: 'rgba(248,81,73,0.1)', color: 'var(--red)', borderRadius: 4, fontSize: '0.75rem' }}>
                    <strong>Halted:</strong> {propFirmStatus.pause_reason}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Full-width Open Positions */}
      <div className="card" style={{ marginTop: 32, marginBottom: 20 }}>
        <div className="card-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="card-title">Open Positions</span>
            <span className="badge badge-blue">{positionsData?.length || 0}</span>
          </div>
          {positionsData?.length > 0 && (
            <button
              className="btn btn-sm"
              style={{ background: 'rgba(248,81,73,0.15)', color: 'var(--red)', border: '1px solid rgba(248,81,73,0.3)', fontSize: '0.75rem' }}
              onClick={async () => {
                if (window.confirm('Force close ALL open positions in the database? This does NOT close MT5 positions — it only clears the dashboard.')) {
                  try {
                    await forceCloseAll();
                    queryClient.invalidateQueries({ queryKey: ['dashboard'] });
                  } catch (err) {
                    alert('Failed: ' + (err?.response?.data?.detail || err.message));
                  }
                }
              }}
            >
              <Trash2 size={12} /> Force Close All
            </button>
          )}
        </div>
        {positionsData?.length ? (
          <div style={{ overflowX: 'auto' }}>
            <div className="table-wrapper">
              <table>
                <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Direction</th>
                  <th>Volume</th>
                  <th>Entry</th>
                  <th>Current</th>
                  <th>SL</th>
                  <th>Live P&L</th>
                  <th>AI Analysis</th>
                </tr>
              </thead>
              <tbody>
                {positionsData.map((p, i) => {
                  const { trade, sub_positions } = p;
                  if (!trade) return null;
                  const isLong = trade.direction === 'BUY';
                  
                  // Link with live WebSocket data if available
                  let currentPrice = null;
                  let currentSl = trade.stop_loss;
                  let livePnl = sub_positions?.reduce((sum, sp) => sum + (sp.pnl || 0), 0) || 0;
                  let hasLive = false;
                  
                  if (livePositions) {
                    const liveSp = sub_positions?.map(sp => livePositions[sp.mt5_ticket]).filter(Boolean);
                    if (liveSp && liveSp.length > 0) {
                      hasLive = true;
                      currentPrice = liveSp[0].price_current;
                      currentSl = liveSp[0].sl;
                      livePnl += liveSp.reduce((sum, ls) => sum + (ls.profit || 0), 0);
                    }
                  }
                  
                  if (!hasLive && livePnl === 0) livePnl = null;

                  return (
                    <tr key={i}>
                      <td><strong>{trade.symbol}</strong></td>
                      <td>
                        <span className={`badge ${isLong ? 'badge-green' : 'badge-red'}`}>
                          {isLong ? '▲ LONG' : '▼ SHORT'}
                        </span>
                      </td>
                      <td>{trade.volume}</td>
                      <td>{trade.entry_price}</td>
                      <td style={{ color: currentPrice ? (isLong ? (currentPrice > trade.entry_price ? 'var(--green)' : 'var(--red)') : (currentPrice < trade.entry_price ? 'var(--green)' : 'var(--red)')) : 'inherit' }}>
                        {currentPrice ? currentPrice.toFixed(5) : '—'}
                      </td>
                      <td>{currentSl}</td>
                      <td style={{ color: livePnl !== null ? (livePnl >= 0 ? 'var(--green)' : 'var(--red)') : 'inherit', fontWeight: 'bold' }}>
                        {livePnl !== null ? `$${livePnl.toFixed(2)}` : '—'}
                      </td>
                      <td style={{ maxWidth: 250, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontSize: '0.75rem', color: 'var(--text-secondary)' }} title={trade.llm_analysis}>
                        {trade.llm_analysis || '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          </div>
        ) : (
          <div className="empty-state">
            <Activity />
            <h3>No Open Positions</h3>
            <p>Start the bot to begin scanning for trade setups</p>
          </div>
        )}
      </div>


      {/* Full-width System Activity Log */}
      <ActivityLog />
    </>
  );
}
