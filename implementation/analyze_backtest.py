import json, sys
from collections import Counter

# Fix encoding
sys.stdout.reconfigure(encoding='utf-8')

with open(r'c:\Users\ikchr\Documents\AlgoEdge\implementation\usdchf_uk100_eurgbp_ndx100_backtest.json') as f:
    data2 = json.load(f)

trades2 = data2['grouped_trades']
params2 = data2['run']['params_snapshot']

for sym in sorted(set(t.get('symbol','?') for t in trades2)):
    sym_trades = [t for t in trades2 if t.get('symbol') == sym]
    sym_wins = [t for t in sym_trades if t.get('combined_pnl',0) > 0]
    sym_losses = [t for t in sym_trades if t.get('combined_pnl',0) < 0]
    reasons2 = Counter(t.get('exit_reason', 'UNKNOWN') for t in sym_trades)
    print(f"\n{sym}: {len(sym_trades)} trades ({len(sym_wins)}W / {len(sym_losses)}L)")
    for reason, count in reasons2.most_common():
        print(f"  {reason}: {count}")
    if sym_trades:
        pnls2 = [t.get('combined_pnl',0) for t in sym_trades]
        print(f"  Mean PnL: ${sum(pnls2)/len(pnls2):.2f}, Max: ${max(pnls2):.2f}, Min: ${min(pnls2):.2f}")
    
    # SL distance
    sl_dists = [abs(t['entry_price'] - t['stop_loss']) for t in sym_trades[:20]]
    if sl_dists:
        avg_sl = sum(sl_dists)/len(sl_dists)
        # Determine pip size for this symbol
        if 'JPY' in sym:
            pip = 0.01
        elif sym in ('UK100', 'NDX100', 'NAS100', 'US500', 'US30', 'SPX500'):
            pip = 1.0
        else:
            pip = 0.0001
        print(f"  Avg SL dist (price): {avg_sl:.5f} = {avg_sl/pip:.1f} pips")

    # Overshoot check
    expected_max = 25000 * 0.01 * 5  # 1% risk, 5R
    overshoots = [t for t in sym_trades if t.get('combined_pnl',0) > expected_max]
    if overshoots:
        print(f"  OVERSHOOTS (>${expected_max:.0f}): {len(overshoots)}")
        for o in overshoots[:3]:
            print(f"    PnL=${o.get('combined_pnl',0):.2f} reason={o.get('exit_reason')} subs={o.get('tp_count')}")
            for sub in o.get('sub_trades', []):
                print(f"      TP{sub.get('tp_level')}: vol={sub.get('volume')} exit={sub.get('exit_price',0):.5f} pnl=${sub.get('pnl',0):.2f} reason={sub.get('exit_reason')}")

# Key observation on the max PnL trades
print("\n\n=== KEY OVERSHOOT INVESTIGATION ===")
all_big = [t for t in trades2 if t.get('combined_pnl', 0) > 1250]
for t in all_big[:5]:
    print(f"\nSymbol: {t.get('symbol')} | Dir: {t.get('direction')}")
    print(f"  Entry: {t.get('entry_price')} | SL: {t.get('stop_loss')}")
    print(f"  Combined PnL: ${t.get('combined_pnl',0):.2f}")
    for sub in t.get('sub_trades', []):
        print(f"  TP{sub.get('tp_level')}: vol={sub.get('volume')} entry={sub.get('entry_price'):.5f} "
              f"tp_target={sub.get('take_profit',0):.5f} exit={sub.get('exit_price',0):.5f} "
              f"pnl=${sub.get('pnl',0):.2f} reason={sub.get('exit_reason')} be={sub.get('be_applied')}")
