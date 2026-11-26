#!/usr/bin/env bash
# Parallel version: wait for gold, stop the sequential job, fetch the other markets in
# parallel, backtest each market as soon as its data is in, then optimise.
#   SHA=<commit> nohup bash research_run2.sh > run2.log 2>&1 &
set -u
D=/opt/algoedge/research_data
APP=/opt/algoedge/demo/app
PY=/opt/algoedge/demo/venv/bin/python
SHA=${SHA:-dev}
cd "$D"
for f in of_fetch.py bt_futures.py of_optimise.py; do
  curl -fsSL "https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/$SHA/scripts/$f" -o "$D/$f.new" && mv "$D/$f.new" "$D/$f"
done
until [ -f "$D/gold_of.parquet" ] || [ -f "$D/gold_of.csv.gz" ]; do sleep 30; done
pkill -f research_run.sh; pkill -f "of_fetch.py --start"; sleep 5
echo "gold ready $(date)"
STRATS=VWAP_v1,RQ_DailyVWAP_v1,ORB_v1,RQ_NYORB_v1,RQ_CrazyORB_v1,RQ_M30ORB_v1,RQ_GigaORB_v1,RQ_FlashTriggerORB_v1,RQ_ScarfaceORB_v1,RQ_SigmaBreakout_v1,RQ_IVB_v1,RQ_NQPrecision_v1,RQ_TMeanReversion_v1,QL_IBS_v1,QL_DipBuy_v1
bt() {  # market
  (cd "$APP" && PYTHONPATH="$APP" nice -n 19 $PY "$D/bt_futures.py" --data "$D" --market "$1" --strategies "$STRATS" --out "$D/bt" > "$D/bt_$1.log" 2>&1)
  echo "bt $1 done $(date)"
}
bt gold &
for pair in "SI.v.0 silver" "NQ.v.0 nasdaq" "ES.v.0 sp500"; do
  set -- $pair
  ( nice -n 19 $PY of_fetch.py --markets "$1" --start 2026-01-01 --end 2026-11-26 --out "$D" > "$D/fetch_$2.log" 2>&1 \
      && echo "fetch $2 done $(date)" && bt "$2" ) &
done
wait
grep -h -E "trades|ERROR" "$D"/bt_*.log | grep -v "^\s"
(cd "$APP" && PYTHONPATH="$APP" nice -n 19 $PY "$D/of_optimise.py" --data "$D" --out "$D/report.json") > "$D/opt.log" 2>&1
tail -70 "$D/opt.log"
echo RESEARCH_DONE
