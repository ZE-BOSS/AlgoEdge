import { useEffect, useRef, useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TrendingUp, TrendingDown, DollarSign, Target, Shield, Activity, AlertTriangle, Play, Square, Eye, Loader2, Terminal, Trash2 } from 'lucide-react';
import { useConnectionStore, useRiskStore, useAuthStore } from '../store';
import { getStats, getPositions, getCompounding, getChartData, getBotStatus, startBot, stopBot, getBotLogs } from '../services/api';
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

function MetricCard({ label, value, color = '', subtext = '', icon: Icon }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{Icon && <Icon size={12} style={{ marginRight: 4, display: 'inline' }} />}{label}</div>
      <div className={`metric-value ${color}`}>{value}</div>
      {subtext && <div className="metric-subtext">{subtext}</div>}
    </div>
  );
}

function LiveChart() {
  const chartRef = useRef(null);
  const containerRef = useRef(null);

  const { data: chartData } = useQuery({
    queryKey: ['chart', 'XAUUSD', 'H1'],
    queryFn: () => getChartData('XAUUSD', 'H1', 200).then(r => r.data),
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

    if (chartData?.candles?.length) {
      const mapped = chartData.candles
        .map(c => {
          let t = c.time;
          if (typeof t === 'string') t = Math.floor(new Date(t).getTime() / 1000);
          return { time: t, open: c.open, high: c.high, low: c.low, close: c.close };
        })
        .sort((a, b) => a.time - b.time)
        .filter((c, i, arr) => i === 0 || c.time > arr[i - 1].time);
      if (mapped.length > 0) {
        candleSeries.setData(mapped);
      }
    }

    chart.timeScale().fitContent();
    chartRef.current = chart;

    const handleResize = () => chart.applyOptions({ width: containerRef.current.clientWidth });
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.remove(); };
  }, [chartData]);

  return <div ref={containerRef} className="chart-container" />;
}

function PositionCard({ position }) {
  const { trade, sub_positions } = position;
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

  const { data: botStatus } = useQuery({
    queryKey: ['botStatus'],
    queryFn: () => getBotStatus().then(r => r.data),
    refetchInterval: 5000,
    enabled: connStatus === 'ONLINE' && isAuthenticated,
  });

  const startMutation = useMutation({
    mutationFn: () => startBot(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['botStatus'] }),
  });

  const stopMutation = useMutation({
    mutationFn: () => stopBot(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['botStatus'] }),
  });

  const isRunning = botStatus?.running === true;
  const isPending = startMutation.isPending || stopMutation.isPending;

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

      {botStatus && (
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          <div><strong>Symbols:</strong> {botStatus.symbols?.join(', ') || 'None configured'}</div>
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
  const mergedEvents = useCallback(() => {
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
  }, [botLogs, liveEvents, filter])();

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
  const { setStats, setCompounding } = useRiskStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const { data: statsData } = useQuery({
    queryKey: ['stats'],
    queryFn: () => getStats().then(r => r.data),
    refetchInterval: 15000,
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  const { data: positionsData } = useQuery({
    queryKey: ['positions'],
    queryFn: () => getPositions().then(r => r.data),
    refetchInterval: 5000,
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  const { data: compoundingData } = useQuery({
    queryKey: ['compounding'],
    queryFn: () => getCompounding().then(r => r.data),
    refetchInterval: 30000,
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  useEffect(() => {
    if (statsData) setStats(statsData);
    if (compoundingData) setCompounding(compoundingData);
  }, [statsData, compoundingData]);

  const s = statsData || {};

  return (
    <>
      <div className="page-header">
        <h2>Dashboard</h2>
        <p>Live trading overview and performance metrics</p>
      </div>

      {status === 'OFFLINE' && (
        <div className="offline-banner">
          <AlertTriangle size={18} />
          <span>Backend Offline — Showing cached data</span>
        </div>
      )}

      <div className="metrics-grid">
        <MetricCard label="Total P&L" value={`$${(s.total_pnl || 0).toFixed(2)}`} color={s.total_pnl >= 0 ? 'green' : 'red'} icon={DollarSign} subtext={`${s.total_trades || 0} trades`} />
        <MetricCard label="Win Rate" value={`${((s.win_rate || 0) * 100).toFixed(1)}%`} color={s.win_rate >= 0.55 ? 'green' : 'yellow'} icon={Target} />
        <MetricCard label="Profit Factor" value={(s.profit_factor || 0).toFixed(2)} color={s.profit_factor >= 1.5 ? 'green' : 'yellow'} icon={TrendingUp} />
        <MetricCard label="Sharpe Ratio" value={(s.sharpe_ratio || 0).toFixed(2)} color="blue" icon={Activity} />
        <MetricCard label="Max Drawdown" value={`${((s.max_drawdown_pct || s.max_drawdown || 0) * 100).toFixed(1)}%`} color="red" icon={TrendingDown} />
        <MetricCard label="TP1 Hit Rate" value={`${((s.tp1_hit_rate || 0) * 100).toFixed(0)}%`} color="green" icon={Shield} />
      </div>

      <div className="grid-2">
        <div>
          <div className="card">
            <div className="card-header">
              <span className="card-title">Live Chart</span>
              <span className="badge badge-green">XAUUSD</span>
            </div>
            <LiveChart />
          </div>
        </div>

        <div>
          <BotControl />

          <div className="card" style={{ marginBottom: 20 }}>
            <div className="card-header">
              <span className="card-title">Open Positions</span>
              <span className="badge badge-blue">{positionsData?.length || 0}</span>
            </div>
            {positionsData?.length ? (
              positionsData.map((p, i) => <PositionCard key={i} position={p} />)
            ) : (
              <div className="empty-state">
                <Activity />
                <h3>No Open Positions</h3>
                <p>Start the bot to begin scanning for trade setups</p>
              </div>
            )}
          </div>

          {compoundingData?.enabled && (
            <div className="card">
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
        </div>
      </div>

      {/* Full-width System Activity Log */}
      <ActivityLog />
    </>
  );
}
