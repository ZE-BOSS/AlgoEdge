import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FlaskConical, Play, Trash2, Eye, Save, X, ChevronDown, ChevronRight, Loader2, Clock, Target, Shield, Terminal, Settings2, Zap } from 'lucide-react';
import { runBacktest, getBacktests, deleteBacktest, getBacktest, saveBacktest, getBotLogs, getConfig } from '../services/api';
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
    const h = e => { try { const m = e.detail; if(m.type==='activity_log'&&m.event) setEvents(p=>[m.event,...p].slice(0,200)); } catch{} };
    window.addEventListener('ws-message',h);
    return ()=>window.removeEventListener('ws-message',h);
  },[]);

  const merged = useCallback(()=>{
    const all=[...events,...(logs?.events||[])]; const seen=new Set(); const out=[];
    for(const e of all){ const k=`${e.time}|${e.message}`; if(!seen.has(k)){seen.add(k);out.push(e);} }
    out.sort((a,b)=>(b.time||'').localeCompare(a.time||'')); return out.filter(e=>['BACKTEST','SIGNAL','TRADE','SMC'].includes(e.category)||e.level==='ERROR');
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
      <td>{fmt(group.entry_time_iso)}</td>
      <td>{fmt(group.exit_time_iso)}</td>
      <td>{fmtDur(group.duration_minutes)}</td>
      <td>{group.tp_count} TPs ({group.tp_wins}W/{group.tp_losses}L)</td>
      <td style={{color:pnl>=0?'var(--green)':'var(--red)',fontWeight:600}}>${pnl.toFixed(2)}</td>
      <td><span className="badge badge-blue">{group.entry_session||'—'}</span>{group.exit_session&&group.exit_session!==group.entry_session&&<span className="badge badge-blue" style={{marginLeft:4}}>→{group.exit_session}</span>}</td>
    </tr>
    {open && group.sub_trades?.map((t,j)=>(
      <tr key={j} style={{background:'var(--bg-tertiary)',fontSize:'0.8rem'}}>
        <td></td><td></td>
        <td colSpan={2}><span className={`badge ${t.exit_reason?.startsWith('TP')?'badge-green':t.exit_reason==='BE_SL'?'badge-blue':'badge-red'}`}>TP{t.tp_level} → {t.exit_reason}</span></td>
        <td colSpan={2} style={{fontSize:'0.72rem'}}>{fmt(t.entry_time_iso)} → {fmt(t.exit_time_iso)}</td>
        <td>{fmtDur(t.duration_minutes)}</td>
        <td style={{fontSize:'0.72rem'}}>Vol: {t.volume} | BE: {t.be_applied?'✓':'✗'}{t.trail_applied?' | Trail: ✓':''}</td>
        <td style={{fontSize:'0.72rem'}}>MAE: {(t.mae_pips||0).toFixed(1)}p | MFE: {(t.mfe_pips||0).toFixed(1)}p</td>
        <td style={{color:(t.pnl||0)>=0?'var(--green)':'var(--red)'}}>
          ${(t.pnl||0).toFixed(2)}
        </td>
        <td>{t.session||'—'}</td>
      </tr>
    ))}
    {open && group.sub_trades?.[0]?.entry_confirmations && (
      <tr><td colSpan={11} style={{padding:0,border:'none'}}>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1.5fr',gap:16,padding:'12px 16px',background:'var(--bg-tertiary)',margin:'4px 8px',borderRadius:'var(--radius-xs)'}}>
          <div>
            <div style={{fontSize:'0.7rem',textTransform:'uppercase',color:'var(--text-muted)',marginBottom:6,fontWeight:600}}>Entry Confirmations (Score: {group.sub_trades[0].confluence_score || '—'})</div>
            {(group.sub_trades[0].entry_confirmations||[]).map((c,i)=>{
              const isHeader = c.startsWith('═') || c.startsWith('──');
              const isPass = c.startsWith('✓');
              const isFail = c.startsWith('✗');
              const isMixed = c.startsWith('△');
              return <div key={i} style={{
                fontSize: isHeader ? '0.72rem' : '0.75rem',
                fontWeight: isHeader ? 700 : 400,
                color: isHeader ? 'var(--blue)' : isPass ? 'var(--green)' : isFail ? 'var(--text-muted)' : isMixed ? 'var(--yellow)' : 'var(--text-secondary)',
                padding: '3px 0',
                borderBottom: isHeader ? 'none' : '1px solid var(--border)',
                marginTop: isHeader ? 8 : 0,
              }}>{c}</div>;
            })}
          </div>
          <div>
            {group.entry_snapshot_b64 ? (
              <>
                <div style={{fontSize:'0.7rem',textTransform:'uppercase',color:'var(--text-muted)',marginBottom:6,fontWeight:600}}>Entry Snapshot</div>
                <img src={`data:image/png;base64,${group.entry_snapshot_b64}`} alt="Chart" style={{width:'100%',borderRadius:'var(--radius-sm)',border:'1px solid var(--border)'}} />
              </>
            ) : (
              <>
                <div style={{fontSize:'0.7rem',textTransform:'uppercase',color:'var(--text-muted)',marginBottom:6,fontWeight:600}}>Exit Info</div>
                {(group.sub_trades[0].exit_confirmations||[]).map((c,i)=>
                  <div key={i} style={{fontSize:'0.75rem',color:'var(--text-secondary)',padding:'3px 0',borderBottom:'1px solid var(--border)'}}>{c}</div>
                )}
              </>
            )}
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
    <div style={{display:'flex',gap:8,flexWrap:'wrap',marginBottom:16}}>
      {[1,2,3,4,5].map(n=>{const rate=report[`tp${n}_hit_rate`]; return rate!=null && rate>0 ? <div key={n} className="badge badge-green" style={{padding:'4px 10px',fontSize:'0.75rem'}}>TP{n}: {(rate*100).toFixed(0)}%</div> : null;})}
      {report.sl_hit_rate!=null && <div className="badge badge-red" style={{padding:'4px 10px',fontSize:'0.75rem'}}>SL: {(report.sl_hit_rate*100).toFixed(0)}%</div>}
      {report.trail_hit_rate!=null && report.trail_hit_rate>0 && <div className="badge badge-blue" style={{padding:'4px 10px',fontSize:'0.75rem'}}>Trail Exit: {(report.trail_hit_rate*100).toFixed(0)}%</div>}
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
        <table><thead><tr><th style={{width:24}}></th><th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Entry Time</th><th>Exit Time</th><th>Duration</th><th>TPs</th><th>Net P&L</th><th>Session</th></tr></thead>
          <tbody>{grouped.map((g,i)=><GroupedTradeRow key={g.group_id||i} group={g} index={i}/>)}</tbody>
        </table>
      </div>
    </>)}
  </div>);
}

const TRAIL_METHODS = [{v:'ATR_TRAIL',l:'ATR Trail'},{v:'FIXED_PIPS',l:'Fixed Pips'},{v:'STRUCTURE_TRAIL',l:'Structure Trail'},{v:'PCT_TRAIL',l:'% Trail'}];

export default function Backtester() {
  const queryClient = useQueryClient();
  const { status } = useConnectionStore();
  const isAuth = useAuthStore(s=>s.isAuthenticated);
  const STORAGE_KEY = 'algoedge_bt_config';

  const [form, setForm] = useState(() => {
    // Restore last saved config from localStorage
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) return JSON.parse(saved);
    } catch {}
    return {
      symbol:'XAUUSD',timeframe:'H1',initial_balance:10000,
      start_date:'',end_date:'',candle_count:5000,
      confluence_threshold:55,swing_length:5,ob_impulse_ratio:1.5,
      fvg_min_gap_pips:3.0,liq_sweep_min_pips:2.0,max_spread_pips:3.0,
      session_filter_enabled:true,news_filter_enabled:true,
      risk_per_trade_pct:1.0,min_rr:3.0,
      max_daily_consecutive_losses:3,max_weekly_consecutive_losses:5,
      max_consecutive_losses:5,max_concurrent_positions:3,
      tp_count:3,tp1_rr:1.0,tp2_rr:3.0,tp3_rr:5.0,tp4_rr:10.0,tp5_rr:15.0,
      tp_splits:'30,25,20,15,10',
      be_trigger_rr:1.0,be_buffer_pips:2.0,
      trail_method_tp2:'ATR_TRAIL',trail_method_tp3:'STRUCTURE_TRAIL',
      trail_method_tp4:'ATR_TRAIL',trail_method_tp5:'STRUCTURE_TRAIL',
      atr_trail_multiplier:1.5,trail_pips:15,
      compounding_enabled:false,
    };
  });
  const [result, setResult] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [progress, setProgress] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);

  // Auto-save config to localStorage on every change
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(form)); } catch {}
  }, [form]);

  // Load defaults from user's live config (only on first load if no saved config)
  const { data: userCfg } = useQuery({ queryKey:['config'], queryFn:()=>getConfig().then(r=>r.data), enabled:status==='ONLINE'&&isAuth });
  useEffect(()=>{
    if(userCfg?.config && !configLoaded && !localStorage.getItem(STORAGE_KEY)){
      const c = userCfg.config;
      setForm(prev=>({...prev,...Object.fromEntries(Object.entries(c).filter(([k])=>k in prev))}));
    }
    if(userCfg) setConfigLoaded(true);
  },[userCfg,configLoaded]);

  useEffect(()=>{
    const h=e=>{
      try{
        const m = e.detail; // 'ws-message' event from useBackendConnection.js passes parsed data in detail
        if(m.type==='backtest_progress'){
          setProgress(m);
          if(m.stage==='complete'){
            if (m.result) {
               setResult(m.result);
               if (m.result.run_logs) setEvents(m.result.run_logs);
            }
            setTimeout(()=>setProgress(null),2000);
          }
        }
      }catch{}
    };
    window.addEventListener('ws-message',h);
    return()=>window.removeEventListener('ws-message',h);
  },[]);

  const { data: backtests, refetch } = useQuery({ queryKey:['backtests'], queryFn:()=>getBacktests().then(r=>r.data), enabled:status==='ONLINE'&&isAuth });

  const mutation = useMutation({
    mutationFn: () => {
      setProgress({stage: 'starting', message: 'Initializing backtest request...', pct: 0});
      return runBacktest({...form, start_date:form.start_date||undefined, end_date:form.end_date||undefined, risk_config:{}});
    },
    onSuccess: res => { 
      // Do nothing, we wait for websocket 'complete' stage
    },
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
  const handleView = async id => { 
      const res = await getBacktest(id); 
      setResult({...res.data.run, trades:res.data.trades, equity_curve:res.data.equity_curve, report:res.data.run, grouped_trades:res.data.grouped_trades||[]}); 
      if(res.data.run_logs) setEvents(res.data.run_logs);
  };

  const isRunning = mutation.isPending || (progress !== null && progress.stage !== 'complete');
  const u = (k,v) => setForm({...form,[k]:v});

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
          <div style={{display:'flex',gap:12}}>
            <label style={{display:'flex',alignItems:'center',gap:6,cursor:'pointer',flex:1}}><input type="checkbox" checked={form.session_filter_enabled} onChange={e=>u('session_filter_enabled',e.target.checked)} style={{width:14,height:14}}/> Session Filter</label>
            <label style={{display:'flex',alignItems:'center',gap:6,cursor:'pointer',flex:1}}><input type="checkbox" checked={form.news_filter_enabled} onChange={e=>u('news_filter_enabled',e.target.checked)} style={{width:14,height:14}}/> News Filter</label>
            <label style={{display:'flex',alignItems:'center',gap:6,cursor:'pointer',flex:1}}><input type="checkbox" checked={form.compounding_enabled} onChange={e=>u('compounding_enabled',e.target.checked)} style={{width:14,height:14}}/> Compounding</label>
          </div>

          <button className="btn btn-secondary btn-sm" onClick={()=>setShowAdvanced(!showAdvanced)} style={{width:'100%'}}><Settings2 size={14}/> {showAdvanced?'Hide':'Show'} Advanced Parameters</button>
          {showAdvanced && (<div style={{display:'grid',gap:14,padding:14,background:'var(--bg-tertiary)',borderRadius:'var(--radius-xs)'}}>
            {/* SMC Strategy */}
            <div style={{fontSize:'0.75rem',fontWeight:700,color:'var(--blue)'}}>━ SMC Strategy</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:8}}>
              <div><label style={{fontSize:'0.7rem'}}>Confluence Threshold</label><input type="number" value={form.confluence_threshold} onChange={e=>u('confluence_threshold',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>Swing Length</label><input type="number" value={form.swing_length} onChange={e=>u('swing_length',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>OB Impulse Ratio</label><input type="number" step="0.1" value={form.ob_impulse_ratio} onChange={e=>u('ob_impulse_ratio',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>FVG Min Gap (pips)</label><input type="number" value={form.fvg_min_gap_pips} onChange={e=>u('fvg_min_gap_pips',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>Liq Sweep Min (pips)</label><input type="number" value={form.liq_sweep_min_pips} onChange={e=>u('liq_sweep_min_pips',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>Max Spread (pips)</label><input type="number" value={form.max_spread_pips} onChange={e=>u('max_spread_pips',+e.target.value)}/></div>
            </div>
            {/* Circuit Breakers */}
            <div style={{fontSize:'0.75rem',fontWeight:700,color:'var(--yellow)'}}>━ Circuit Breakers</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr 1fr',gap:8}}>
              <div><label style={{fontSize:'0.7rem'}}>Max Daily Consec. Losses</label><input type="number" step="1" min="1" value={form.max_daily_consecutive_losses} onChange={e=>u('max_daily_consecutive_losses',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>Max Weekly Consec. Losses</label><input type="number" step="1" min="1" value={form.max_weekly_consecutive_losses} onChange={e=>u('max_weekly_consecutive_losses',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>Max Consec. Losses</label><input type="number" value={form.max_consecutive_losses} onChange={e=>u('max_consecutive_losses',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>Max Open Positions</label><input type="number" value={form.max_concurrent_positions} onChange={e=>u('max_concurrent_positions',+e.target.value)}/></div>
            </div>
            {/* TP R:R + Volume Split */}
            <div style={{fontSize:'0.75rem',fontWeight:700,color:'var(--green)'}}>━ Take Profit</div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:8}}>
              {[1,2,3,4,5].filter(n=>n<=form.tp_count).map(n=>(
                <div key={n}><label style={{fontSize:'0.7rem'}}>TP{n} R:R</label><input type="number" step="0.5" value={form[`tp${n}_rr`]} onChange={e=>u(`tp${n}_rr`,+e.target.value)}/></div>
              ))}
            </div>
            <div><label style={{fontSize:'0.7rem'}}>TP Volume Split (%)</label><input type="text" value={form.tp_splits} onChange={e=>u('tp_splits',e.target.value)} placeholder="30,25,20,15,10"/><div style={{fontSize:'0.65rem',color:'var(--text-muted)'}}>Comma-separated % per TP (must sum to 100)</div></div>
            {/* Break-Even */}
            <div style={{fontSize:'0.75rem',fontWeight:700,color:'var(--purple)'}}>━ Break-Even</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
              <div><label style={{fontSize:'0.7rem'}}>BE Trigger (R)</label><input type="number" step="0.1" value={form.be_trigger_rr} onChange={e=>u('be_trigger_rr',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>BE Buffer (pips)</label><input type="number" step="0.5" value={form.be_buffer_pips} onChange={e=>u('be_buffer_pips',+e.target.value)}/></div>
            </div>
            {/* Trailing Stops */}
            <div style={{fontSize:'0.75rem',fontWeight:700,color:'var(--red)'}}>━ Trailing Stops</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
              {[2,3,4,5].filter(n=>n<=form.tp_count).map(n=>(
                <div key={n}><label style={{fontSize:'0.7rem'}}>TP{n} Trail</label><select value={form[`trail_method_tp${n}`]} onChange={e=>u(`trail_method_tp${n}`,e.target.value)}>{TRAIL_METHODS.map(m=><option key={m.v} value={m.v}>{m.l}</option>)}</select></div>
              ))}
              <div><label style={{fontSize:'0.7rem'}}>ATR Multiplier</label><input type="number" step="0.1" value={form.atr_trail_multiplier} onChange={e=>u('atr_trail_multiplier',+e.target.value)}/></div>
              <div><label style={{fontSize:'0.7rem'}}>Fixed Trail Pips</label><input type="number" value={form.trail_pips} onChange={e=>u('trail_pips',+e.target.value)}/></div>
            </div>
          </div>)}

          <div style={{display:'flex',gap:8}}>
            <button className="btn btn-primary" onClick={()=>mutation.mutate()} disabled={isRunning||status!=='ONLINE'} style={{flex:1}}>
              {isRunning?<><Loader2 size={16} className="spin"/> Running...</>:<><Play size={16}/> Run Backtest</>}
            </button>
            <button className="btn btn-secondary btn-sm" onClick={()=>{localStorage.removeItem(STORAGE_KEY);location.reload();}} title="Reset to defaults" style={{whiteSpace:'nowrap'}}>
              <X size={14}/> Reset
            </button>
          </div>
          {(isRunning||progress)&&<ProgressBar progress={progress}/>}
          {mutation.isError&&<div style={{color:'var(--red)',fontSize:'0.8rem'}}>Error: {mutation.error?.response?.data?.detail||mutation.error?.message||'Failed'}</div>}
        </div>
      </div>

      {/* Right panel: Live logs always visible */}
      <div className="card">
        <div className="card-header"><span className="card-title"><Terminal size={14} style={{display:'inline',marginRight:6}}/>Live Logs</span></div>
        <LiveLogPanel />
      </div>
    </div>

    {/* Results */}
    {result && <BacktestResults result={result} onSave={handleSave} onDismiss={handleDismiss} isSaving={isSaving}/>}

    {/* Saved Backtests — always visible at bottom */}
    <div className="card" style={{marginTop:20}}>
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
    </div>
  </>);
}
