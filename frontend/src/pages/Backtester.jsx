import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FlaskConical, Play, Trash2, Eye, Save, X, ChevronDown, ChevronRight, Loader2, Clock, Target, Shield, Terminal, Settings2 } from 'lucide-react';
import { runBacktest, getBacktests, deleteBacktest, getBacktest, saveBacktest, getBotLogs } from '../services/api';
import { useConnectionStore, useAuthStore } from '../store';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts';

const SYMBOLS = [
  'XAUUSD','EURUSD','GBPUSD','USDJPY','US30','BTCUSD',
  'Volatility 10 Index','Volatility 25 Index','Volatility 50 Index',
  'Volatility 75 Index','Volatility 100 Index',
];

function fmt(v) {
  if (!v) return '—';
  if (typeof v === 'string' && v.includes('T')) return new Date(v).toLocaleString('en-GB',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});
  if (typeof v === 'number' && v > 1e9) return new Date(v*1000).toLocaleString('en-GB',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});
  return String(v);
}
function fmtDur(m) { if (!m||m<=0) return '—'; if (m<60) return `${m.toFixed(0)}m`; if (m<1440) return `${(m/60).toFixed(1)}h`; return `${(m/1440).toFixed(1)}d`; }

function ProgressBar({ progress }) {
  if (!progress || progress.pct === undefined) return null;
  return (<div style={{marginTop:12}}>
    <div style={{display:'flex',justifyContent:'space-between',fontSize:'0.75rem',color:'var(--text-secondary)',marginBottom:4}}>
      <span>{progress.message||progress.stage}</span><span>{progress.pct}%</span>
    </div>
    <div style={{height:6,borderRadius:3,background:'var(--bg-tertiary)',overflow:'hidden'}}>
      <div style={{height:'100%',width:`${progress.pct}%`,background:'linear-gradient(90deg,var(--blue),var(--green))',borderRadius:3,transition:'width 0.3s ease'}}/>
    </div>
  </div>);
}

function LiveLogPanel() {
  const ref = useRef(null);
  const { status } = useConnectionStore();
  const isAuth = useAuthStore(s=>s.isAuthenticated);
  const [events, setEvents] = useState([]);
  const { data: logs } = useQuery({ queryKey:['btLogs'], queryFn:()=>getBotLogs(50).then(r=>r.data), refetchInterval:2000, enabled: status==='ONLINE'&&isAuth });

  useEffect(()=>{
    const h = e => { try { const m=JSON.parse(e.data); if(m.type==='activity_log'&&m.event) setEvents(p=>[m.event,...p].slice(0,200)); } catch{} };
    if(window._algoEdgeWs){ window._algoEdgeWs.addEventListener('message',h); return ()=>window._algoEdgeWs?.removeEventListener('message',h); }
  },[]);

  const merged = useCallback(()=>{
    const all=[...events,...(logs?.events||[])]; const seen=new Set(); const out=[];
    for(const e of all){ const k=`${e.time}|${e.message}`; if(!seen.has(k)){seen.add(k);out.push(e);} }
    out.sort((a,b)=>(b.time||'').localeCompare(a.time||'')); return out.filter(e=>e.category==='BACKTEST'||e.category==='SIGNAL'||e.category==='TRADE'||e.level==='ERROR');
  },[logs,events])();

  return (<div style={{maxHeight:340,overflow:'auto',background:'#0d1117',borderRadius:'var(--radius-xs)',padding:'8px 12px',fontFamily:"'JetBrains Mono',monospace",fontSize:'0.72rem',lineHeight:1.7,border:'1px solid var(--border)'}}>
    {merged.length ? merged.map((e,i)=>(
      <div key={`${e.time}-${i}`} style={{padding:'2px 0',borderBottom:'1px solid #21262d',display:'flex',gap:8}}>
        <span style={{color:'#484f58',flexShrink:0,minWidth:65}}>{e.time?new Date(e.time).toLocaleTimeString():''}</span>
        <span style={{color:e.level==='ERROR'?'#f85149':e.category==='SIGNAL'?'#f0883e':'#58a6ff',fontWeight:600,flexShrink:0,minWidth:65,textTransform:'uppercase',fontSize:'0.65rem'}}>[{e.category||e.level}]</span>
        <span style={{color:e.level==='ERROR'?'#f85149':'#c9d1d9',wordBreak:'break-word'}}>{e.message}</span>
      </div>
    )) : (<div style={{padding:20,textAlign:'center',color:'#484f58'}}><Terminal size={20} style={{marginBottom:8,opacity:0.3}}/><div>Waiting for backtest events...</div></div>)}
  </div>);
}

function GroupedTradeRow({ group, index }) {
  const [open, setOpen] = useState(false);
  const pnl = group.combined_pnl || 0;
  return (<>
    <tr onClick={()=>setOpen(!open)} style={{cursor:'pointer',background:pnl>=0?'rgba(63,182,139,0.04)':'rgba(248,81,73,0.04)'}}>
      <td>{open?<ChevronDown size={12}/>:<ChevronRight size={12}/>}</td>
      <td>{index+1}</td>
      <td><strong>{group.symbol}</strong></td>
      <td><span className={`badge ${group.direction==='BUY'?'badge-green':'badge-red'}`}>{group.direction==='BUY'?'▲ BUY':'▼ SELL'}</span></td>
      <td>{typeof group.entry_price==='number'?group.entry_price.toFixed(2):group.entry_price}</td>
      <td>{group.tp_count} TPs ({group.tp_wins}W/{group.tp_losses}L)</td>
      <td style={{color:pnl>=0?'var(--green)':'var(--red)',fontWeight:600}}>${pnl.toFixed(2)}</td>
      <td>{fmtDur(group.duration_minutes)}</td>
      <td><span className="badge badge-blue">{group.entry_session||'—'}</span>{group.exit_session&&group.exit_session!==group.entry_session&&<span className="badge badge-blue" style={{marginLeft:4}}>→{group.exit_session}</span>}</td>
    </tr>
    {open && group.sub_trades?.map((t,j)=>(
      <tr key={j} style={{background:'var(--bg-tertiary)',fontSize:'0.8rem'}}>
        <td></td><td></td>
        <td colSpan={2}><span className={`badge ${t.exit_reason?.startsWith('TP')?'badge-green':'badge-red'}`}>TP{t.tp_level} → {t.exit_reason}</span></td>
        <td>{typeof t.entry_price==='number'?t.entry_price.toFixed(2):t.entry_price} → {typeof t.exit_price==='number'?t.exit_price.toFixed(2):t.exit_price}</td>
        <td>Vol: {t.volume} | BE: {t.be_applied?'✓':'✗'}</td>
        <td style={{color:(t.pnl||0)>=0?'var(--green)':'var(--red)'}}>${(t.pnl||0).toFixed(2)}</td>
        <td>{fmtDur(t.duration_minutes)}</td>
        <td>{t.session||'—'}</td>
      </tr>
    ))}
    {open && group.sub_trades?.[0]?.entry_confirmations && (
      <tr><td colSpan={9} style={{padding:0,border:'none'}}>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,padding:'12px 16px',background:'var(--bg-tertiary)',margin:'4px 8px',borderRadius:'var(--radius-xs)'}}>
          <div>
            <div style={{fontSize:'0.7rem',textTransform:'uppercase',color:'var(--text-muted)',marginBottom:6,fontWeight:600}}>Entry Confirmations</div>
            {(group.sub_trades[0].entry_confirmations||[]).map((c,i)=><div key={i} style={{fontSize:'0.75rem',color:'var(--text-secondary)',padding:'2px 0',borderBottom:'1px solid var(--border)'}}>{c}</div>)}
          </div>
          <div>
            <div style={{fontSize:'0.7rem',textTransform:'uppercase',color:'var(--text-muted)',marginBottom:6,fontWeight:600}}>Exit Confirmations</div>
            {(group.sub_trades[0].exit_confirmations||[]).map((c,i)=><div key={i} style={{fontSize:'0.75rem',color:'var(--text-secondary)',padding:'2px 0',borderBottom:'1px solid var(--border)'}}>{c}</div>)}
          </div>
        </div>
      </td></tr>
    )}
  </>);
}

function BacktestResults({ result, onSave, onDismiss, isSaving }) {
  const report = result.report||{};
  const grouped = result.grouped_trades || [];
  const eqData = (result.equity_curve||[]).map((v,i)=>({bar:i,equity:v}));
  const sessionData = [{session:'London',rate:(report.london_win_rate||0)*100},{session:'NY',rate:(report.ny_win_rate||0)*100},{session:'London/NY',rate:(report.overlap_win_rate||0)*100}];

  return (<div className="card" style={{marginTop:20}}>
    <div className="card-header">
      <span className="card-title">Results — {result.total_signals||0} signals, {result.total_trades||0} sub-positions</span>
      <div style={{display:'flex',gap:8}}>
        <button className="btn btn-primary btn-sm" onClick={onSave} disabled={isSaving}><Save size={14}/> {isSaving?'Saving...':'Save'}</button>
        <button className="btn btn-danger btn-sm" onClick={onDismiss}><X size={14}/> Dismiss</button>
      </div>
    </div>
    {result.invalid_signals>0&&<div style={{fontSize:'0.8rem',color:'var(--yellow)',marginBottom:8}}><Shield size={12} style={{display:'inline',marginRight:4}}/>{result.invalid_signals} signals rejected (invalid SL/TP)</div>}
    <div className="metrics-grid" style={{marginBottom:16}}>
      <div className="metric-card"><div className="metric-label">Final Balance</div><div className={`metric-value ${result.final_balance>=result.initial_balance?'green':'red'}`}>${result.final_balance?.toFixed(2)}</div></div>
      <div className="metric-card"><div className="metric-label">Net P&L</div><div className={`metric-value ${(result.final_balance-result.initial_balance)>=0?'green':'red'}`}>${(result.final_balance-result.initial_balance).toFixed(2)}</div></div>
      <div className="metric-card"><div className="metric-label">Win Rate</div><div className={`metric-value ${report.win_rate>=0.55?'green':'yellow'}`}>{((report.win_rate||0)*100).toFixed(1)}%</div></div>
      <div className="metric-card"><div className="metric-label">Profit Factor</div><div className="metric-value green">{(report.profit_factor||0).toFixed(2)}</div></div>
      <div className="metric-card"><div className="metric-label">Sharpe</div><div className="metric-value blue">{(report.sharpe_ratio||0).toFixed(2)}</div></div>
      <div className="metric-card"><div className="metric-label">Max DD</div><div className="metric-value red">{((report.max_drawdown_pct||0)*100).toFixed(1)}%</div></div>
      <div className="metric-card"><div className="metric-label">Expectancy (R)</div><div className="metric-value blue">{(report.expectancy_r||0).toFixed(2)}</div></div>
      <div className="metric-card"><div className="metric-label">Sortino</div><div className="metric-value blue">{(report.sortino_ratio||0).toFixed(2)}</div></div>
    </div>
    <div className="grid-2" style={{marginBottom:16}}>
      {eqData.length>1&&(<div className="card" style={{padding:12}}><h4 style={{marginBottom:8}}>Equity Curve</h4>
        <ResponsiveContainer width="100%" height={200}><AreaChart data={eqData}><XAxis dataKey="bar" hide/><YAxis domain={['auto','auto']} fontSize={10}/><Tooltip formatter={v=>`$${v.toFixed(2)}`}/><Area type="monotone" dataKey="equity" stroke="#3fb68b" fill="#3fb68b20" strokeWidth={2}/></AreaChart></ResponsiveContainer>
      </div>)}
      <div className="card" style={{padding:12}}><h4 style={{marginBottom:8}}>Win Rate by Session</h4>
        <ResponsiveContainer width="100%" height={200}><BarChart data={sessionData}><XAxis dataKey="session" fontSize={11}/><YAxis domain={[0,100]} fontSize={10}/><Tooltip formatter={v=>`${v.toFixed(1)}%`}/><Bar dataKey="rate" fill="#58a6ff" radius={[4,4,0,0]}/></BarChart></ResponsiveContainer>
      </div>
    </div>
    {grouped.length>0&&(<>
      <div className="card-header"><span className="card-title">Trade Groups ({grouped.length})</span></div>
      <div className="table-wrapper" style={{maxHeight:500,overflow:'auto'}}>
        <table><thead><tr><th style={{width:24}}></th><th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>TPs</th><th>Net P&L</th><th>Duration</th><th>Session</th></tr></thead>
          <tbody>{grouped.map((g,i)=><GroupedTradeRow key={g.group_id||i} group={g} index={i}/>)}</tbody>
        </table>
      </div>
    </>)}
  </div>);
}

export default function Backtester() {
  const queryClient = useQueryClient();
  const { status } = useConnectionStore();
  const isAuth = useAuthStore(s=>s.isAuthenticated);
  const [form, setForm] = useState({
    symbol:'XAUUSD',timeframe:'H1',initial_balance:10000,
    risk_per_trade_pct:1.0,min_rr:3.0,tp_count:3,
    tp1_rr:3.0,tp2_rr:5.0,tp3_rr:7.0,tp4_rr:10.0,tp5_rr:15.0,
    be_trigger_rr:1.0,be_buffer_pips:2.0,
    session_filter_enabled:true,
    start_date:'',end_date:'',candle_count:5000,
  });
  const [result, setResult] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [progress, setProgress] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(()=>{
    const h=e=>{try{const m=JSON.parse(e.data);if(m.type==='backtest_progress'){setProgress(m);if(m.stage==='complete')setTimeout(()=>setProgress(null),2000);}}catch{}};
    if(window._algoEdgeWs){window._algoEdgeWs.addEventListener('message',h);return()=>window._algoEdgeWs?.removeEventListener('message',h);}
  },[]);

  const { data: backtests, refetch } = useQuery({ queryKey:['backtests'], queryFn:()=>getBacktests().then(r=>r.data), enabled:status==='ONLINE'&&isAuth });

  const mutation = useMutation({
    mutationFn: () => runBacktest({
      symbol:form.symbol, timeframe:form.timeframe, initial_balance:form.initial_balance,
      start_date:form.start_date||undefined, end_date:form.end_date||undefined, candle_count:form.candle_count,
      tp_count:form.tp_count, session_filter_enabled:form.session_filter_enabled,
      risk_per_trade_pct:form.risk_per_trade_pct, min_rr:form.min_rr,
      tp1_rr:form.tp1_rr, tp2_rr:form.tp2_rr, tp3_rr:form.tp3_rr, tp4_rr:form.tp4_rr, tp5_rr:form.tp5_rr,
      be_trigger_rr:form.be_trigger_rr, be_buffer_pips:form.be_buffer_pips,
      risk_config:{},
    }),
    onSuccess: res => { setResult(res.data); setProgress(null); },
    onError: () => setProgress(null),
  });

  const handleSave = async () => {
    if (!result) return;
    setIsSaving(true);
    try {
      await saveBacktest(result.backtest_id, { backtest_data:{...result,strategy_id:'SMC_v1',symbol:form.symbol,risk_config:form}, save_mode:'FULL' });
      setResult(null);
      queryClient.invalidateQueries({ queryKey: ['backtests'] });
      refetch();
    } finally { setIsSaving(false); }
  };
  const handleDismiss = () => setResult(null);
  const handleDelete = async id => { await deleteBacktest(id); refetch(); };
  const handleView = async id => { const res = await getBacktest(id); setResult({...res.data.run, trades:res.data.trades, equity_curve:res.data.equity_curve, report:res.data.run, grouped_trades:res.data.grouped_trades||[]}); };

  const isRunning = mutation.isPending;

  return (<>
    <div className="page-header"><h2><FlaskConical size={22} style={{display:'inline',marginRight:8}}/>Backtester</h2><p>Test strategies with independent risk parameters</p></div>
    <div className="grid-2">
      <div className="card">
        <div className="card-header"><span className="card-title">Configuration</span></div>
        <div style={{display:'grid',gap:14}}>
          <div><label>Symbol</label><select value={form.symbol} onChange={e=>setForm({...form,symbol:e.target.value})}>{SYMBOLS.map(s=><option key={s} value={s}>{s}</option>)}</select></div>
          <div><label>Timeframe</label><select value={form.timeframe} onChange={e=>setForm({...form,timeframe:e.target.value})}>{['M5','M15','H1','H4','D1'].map(tf=><option key={tf} value={tf}>{tf}</option>)}</select></div>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
            <div><label>Start Date</label><input type="date" value={form.start_date} onChange={e=>setForm({...form,start_date:e.target.value})}/></div>
            <div><label>End Date</label><input type="date" value={form.end_date} onChange={e=>setForm({...form,end_date:e.target.value})}/></div>
          </div>
          <div style={{fontSize:'0.7rem',color:'var(--text-muted)',marginTop:-8}}>Leave empty to use last N candles.</div>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
            <div><label>Candles (if no dates)</label><input type="number" value={form.candle_count} onChange={e=>setForm({...form,candle_count:+e.target.value})} min={100} max={50000}/></div>
            <div><label>Balance ($)</label><input type="number" value={form.initial_balance} onChange={e=>setForm({...form,initial_balance:+e.target.value})}/></div>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:12}}>
            <div><label>Risk %</label><input type="number" step="0.1" value={form.risk_per_trade_pct} onChange={e=>setForm({...form,risk_per_trade_pct:+e.target.value})}/></div>
            <div><label>Min R:R</label><input type="number" step="0.5" value={form.min_rr} onChange={e=>setForm({...form,min_rr:+e.target.value})}/></div>
            <div><label>TP Count</label><select value={form.tp_count} onChange={e=>setForm({...form,tp_count:+e.target.value})}>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n} TP{n>1?'s':''}</option>)}</select></div>
          </div>
          <div><label style={{display:'flex',alignItems:'center',gap:6,cursor:'pointer'}}><input type="checkbox" checked={form.session_filter_enabled} onChange={e=>setForm({...form,session_filter_enabled:e.target.checked})} style={{width:14,height:14}}/> Session Filter ({form.session_filter_enabled?'London/NY only':'All sessions'})</label></div>

          {/* Advanced Parameters Toggle */}
          <button className="btn btn-secondary btn-sm" onClick={()=>setShowAdvanced(!showAdvanced)} style={{width:'100%'}}><Settings2 size={14}/> {showAdvanced?'Hide':'Show'} Advanced Parameters</button>
          {showAdvanced && (<div style={{display:'grid',gap:12,padding:12,background:'var(--bg-tertiary)',borderRadius:'var(--radius-xs)'}}>
            <div style={{fontSize:'0.75rem',fontWeight:600,color:'var(--text-secondary)'}}>TP R:R Multipliers</div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:8}}>
              {[1,2,3,4,5].filter(n=>n<=form.tp_count).map(n=>(
                <div key={n}><label style={{fontSize:'0.7rem'}}>TP{n}</label><input type="number" step="0.5" value={form[`tp${n}_rr`]} onChange={e=>setForm({...form,[`tp${n}_rr`]:+e.target.value})} style={{fontSize:'0.8rem'}}/></div>
              ))}
            </div>
            <div style={{fontSize:'0.75rem',fontWeight:600,color:'var(--text-secondary)',marginTop:4}}>Break-Even Settings</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
              <div><label style={{fontSize:'0.7rem'}}>BE Trigger (R)</label><input type="number" step="0.1" value={form.be_trigger_rr} onChange={e=>setForm({...form,be_trigger_rr:+e.target.value})}/></div>
              <div><label style={{fontSize:'0.7rem'}}>BE Buffer (pips)</label><input type="number" step="0.5" value={form.be_buffer_pips} onChange={e=>setForm({...form,be_buffer_pips:+e.target.value})}/></div>
            </div>
          </div>)}

          <button className="btn btn-primary" onClick={()=>mutation.mutate()} disabled={isRunning||status!=='ONLINE'} style={{width:'100%'}}>
            {isRunning?<><Loader2 size={16} className="spin"/> Running...</>:<><Play size={16}/> Run Backtest</>}
          </button>
          {(isRunning||progress)&&<ProgressBar progress={progress}/>}
          {mutation.isError&&<div style={{color:'var(--red)',fontSize:'0.8rem'}}>Error: {mutation.error?.response?.data?.detail||mutation.error?.message||'Failed'}</div>}
        </div>
      </div>

      {/* Right panel: Live logs while running, saved backtests otherwise */}
      <div className="card">
        {isRunning ? (<>
          <div className="card-header"><span className="card-title"><Terminal size={14} style={{display:'inline',marginRight:6}}/>Live Backtest Logs</span></div>
          <LiveLogPanel />
        </>) : (<>
          <div className="card-header"><span className="card-title">Saved Backtests</span><span className="badge badge-blue">{backtests?.length||0}</span></div>
          <div className="table-wrapper">
            <table><thead><tr><th>Symbol</th><th>Trades</th><th>Win Rate</th><th>P&L</th><th></th></tr></thead>
              <tbody>{backtests?.length ? backtests.map(bt=>(
                <tr key={bt.id}><td><strong>{bt.symbol}</strong></td><td>{bt.total_trades}</td><td>{((bt.win_rate||0)*100).toFixed(0)}%</td>
                  <td style={{color:bt.total_pnl>=0?'var(--green)':'var(--red)'}}>${(bt.total_pnl||0).toFixed(2)}</td>
                  <td><div style={{display:'flex',gap:4}}><button className="btn btn-secondary btn-sm" onClick={()=>handleView(bt.id)}><Eye size={12}/></button><button className="btn btn-danger btn-sm" onClick={()=>handleDelete(bt.id)}><Trash2 size={12}/></button></div></td>
                </tr>
              )) : (<tr><td colSpan={5}><div className="empty-state"><FlaskConical/><h3>No saved backtests</h3></div></td></tr>)}</tbody>
            </table>
          </div>
        </>)}
      </div>
    </div>
    {result && <BacktestResults result={result} onSave={handleSave} onDismiss={handleDismiss} isSaving={isSaving}/>}
  </>);
}
