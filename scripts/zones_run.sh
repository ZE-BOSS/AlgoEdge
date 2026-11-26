#!/usr/bin/env bash
# Order-flow zone study on every market with 1-minute flow, M5 and M15.
#   SHA=<commit> MARKETS=gold,nasdaq nohup bash zones_run.sh > zones.log 2>&1 &
set -u
D=/opt/algoedge/research_data
PY=/opt/algoedge/demo/venv/bin/python
SHA=${SHA:-dev}
cd "$D"
curl -fsSL "https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/$SHA/scripts/of_zones.py" -o of_zones.py
M=${MARKETS:-gold,nasdaq,sp500,silver}
for tf in ${TFS:-M5 M15}; do
  nice -n 15 $PY of_zones.py --data "$D" --markets "$M" --tf "$tf" --out "$D/zones" &
done
wait
echo ZONES_RUN_DONE
