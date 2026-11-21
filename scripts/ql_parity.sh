#!/bin/bash
# AlgoEdge backtester runs for QL_IBS_v1 on the three US index CFDs (research code, demo's MT5).
# Usage (as root): bash ql_parity.sh [STRATEGY]  -> /opt/algoedge/research_out/ql_parity_<STRATEGY>.txt
STRAT=${1:-QL_IBS_v1}
OUT=/opt/algoedge/research_out/ql_parity_${STRAT}.txt
: > "$OUT"; chown algoedge "$OUT"
for s in "US Tech 100" "US SP 500" "Wall Street 30"; do
  runuser -u algoedge -- bash -c "cd /opt/algoedge/research && set -a && . /opt/algoedge/demo/instance.env && set +a && \
    nice -n 19 /opt/algoedge/demo/venv/bin/python scripts/run_app_backtest.py --strategy $STRAT --symbol '$s' \
    --start 2024-01-23 --end 2026-11-20 --balance 10000 --risk 1 --tp1-rr 40 --min-rr 3 \
    --dump /opt/algoedge/research_out/ql_${STRAT}_${s// /_}.json 2>/dev/null" \
  | python3 -c "import sys,json;t=sys.stdin.read();j=json.loads(t[t.index('{'):]);print('$s',{k:j.get(k) for k in ('trades','net_pnl','win_rate','profit_factor','max_dd_pct')})" >> "$OUT" 2>&1
done
echo PARITY_DONE >> "$OUT"
