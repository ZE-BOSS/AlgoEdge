#!/usr/bin/env bash
# Futures strategy research: fetch Databento history, then run the strategies per market.
# nohup bash research_run.sh > /opt/algoedge/research_data/run.log 2>&1 &
set -u
D=/opt/algoedge/research_data
APP=/opt/algoedge/demo/app
PY=/opt/algoedge/demo/venv/bin/python
SHA=${SHA:-dev}
cd "$D"
for f in of_fetch.py bt_futures.py of_optimise.py; do
  curl -fsSL "https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/$SHA/scripts/$f" -o "$D/$f" || true
done
if [ ! -f "$D/sp500_of.parquet" ] && [ ! -f "$D/sp500_of.csv.gz" ]; then
  nice -n 19 $PY of_fetch.py --start 2026-01-01 --end 2026-11-26 --out "$D" || exit 1
fi
STRATS=VWAP_v1,RQ_DailyVWAP_v1,ORB_v1,RQ_NYORB_v1,RQ_CrazyORB_v1,RQ_M30ORB_v1,RQ_GigaORB_v1,RQ_FlashTriggerORB_v1,RQ_ScarfaceORB_v1,RQ_SigmaBreakout_v1,RQ_IVB_v1,RQ_NQPrecision_v1,RQ_TMeanReversion_v1,QL_IBS_v1,QL_DipBuy_v1
cd "$APP"
for pair in "gold silver" "nasdaq sp500"; do
  for m in $pair; do
    PYTHONPATH="$APP" nice -n 19 $PY "$D/bt_futures.py" --data "$D" --market "$m" --strategies "$STRATS" --out "$D/bt" > "$D/bt_$m.log" 2>&1 &
  done
  wait
done
grep -h -E "trades|ERROR" "$D"/bt_*.log | grep -v "^\s" 
[ -f "$D/of_optimise.py" ] && PYTHONPATH="$APP" nice -n 19 $PY "$D/of_optimise.py" --data "$D" --out "$D/report.json"
echo RESEARCH_DONE
