import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity, Layers, GitCompare, CalendarDays, Waves, AlertTriangle,
  RefreshCw, Zap, DollarSign, BarChart2, Table,
} from 'lucide-react';
import {
  getFundProviders, selectFundProvider, getOrderFlow, getOrderBook,
  getCorrelation, getGex, getEconCalendar,
} from '../services/api';
import AnalyzeButton from '../components/AnalyzeButton';
import { FUNDAMENTALS_HELP } from '../components/FundamentalsHelp';
import FundamentalsChart from '../components/FundamentalsChart';

/**
 * Fundamentals — order flow, depth, correlation, gamma exposure, calendar.
 *
 * Every panel is served through the backend's provider registry, so the vendor
 * behind a capability is a dropdown rather than a deploy. Free sources are the
 * defaults; paid slots are visible but inert until a key exists.
 *
 * Each panel shows the provider that served it, the latency, and the caveat
 * where the data is delayed or derived. That last part is deliberate: CVD here
 * is INFERRED from tick price vs. bid/ask because MT5 CFD ticks carry no
 * aggressor flag, and a proxy displayed as ground truth is how a number ends up
 * trusted more than it deserves.
 */

const TABS = [
  { id: 'orderflow', label: 'Order Flow', icon: Waves, cap: 'order_flow' },
  { id: 'orderbook', label: 'Depth', icon: Layers, cap: 'order_book' },
  { id: 'correlation', label: 'Correlation', icon: GitCompare, cap: 'correlation' },
  { id: 'gex', label: 'Gamma (GEX)', icon: Activity, cap: 'gex' },
  { id: 'calendar', label: 'Calendar', icon: CalendarDays, cap: 'calendar' },
];

function Meta({ result }) {
  if (!result) return null;
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: '0.68rem', color: 'var(--text-muted)', marginBottom: 8 }}>
      <span><b style={{ color: 'var(--blue)' }}>{result.provider}</b></span>
      {result.latency_ms != null && <span>{result.latency_ms} ms</span>}
      {result.fetched_at && <span>{String(result.fetched_at).slice(11, 19)}Z</span>}
      {result.caveat && (
        <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: 'var(--amber)' }}>
          <AlertTriangle size={11} /> {result.caveat}
        </span>
      )}
      {result.error && <span style={{ color: 'var(--red)' }}>{result.error}</span>}
    </div>
  );
}

function ProviderSelect({ capability, catalogue, onChange }) {
  const entry = catalogue?.capabilities?.[capability];
  if (!entry) return null;
  return (
    <select
      className="input input-sm"
      value={entry.selected || ''}
      onChange={e => onChange(capability, e.target.value)}
      style={{ minWidth: 150 }}
      title="Free now, paid later — switching here needs no code change"
    >
      {(entry.options || []).map(o => (
        <option key={o.name} value={o.name} disabled={!o.available}>
          {o.name} · {o.tier}
          {!o.available ? ' (not installed)' : (o.tier === 'paid' && !o.configured ? ' (no key)' : '')}
        </option>
      ))}
    </select>
  );
}

function Stat({ label, value, tone }) {
  return (
    <div style={{ padding: '8px 12px', background: 'var(--bg-tertiary)', borderRadius: 6, minWidth: 118 }}>
      <div style={{ fontSize: '0.64rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</div>
      <div style={{ fontSize: '1.05rem', fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: tone || 'var(--text-primary)' }}>{value}</div>
    </div>
  );
}

/** Horizontal magnitude bar — shared by depth and GEX so they read alike. */
function Bar({ value, max, color, width = 120 }) {
  const pct = max ? Math.min(100, (Math.abs(value) / max) * 100) : 0;
  return (
    <div style={{ width, height: 10, background: 'rgba(255,255,255,0.05)', borderRadius: 2, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color }} />
    </div>
  );
}

export default function Fundamentals() {
  const [tab, setTab] = useState('orderflow');
  const [symbol, setSymbol] = useState('EURUSD');
  const [minutes, setMinutes] = useState(60);
  const [ticker, setTicker] = useState('SPX');
  const [corrSymbols, setCorrSymbols] = useState('EURUSD,GBPUSD,XAUUSD,USDJPY');
  const [autoRefresh, setAutoRefresh] = useState(false);
  // [Phase 14 E.1] Chart / Data view toggle — persisted per-tab so switching
  // tabs doesn't reset the user's preferred view for the tab they return to.
  const [viewMode, setViewMode] = useState({});  // { [tabId]: 'chart' | 'data' }
  const currentView = viewMode[tab] || 'data';

  const { data: providers, refetch: refetchProviders } = useQuery({
    queryKey: ['fund-providers'],
    queryFn: () => getFundProviders().then(r => r.data),
    // Short, because this drives the health strip (latency, error rate, call
    // count). A stale catalogue showed "0 calls" immediately after a fetch,
    // which made the strip look broken rather than merely cached.
    staleTime: 5000,
    refetchInterval: 15000,
  });

  const changeProvider = async (capability, provider) => {
    try {
      await selectFundProvider({ capability, provider });
      refetchProviders();
    } catch { /* the select stays on the old value — catalogue refetch corrects it */ }
  };

  // Order flow and depth move in seconds; correlation and calendar do not.
  // Polling an options chain faster than the exchange disseminates it buys
  // nothing, so the intervals differ per panel rather than sharing one.
  const flowQ = useQuery({
    queryKey: ['ff-orderflow', symbol, minutes],
    queryFn: () => getOrderFlow({ symbol, minutes }).then(r => r.data),
    enabled: tab === 'orderflow',
    refetchInterval: tab === 'orderflow' && autoRefresh ? 5000 : false,
  });
  const bookQ = useQuery({
    queryKey: ['ff-book', symbol],
    queryFn: () => getOrderBook({ symbol }).then(r => r.data),
    enabled: tab === 'orderbook',
    refetchInterval: tab === 'orderbook' && autoRefresh ? 2000 : false,
  });
  const corrQ = useQuery({
    queryKey: ['ff-corr', corrSymbols],
    queryFn: () => getCorrelation({ symbols: corrSymbols }).then(r => r.data),
    enabled: tab === 'correlation',
  });
  const gexQ = useQuery({
    queryKey: ['ff-gex', ticker],
    queryFn: () => getGex({ ticker }).then(r => r.data),
    enabled: tab === 'gex',
  });
  const calQ = useQuery({
    queryKey: ['ff-cal'],
    queryFn: () => getEconCalendar({}).then(r => r.data),
    enabled: tab === 'calendar',
  });

  const active = { orderflow: flowQ, orderbook: bookQ, correlation: corrQ, gex: gexQ, calendar: calQ }[tab];
  const result = active?.data;
  const d = result?.data;
  const cap = TABS.find(t => t.id === tab)?.cap;

  const bubbleMax = useMemo(
    () => Math.max(1, ...((d?.bubbles || []).map(b => b.abs_volume))),
    [d],
  );

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <h1 style={{ display: 'flex', alignItems: 'center', gap: 8, margin: 0 }}>
          <Activity size={22} /> Fundamentals
        </h1>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {cap && <ProviderSelect capability={cap} catalogue={providers?.catalogue} onChange={changeProvider} />}
          <button className={`btn btn-sm ${autoRefresh ? 'btn-green' : 'btn-secondary'}`} onClick={() => setAutoRefresh(a => !a)}>
            <RefreshCw size={12} /> {autoRefresh ? 'Live' : 'Manual'}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => active?.refetch?.()}>
            <RefreshCw size={12} /> Refresh
          </button>
          <AnalyzeButton
            targetType={tab === 'orderflow' ? 'orderflow' : 'fundamentals'}
            targetId={tab === 'orderflow' ? symbol : tab}
            payload={tab === 'orderflow' ? { symbol, flow: d || {} } : { label: tab, ...(d || {}) }}
            compact
            disabled={!d}
          />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 4, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={`btn btn-sm ${tab === id ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setTab(id)}
          >
            <Icon size={13} /> {label}
          </button>
        ))}
        {/* [Phase 14 E.1] Chart / Data toggle */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          <button
            className={`btn btn-sm ${currentView === 'chart' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setViewMode(m => ({ ...m, [tab]: 'chart' }))}
            title="Chart view"
          >
            <BarChart2 size={13} /> Chart
          </button>
          <button
            className={`btn btn-sm ${currentView === 'data' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setViewMode(m => ({ ...m, [tab]: 'data' }))}
            title="Data table view"
          >
            <Table size={13} /> Data
          </button>
        </div>
      </div>

      <div className="card" style={{ padding: 14 }}>
        {/* Panel-specific inputs */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          {(tab === 'orderflow' || tab === 'orderbook') && (
            <>
              <input className="input input-sm" value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} style={{ width: 130 }} placeholder="Symbol" />
              {tab === 'orderflow' && (
                <select className="input input-sm" value={minutes} onChange={e => setMinutes(+e.target.value)}>
                  {[15, 30, 60, 120, 240, 480].map(m => <option key={m} value={m}>{m} min</option>)}
                </select>
              )}
            </>
          )}
          {tab === 'correlation' && (
            <input className="input input-sm" value={corrSymbols} onChange={e => setCorrSymbols(e.target.value.toUpperCase())} style={{ flex: 1, minWidth: 260 }} placeholder="Comma-separated symbols" />
          )}
          {tab === 'gex' && (
            <>
              {/* Options are listed on the INDEX, not on a broker's CFD wrapper,
                  so `SPX500` / `NDX100` / `US Tech 100` have no chain and return
                  nothing. Offering the valid codes as a datalist turns a silent
                  empty result into an obvious choice, while still allowing a
                  single-name ticker to be typed. */}
              <input
                className="input input-sm"
                list="gex-tickers"
                value={ticker}
                onChange={e => setTicker(e.target.value.toUpperCase())}
                style={{ width: 150 }}
                placeholder="SPX / NDX / AAPL"
                title="Index code or single-name ticker — not a broker CFD symbol"
              />
              <datalist id="gex-tickers">
                <option value="SPX">S&amp;P 500 index</option>
                <option value="NDX">Nasdaq 100 index</option>
                <option value="RUT">Russell 2000 index</option>
                <option value="DJI">Dow Jones index</option>
                <option value="VIX">Volatility index</option>
                <option value="SPY">S&amp;P 500 ETF</option>
                <option value="QQQ">Nasdaq 100 ETF</option>
                <option value="IWM">Russell 2000 ETF</option>
              </datalist>
              {/^(SPX500|NDX100|US30|US500|USTEC|GER40|GER30|UK100|JP225)$/i.test(ticker) && (
                <span style={{ fontSize: '0.66rem', color: 'var(--amber)' }}>
                  That is a broker CFD name — try {ticker.toUpperCase().startsWith('NDX') || /USTEC/i.test(ticker) ? 'NDX' : 'SPX'}
                </span>
              )}
            </>
          )}
        </div>

        {/* [Phase 14 Part F] Per-panel explanation. Collapsed by default and
            remembered per panel, so it is there the first time and out of the
            way afterwards. */}
        {(() => {
          const Help = FUNDAMENTALS_HELP[tab];
          return Help ? <Help /> : null;
        })()}

        <Meta result={result} />

        {active?.isFetching && !d && (
          <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-muted)' }}>Fetching…</div>
        )}
        {result && !result.ok && (
          <div style={{ padding: 14, borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--red)', fontSize: '0.82rem' }}>
            {result.error}
          </div>
        )}

        {/* [Phase 14 E.1] Chart view — rendered before the per-panel data blocks;
            the data blocks below are only shown when currentView === 'data'. */}
        {currentView === 'chart' && result && (
          <FundamentalsChart panel={tab} result={result} />
        )}

        {/* ── Order flow — data view ── */}
        {tab === 'orderflow' && d && !d.error && currentView === 'data' && (
          <>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
              <Stat label="CVD" value={d.cvd?.toLocaleString?.() ?? '—'} tone={d.cvd > 0 ? 'var(--green)' : 'var(--red)'} />
              <Stat label="Delta" value={d.delta?.toLocaleString?.() ?? '—'} tone={d.delta > 0 ? 'var(--green)' : 'var(--red)'} />
              <Stat label="Imbalance" value={d.imbalance != null ? `${(d.imbalance * 100).toFixed(1)}%` : '—'} tone={d.imbalance > 0 ? 'var(--green)' : 'var(--red)'} />
              <Stat label="Buy vol" value={d.buy_volume?.toLocaleString?.() ?? '—'} />
              <Stat label="Sell vol" value={d.sell_volume?.toLocaleString?.() ?? '—'} />
              <Stat label="Ticks" value={d.ticks?.toLocaleString?.() ?? '—'} />
            </div>

            {(d.divergence || d.absorption) && (
              <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
                {d.divergence && (
                  <span className="badge badge-amber" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Zap size={11} /> Delta divergence: {d.divergence.type || 'detected'}
                  </span>
                )}
                {d.absorption && (
                  <span className="badge badge-blue" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Layers size={11} /> Absorption at {d.absorption.price ?? '—'}
                  </span>
                )}
              </div>
            )}

            {d.volume_profile?.vpoc != null && (
              <div style={{ marginBottom: 14, fontSize: '0.78rem' }}>
                <b>VPOC</b> {d.volume_profile.vpoc}
                {d.volume_profile.value_area_low != null && (
                  <span style={{ color: 'var(--text-muted)' }}>
                    {'  '}· value area {d.volume_profile.value_area_low} – {d.volume_profile.value_area_high}
                  </span>
                )}
              </div>
            )}

            {d.bubbles?.length > 0 && (
              <>
                <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 6 }}>
                  Signed volume by price ({d.bubbles.length} levels)
                </div>
                <div style={{ maxHeight: 300, overflowY: 'auto' }}>
                  {d.bubbles.slice(0, 60).map((b, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0', fontSize: '0.72rem', fontVariantNumeric: 'tabular-nums' }}>
                      <span style={{ width: 90, color: 'var(--text-muted)' }}>{b.price}</span>
                      <Bar value={b.abs_volume} max={bubbleMax} color={b.side === 'buy' ? 'var(--green)' : 'var(--red)'} width={200} />
                      <span style={{ color: b.side === 'buy' ? 'var(--green)' : 'var(--red)' }}>
                        {b.signed_volume > 0 ? '+' : ''}{b.signed_volume}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
        {tab === 'orderflow' && d?.error && (
          <div style={{ padding: 14, color: 'var(--amber)', fontSize: '0.82rem' }}>{d.error}</div>
        )}

        {/* ── Depth — data view ── */}
        {tab === 'orderbook' && d && currentView === 'data' && (
          <>
            <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
              <Stat label="Bid vol" value={d.bid_volume?.toLocaleString?.() ?? '—'} tone="var(--green)" />
              <Stat label="Ask vol" value={d.ask_volume?.toLocaleString?.() ?? '—'} tone="var(--red)" />
              <Stat label="Imbalance" value={d.imbalance != null ? `${(d.imbalance * 100).toFixed(1)}%` : '—'} tone={d.imbalance > 0 ? 'var(--green)' : 'var(--red)'} />
              <Stat label="Spread" value={d.spread ?? '—'} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {[['Bids', d.bids, 'var(--green)'], ['Asks', d.asks, 'var(--red)']].map(([title, rows, color]) => {
                const max = Math.max(1, ...((rows || []).map(r => r.volume)));
                return (
                  <div key={title}>
                    <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 4 }}>{title}</div>
                    {(rows || []).map((r, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.72rem', padding: '1px 0', fontVariantNumeric: 'tabular-nums' }}>
                        <span style={{ width: 84 }}>{r.price}</span>
                        <Bar value={r.volume} max={max} color={color} width={100} />
                        <span style={{ color: 'var(--text-muted)' }}>{r.volume}</span>
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          </>
        )}

        {/* ── Correlation — data view ── */}
        {tab === 'correlation' && d?.matrix && currentView === 'data' && (
          <>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 8 }}>
              Computed on returns over {d.bars} {d.timeframe} bars — correlating prices instead would
              read near 1.0 for any two drifting series.
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', fontSize: '0.74rem', fontVariantNumeric: 'tabular-nums' }}>
                <thead>
                  <tr>
                    <th style={{ padding: 6 }} />
                    {d.symbols.map(s => <th key={s} style={{ padding: 6, color: 'var(--text-muted)' }}>{s}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {d.symbols.map(a => (
                    <tr key={a}>
                      <td style={{ padding: 6, color: 'var(--text-muted)', fontWeight: 600 }}>{a}</td>
                      {d.symbols.map(b => {
                        const v = d.matrix[a][b];
                        const alpha = Math.min(0.55, Math.abs(v) * 0.55);
                        return (
                          <td key={b} style={{
                            padding: 6, textAlign: 'center',
                            background: a === b ? 'transparent'
                              : `rgba(${v > 0 ? '16,185,129' : '239,68,68'},${alpha})`,
                            borderRadius: 3,
                          }}>
                            {a === b ? '—' : v.toFixed(2)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: 12, fontSize: '0.72rem' }}>
              <b>Most/least correlated pairs</b>
              <div style={{ marginTop: 4, display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(220px,1fr))', gap: 4 }}>
                {(d.pairs || []).slice(0, 12).map((p, i) => (
                  <div key={i} style={{ display: 'flex', gap: 6, color: 'var(--text-muted)' }}>
                    <span>{p.a} / {p.b}</span>
                    <span style={{ marginLeft: 'auto', color: p.corr > 0 ? 'var(--green)' : 'var(--red)' }}>{p.corr.toFixed(3)}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {/* ── GEX — data view ── */}
        {tab === 'gex' && d?.by_strike && currentView === 'data' && (
          <>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
              <Stat label="Spot" value={d.spot ?? '—'} />
              <Stat label="Total GEX" value={d.total_gex != null ? `${(d.total_gex / 1e9).toFixed(2)}B` : '—'} tone={d.total_gex > 0 ? 'var(--green)' : 'var(--red)'} />
              <Stat label="Regime" value={d.regime ?? '—'} tone={d.regime === 'positive' ? 'var(--green)' : 'var(--red)'} />
              <Stat label="Flip strike" value={d.flip_strike ?? '—'} tone="var(--amber)" />
            </div>
            <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginBottom: 12 }}>{d.interpretation}</div>
            {(() => {
              const max = Math.max(1, ...d.by_strike.map(r => Math.abs(r.gex)));
              const near = d.by_strike
                .filter(r => !d.spot || Math.abs(r.strike - d.spot) / d.spot < 0.12)
                .slice(0, 60);
              return (
                <div style={{ maxHeight: 340, overflowY: 'auto' }}>
                  {near.map((r, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.72rem', padding: '1px 0', fontVariantNumeric: 'tabular-nums' }}>
                      <span style={{ width: 74, color: r.strike === d.flip_strike ? 'var(--amber)' : 'var(--text-muted)' }}>{r.strike}</span>
                      <Bar value={r.gex} max={max} color={r.gex > 0 ? 'var(--green)' : 'var(--red)'} width={180} />
                      <span style={{ color: r.gex > 0 ? 'var(--green)' : 'var(--red)' }}>{(r.gex / 1e9).toFixed(2)}B</span>
                    </div>
                  ))}
                </div>
              );
            })()}
          </>
        )}

        {/* ── Calendar — data view ── */}
        {tab === 'calendar' && d?.events && currentView === 'data' && (
          <div style={{ maxHeight: 460, overflowY: 'auto' }}>
            {d.events.slice(0, 120).map((e, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, padding: '4px 0', fontSize: '0.75rem', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <span style={{ width: 130, color: 'var(--text-muted)' }}>{(e.date || '').slice(0, 16).replace('T', ' ')}</span>
                <span style={{ width: 42, fontWeight: 600 }}>{e.country}</span>
                <span style={{
                  width: 56,
                  color: e.impact === 'High' ? 'var(--red)' : e.impact === 'Medium' ? 'var(--amber)' : 'var(--text-muted)',
                }}>{e.impact}</span>
                <span style={{ flex: 1 }}>{e.title}</span>
                <span style={{ width: 60, textAlign: 'right', color: 'var(--text-muted)' }}>{e.forecast || ''}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: 14, padding: 12 }}>
        <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
          <DollarSign size={12} /> Provider health
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))', gap: 8 }}>
          {(providers?.catalogue?.providers || []).map(p => (
            <div key={p.name} style={{ padding: 8, background: 'var(--bg-tertiary)', borderRadius: 6, fontSize: '0.72rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{
                  width: 7, height: 7, borderRadius: '50%',
                  background: !p.available ? 'var(--text-muted)' : (p.configured ? 'var(--green)' : 'var(--amber)'),
                }} />
                <b>{p.name}</b>
                <span className={`badge ${p.tier === 'paid' ? 'badge-amber' : 'badge-green'}`} style={{ fontSize: '0.6rem' }}>{p.tier}</span>
              </div>
              <div style={{ color: 'var(--text-muted)', marginTop: 3, lineHeight: 1.35 }}>{p.note}</div>
              <div style={{ color: 'var(--text-muted)', marginTop: 3, fontVariantNumeric: 'tabular-nums' }}>
                {p.calls} calls
                {p.calls > 0 && ` · ${(p.error_rate * 100).toFixed(0)}% err`}
                {p.last_latency_ms != null && ` · ${Math.round(p.last_latency_ms)} ms`}
              </div>
              {p.last_error && <div style={{ color: 'var(--red)', marginTop: 3 }}>{p.last_error}</div>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
