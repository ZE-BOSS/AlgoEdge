#!/usr/bin/env bash
# Fetch more CME markets (US30, oil, euro, pound, yen) for the order-flow zone research.
#   SHA=<commit> nohup bash research_fetch2.sh > fetch2.log 2>&1 &
set -u
D=/opt/algoedge/research_data
PY=/opt/algoedge/demo/venv/bin/python
SHA=${SHA:-dev}
cd "$D"
curl -fsSL "https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/$SHA/scripts/of_fetch.py" -o of_fetch.py
M="YM.v.0:us30 CL.v.0:oil 6E.v.0:eurusd 6B.v.0:gbpusd 6J.v.0:usdjpy"
$PY of_fetch.py --cost --markets "$(echo $M | tr ' ' '\n' | cut -d: -f1 | paste -sd,)" --start 2026-01-01 --end 2026-11-26 \
  | tee cost2.log
grep -q "cost total \$0.00" cost2.log || { echo "STOP: not free, see cost2.log"; exit 2; }
for m in $M; do
  s=${m%%:*}; n=${m##*:}
  ( nice -n 19 $PY of_fetch.py --markets "$s" --start 2026-01-01 --end 2026-11-26 --out "$D" > "$D/fetch_$n.log" 2>&1; echo "FETCHED $n" ) &
done
wait
echo FETCH2_DONE
