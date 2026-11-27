#!/usr/bin/env bash
# Trend/Range/Fib strategies + order flow + institutional nodes, on every market with 1-minute flow.
#   SHA=<commit> nohup bash trend_run.sh > trend.log 2>&1 &
set -u
D=/opt/algoedge/research_data
PY=/opt/algoedge/demo/venv/bin/python
SHA=${SHA:-dev}
cd "$D"
for f in trend_of.py of_zones.py combo_eval.py combo_summary.py; do
  curl -fsSL "https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/$SHA/scripts/$f" -o "$D/$f"
done
F=$(for m in gold nasdaq sp500 silver us30 oil eurusd gbpusd usdjpy; do [ -f "${m}_of.parquet" ] && echo $m; done | paste -sd,)
S=${STRATS:-tb,rb,rr,ft,ti}
# two workers: markets split in halves
A=$(echo "$F" | tr ',' '\n' | awk 'NR%2==1' | paste -sd,)
B=$(echo "$F" | tr ',' '\n' | awk 'NR%2==0' | paste -sd,)
nice -n 15 $PY trend_of.py --data "$D" --markets "$A" --strategies "$S" --out "$D/trend" &
nice -n 15 $PY trend_of.py --data "$D" --markets "$B" --strategies "$S" --out "$D/trend" &
wait
nice -n 15 $PY combo_eval.py $(ls trend/cand_tb_*.parquet trend/cand_ft_*.parquet trend/cand_ti_*.parquet 2>/dev/null) \
  --min-n 12 --out trend/combos_slow.json &
nice -n 15 $PY combo_eval.py $(ls trend/cand_rb_*.parquet trend/cand_rr_*.parquet 2>/dev/null) \
  --min-n 30 --out trend/combos_fast.json &
wait
echo TREND_RUN_DONE
