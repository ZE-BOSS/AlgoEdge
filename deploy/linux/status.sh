#!/usr/bin/env bash
# One-screen health check of every instance: services, bridge, API.
for d in /opt/algoedge/*/instance.env; do
    n=$(basename "$(dirname "$d")"); . "$d"
    printf '== %s\n' "$n"
    for u in "algoedge-mt5@$n" "algoedge-bridge@$n" "algoedge-api@$n"; do
        printf '  %-26s %s\n' "$u" "$(systemctl is-active "$u")"
    done
    printf '  %-26s %s\n' "api /api/health" "$(curl -s -m 5 "http://127.0.0.1:$API_PORT/api/health" | head -c 120)"
done
