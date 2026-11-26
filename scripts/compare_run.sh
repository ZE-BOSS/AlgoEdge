#!/usr/bin/env bash
# Run the futures shortlist on the demo instance's MT5 (Deriv) prices and compare.
#   SHA=<commit> nohup bash compare_run.sh > compare.log 2>&1 &
set -u
D=/opt/algoedge/research_data
SHA=${SHA:-dev}
curl -fsSL "https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/$SHA/scripts/bt_mt5_compare.py" -o "$D/bt_mt5_compare.py"
cd /opt/algoedge/demo/app
set -a; . /opt/algoedge/demo/instance.env; set +a   # app .env is read by the app itself (dotenv)
PYTHONPATH=. nice -n 10 /opt/algoedge/demo/venv/bin/python "$D/bt_mt5_compare.py" \
    --data "$D" --out "$D/${OUT:-compare.json}" ${ONLY:+--only "$ONLY"} ${ALL:+--all} 2>&1 | grep -vE "Warning|warn\(" 
echo COMPARE_RUN_DONE
