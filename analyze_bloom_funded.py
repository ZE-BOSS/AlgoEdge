import json
from datetime import datetime, timedelta

def main():
    with open('errors/backtest_result.json', 'r') as f:
        data = json.load(f)
    
    trades = data.get('grouped_trades', [])
    initial_balance = data.get('initial_balance', 25000)
    
    if not trades:
        print("No trades found.")
        return
        
    print(f"--- Bloom Funded Challenge Evaluation ---")
    print(f"Initial Balance: ${initial_balance:,.2f}\n")
    
    # --- Data Prep ---
    current_balance = initial_balance
    all_time_peak = initial_balance
    
    max_trailing_dd_abs = 0
    lowest_balance = initial_balance
    
    # Dictionaries to aggregate by trading day
    daily_pnl = {}
    daily_balances = {}
    
    fast_trades_count = 0
    total_trades_count = 0
    
    for t in trades:
        exit_time_str = t.get('exit_time') or t.get('exit_time_iso')
        if not exit_time_str:
            continue
            
        dt_utc = datetime.fromisoformat(exit_time_str.replace('Z', '+00:00'))
        
        # Adjust dt to "Trading Day" (Shift by -22 hours so that a day starts at 22:00 UTC = 17:00 EST)
        trading_day = (dt_utc - timedelta(hours=22)).strftime('%Y-%m-%d')
        
        pnl = t.get('pnl', 0)
        current_balance += pnl
        
        # Trailing DD
        if current_balance > all_time_peak:
            all_time_peak = current_balance
            
        trailing_dd = all_time_peak - current_balance
        if trailing_dd > max_trailing_dd_abs:
            max_trailing_dd_abs = trailing_dd
            
        # Static DD
        if current_balance < lowest_balance:
            lowest_balance = current_balance
            
        # Duration for scalping rule
        duration_minutes = t.get('duration_minutes', 0)
        if duration_minutes < 2:
            fast_trades_count += 1
        total_trades_count += 1
        
        # Aggregate by day
        daily_pnl[trading_day] = daily_pnl.get(trading_day, 0) + pnl
        if trading_day not in daily_balances:
            daily_balances[trading_day] = []
        daily_balances[trading_day].append(current_balance)
        
    # --- Profit Target ---
    total_profit = current_balance - initial_balance
    profit_pct = (total_profit / initial_balance) * 100
    
    print("1. PROFIT TARGET")
    print(f"Total Profit Made: ${total_profit:,.2f} ({profit_pct:.2f}%)")
    print(f"1-Step Requirement (10%): ${initial_balance * 0.10:,.2f} -> {'PASSED' if profit_pct >= 10 else 'FAILED'}")
    print(f"2-Step Phase 1 Req (8%): ${initial_balance * 0.08:,.2f} -> {'PASSED' if profit_pct >= 8 else 'FAILED'}")
    print(f"2-Step Phase 2 Req (5%): ${initial_balance * 0.05:,.2f} -> {'PASSED' if profit_pct >= 5 else 'FAILED'}\n")

    # --- Max Drawdown ---
    print("2. MAXIMUM DRAWDOWN")
    trailing_dd_limit_abs = initial_balance * 0.08
    
    print(f"Max Trailing Drawdown Experienced: ${max_trailing_dd_abs:,.2f} ({(max_trailing_dd_abs/initial_balance)*100:.2f}% of initial)")
    print(f"1-Step Trailing Limit (8%): ${trailing_dd_limit_abs:,.2f} -> {'PASSED' if max_trailing_dd_abs <= trailing_dd_limit_abs else 'FAILED'}")
    
    static_dd_experienced = initial_balance - lowest_balance
    if static_dd_experienced < 0: static_dd_experienced = 0
    static_dd_limit_abs = initial_balance * 0.06
    print(f"Max Static Drawdown Experienced: ${static_dd_experienced:,.2f} ({(static_dd_experienced/initial_balance)*100:.2f}% of initial)")
    print(f"2-Step Static Limit (6%): ${static_dd_limit_abs:,.2f} -> {'PASSED' if static_dd_experienced <= static_dd_limit_abs else 'FAILED'}\n")

    # --- Daily Drawdown ---
    print("3. DAILY DRAWDOWN")
    daily_dd_limit = initial_balance * 0.04
    
    max_daily_dd_experienced = 0
    start_of_day_balance = initial_balance
    
    # Sort days
    for day in sorted(daily_balances.keys()):
        balances = daily_balances[day]
        min_balance_in_day = min(balances)
        
        if min_balance_in_day < start_of_day_balance:
            day_drop = start_of_day_balance - min_balance_in_day
            if day_drop > max_daily_dd_experienced:
                max_daily_dd_experienced = day_drop
                
        # Prepare for next day
        start_of_day_balance = balances[-1]
        
    print(f"Max Daily Drop Experienced: ${max_daily_dd_experienced:,.2f} ({(max_daily_dd_experienced/initial_balance)*100:.2f}% of initial)")
    print(f"Daily Limit (4%): ${daily_dd_limit:,.2f} -> {'PASSED' if max_daily_dd_experienced <= daily_dd_limit else 'FAILED'}\n")
    
    # --- Consistency Rule ---
    print("4. CONSISTENCY RULE")
    max_single_day_profit = max(daily_pnl.values()) if daily_pnl else 0
    
    one_step_consistency_limit = (initial_balance * 0.10) * 0.35
    two_step_p1_consistency_limit = (initial_balance * 0.08) * 0.40
    
    print(f"Max Single Day Profit: ${max_single_day_profit:,.2f}")
    print(f"1-Step Limit (35% of 10% Target): ${one_step_consistency_limit:,.2f} -> {'PASSED' if max_single_day_profit <= one_step_consistency_limit else 'FAILED'}")
    print(f"2-Step P1 Limit (40% of 8% Target): ${two_step_p1_consistency_limit:,.2f} -> {'PASSED' if max_single_day_profit <= two_step_p1_consistency_limit else 'FAILED'}\n")

    # --- Min Trading Days ---
    print("5. MINIMUM TRADING DAYS")
    min_profit_req = initial_balance * 0.005
    active_days = sum(1 for p in daily_pnl.values() if p >= min_profit_req)
    print(f"Days with >= ${min_profit_req:,.2f} profit: {active_days}")
    print(f"Requirement (4 days): -> {'PASSED' if active_days >= 4 else 'FAILED'}\n")
    
    # --- Gambling / Scalping Rule ---
    print("6. GAMBLING / SCALPING (HFT)")
    fast_trades_pct = (fast_trades_count / total_trades_count) * 100 if total_trades_count else 0
    print(f"Trades under 2 minutes: {fast_trades_count} / {total_trades_count} ({fast_trades_pct:.2f}%)")
    print(f"Limit (50%): -> {'PASSED' if fast_trades_pct < 50 else 'FAILED'}\n")

if __name__ == "__main__":
    main()
