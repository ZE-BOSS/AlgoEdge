import json
import sys

def main():
    with open('errors/backtest_result.json', 'r') as f:
        data = json.load(f)

    print("--- TOP LEVEL METRICS ---")
    print(f"Total Trades: {data.get('total_trades')}")
    print(f"Total PnL: {data.get('total_pnl')}")
    print(f"Initial Balance: {data.get('initial_balance')}")
    print(f"Final Balance: {data.get('final_balance')}")
    print(f"Sharpe Ratio: {data.get('sharpe_ratio')}")
    print(f"Profit Factor: {data.get('profit_factor')}")
    
    print("\n--- REPORT METRICS ---")
    report = data.get('report', {})
    if isinstance(report, dict):
        print(f"Expectancy: {report.get('expectancy')}")
        print(f"Sortino: {report.get('sortino')}")
    
    print("\n--- GROUPED TRADES (183) ---")
    grouped_trades = data.get('grouped_trades', [])
    
    calculated_pnl = 0
    calculated_balance = data.get('initial_balance', 0)
    
    for idx, group in enumerate(grouped_trades):
        group_pnl = group.get('pnl', 0)  # The actual field is 'pnl' or 'combined_pnl'
        
        calculated_pnl += group_pnl
        calculated_balance += group_pnl
        
        if idx < 3 or idx > len(grouped_trades) - 3:
            print(f"Trade {idx+1}: PnL = {group_pnl:.2f}, Bal Before = {group.get('balance_before'):.2f}, Bal After = {group.get('balance_after'):.2f}")

    print(f"\nCalculated Total PnL: {calculated_pnl:.2f}")
    print(f"Math check: {data.get('initial_balance', 0):.2f} + {calculated_pnl:.2f} = {data.get('initial_balance', 0) + calculated_pnl:.2f}")
    
    print("\n--- PROP FIRM RISK ---")
    # Let's inspect sub_trades of a few trades to see the risk
    for idx in range(3):
        group = grouped_trades[idx]
        print(f"Trade {idx+1} sub_trades:")
        for sub in group.get('sub_trades', []):
            print(f"  Volume: {sub.get('volume', 0)}, SL: {sub.get('stop_loss', 0)}, Entry: {sub.get('entry_price', 0)}, PnL: {sub.get('pnl', 0):.2f}")
            
if __name__ == "__main__":
    main()
