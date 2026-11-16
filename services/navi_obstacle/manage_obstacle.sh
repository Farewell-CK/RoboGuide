#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/seaway/sdb/niulinqiang/roboguide_demo
WORK="$ROOT/navi-obstacle"
cd "$WORK"
if test -f .deploying; then exit 0; fi
if curl -fsS --max-time 3 http://127.0.0.1:8003/health >/dev/null; then exit 0; fi
if test -f service.pid; then
    PID=$(cat service.pid)
    if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
        STATE=$(ps -p "$PID" -o stat=)
        if [[ "$STATE" != Z* ]]; then
            if ps -p "$PID" -o args= | grep -Fq -- "$WORK/obstacle_server.py"; then exit 0; fi
            echo 'PID belongs to another process; refusing to replace it'; exit 1
        fi
    fi
fi
if ss -ltn 'sport = :8003' | tail -n +2 | grep -q .; then echo 'Port occupied; refusing to replace it'; exit 1; fi
export CUDA_VISIBLE_DEVICES
CUDA_VISIBLE_DEVICES=$("$ROOT/navi-gpu-env/bin/python" -c 'import json; print(json.load(open("../gpu-compare/gpu-environment.json"))["visible_gpu"])')
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
nohup "$ROOT/navi-obstacle-env/bin/python" -u "$WORK/obstacle_server.py" >> "$WORK/service.log" 2>&1 < /dev/null &
echo $! > service.pid
