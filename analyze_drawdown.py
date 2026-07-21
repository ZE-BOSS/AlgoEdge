import json
from datetime import datetime

def main():
    with open('errors/backtest_result.json', 'r') as f:
        data = json.load(f)
    
    trades = data.get('grouped_trades', [])
    initial_balance = data.get('initial_balance', 25000)
    
    if not trades:
        print("No trades found.")
        return
        
    records = []
    current_balance = initial_balance
    
    # Track all-time peak for absolute drawdown
    all_time_peak = initial_balance
    all_time_max_dd_abs = 0
    all_time_max_dd_pct = 0
    
    daily_pnl = {}
    weekly_pnl = {}
    monthly_pnl = {}
    
    for t in trades:
        exit_time_str = t.get('exit_time') or t.get('exit_time_iso')
        if not exit_time_str:
            continue
            
        dt = datetime.fromisoformat(exit_time_str.replace('Z', '+00:00'))
        pnl = t.get('pnl', 0)
        current_balance += pnl
        
        # Drawdown tracking
        if current_balance > all_time_peak:
            all_time_peak = current_balance
        
        dd_abs = all_time_peak - current_balance
        dd_pct = dd_abs / all_time_peak if all_time_peak > 0 else 0
        
        if dd_abs > all_time_max_dd_abs:
            all_time_max_dd_abs = dd_abs
        if dd_pct > all_time_max_dd_pct:
            all_time_max_dd_pct = dd_pct
            
        # Day
        day_key = dt.strftime('%Y-%m-%d')
        daily_pnl[day_key] = daily_pnl.get(day_key, 0) + pnl
        
        # Week
        year, week, weekday = dt.isocalendar()
        week_key = f"{year}-W{week}"
        weekly_pnl[week_key] = weekly_pnl.get(week_key, 0) + pnl
        
        # Month
        month_key = dt.strftime('%Y-%m')
        monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + pnl
    
    max_daily_loss = min(daily_pnl.values()) if daily_pnl else 0
    max_weekly_loss = min(weekly_pnl.values()) if weekly_pnl else 0
    max_monthly_loss = min(monthly_pnl.values()) if monthly_pnl else 0
    
    print("--- PnL Drops (Sum of Net PnL per calendar period) ---")
    print(f"Max Daily Net Loss: {max_daily_loss:.2f}")
    print(f"Max Weekly Net Loss: {max_weekly_loss:.2f}")
    print(f"Max Monthly Net Loss: {max_monthly_loss:.2f}")
    
    print("\n--- All Time Drawdown ---")
    print(f"All Time Max DD (Absolute): ${all_time_max_dd_abs:.2f}")
    print(f"All Time Max DD (Percentage): {all_time_max_dd_pct*100:.2f}%")
    
    reported_pct = data.get('max_drawdown_pct')
    if reported_pct is None:
        reported_pct = data.get('report', {}).get('max_drawdown_pct', 0)
        
    print(f"Reported Max Drawdown Pct in JSON: {reported_pct * 100:.2f}%")

if __name__ == "__main__":
    main()
