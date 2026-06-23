import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { TrendingUp, TrendingDown, DollarSign, Target, Shield, Activity, AlertTriangle } from 'lucide-react';
import { useConnectionStore, useRiskStore, useAuthStore } from '../store';
import { getStats, getPositions, getCompounding, getChartData } from '../services/api';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';

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
      const mapped = chartData.candles.map(c => {
        // Normalize time: lightweight-charts expects UNIX timestamps (seconds)
        let t = c.time;
        if (typeof t === 'string') {
          t = Math.floor(new Date(t).getTime() / 1000);
        }
        return { time: t, open: c.open, high: c.high, low: c.low, close: c.close };
      });
      candleSeries.setData(mapped);
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
                <p>Waiting for signals...</p>
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
    </>
  );
}
