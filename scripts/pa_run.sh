#!/usr/bin/env bash
# Export Deriv M5 history, then run the price-action study on Deriv (M15 + H1-ish M30) and on
# the futures with order flow.
#   SHA=<commit> nohup bash pa_run.sh > pa.log 2>&1 &
set -u
D=/opt/algoedge/research_data
PY=/opt/algoedge/demo/venv/bin/python
SHA=${SHA:-dev}
for f in mt5_export.py pa_study.py of_zones.py; do
  curl -fsSL "https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/$SHA/scripts/$f" -o "$D/$f"
done
if [ -z "${SKIP_EXPORT:-}" ]; then
  ( cd /opt/algoedge/demo/app && set -a && . /opt/algoedge/demo/instance.env && set +a && \
    PYTHONPATH=. nice -n 10 $PY "$D/mt5_export.py" --out "$D/mt5" 2>&1 | grep -E "bars|not on|ERROR|EXPORT" )
fi
cd "$D"
A=$(ls mt5/*_m5.parquet 2>/dev/null | xargs -n1 basename | sed 's/_m5.parquet//' | paste -sd,)
for tf in M15 M5; do
  nice -n 15 $PY pa_study.py --data "$D" --source mt5 --assets "$A" --tf $tf --out "$D/pa" &
done
F=$(for m in gold nasdaq sp500 silver us30 oil eurusd gbpusd usdjpy; do [ -f "${m}_of.parquet" ] && echo $m; done | paste -sd,)
nice -n 15 $PY pa_study.py --data "$D" --source futures --assets "$F" --tf M15 --out "$D/pa" &
wait
echo PA_RUN_DONE
