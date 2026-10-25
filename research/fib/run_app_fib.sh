#!/usr/bin/env bash
# FibTrend_v1 through the app's own backtest path (scripts/run_app_backtest.py),
# every tested market, 3 years, $10k, 1% risk compounded, 4 markets at a time.
cd "$(dirname "$0")/../.."
export PYTHONPATH="venv/Lib/site-packages;."
# Deriv's bars are UTC+0 (data_fetcher measures it); this machine's .env pins +3,
# which shifts every bar 3h and puts the H4 grid off the broker's. Override for
# these runs only - .env itself is the user's to change.
export ALGOEDGE_MT5_SERVER_UTC_OFFSET=0
one() {
  s="$1"; f="research/fib/app/${s// /_}"
  [ -s "$f.summary.json" ] && { echo "SKIP $s"; return; }
  py -3.12 scripts/run_app_backtest.py --strategy FibTrend_v1 --symbol "$s" --start 2023-10-25 --end 2026-10-25 \
     --balance 10000 --risk 1 --tp1-rr 20 --sizing-basis BALANCE --daily-dd 100 --weekly-dd 100 \
     --dump "$f.trades.json" > "$f.summary.json" 2> "$f.err"
  echo "DONE $s $(py -3.12 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('trades'),d.get('final_balance'),d.get('expectancy_r'),d.get('max_dd_pct'))" "$f.summary.json" 2>&1 | tail -1)"
}
export -f one
printf '%s\n' "XAUUSD" "XAGUSD" "US Tech 100" "US SP 500" "Wall Street 30" "GBPJPY" "EURUSD" "BTCUSD" \
  "Step Index 200" "Step Index 500" "Step Index" "Step Index 300" "Step Index 400" "Multi Step 2 Index" \
  "Volatility 10 Index" "Volatility 25 Index" "Volatility 50 Index" "Volatility 75 Index" "Volatility 100 Index" \
  "Volatility 75 (1s) Index" \
  | xargs -P 4 -I{} bash -c 'one "$@"' _ {}
echo ALL_FINISHED
