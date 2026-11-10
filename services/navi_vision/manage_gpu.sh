#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/seaway/sdb/niulinqiang/roboguide_demo
WORK="$ROOT/gpu-compare"
CPU="$ROOT/navi-cpu-env/bin/python"
GPU="$ROOT/navi-gpu-env/bin/python"
cd "$WORK"
case "${1:-status}" in
network_probe)
    date -Is
    printf '\nCURRENT_SERVICES\n'
    curl --fail --silent --max-time 5 http://127.0.0.1:8002/health
    printf '\nNETWORK_TOOLS\n'
    for program in tailscale caddy nginx node; do command -v "$program" || true; done
    printf '\nLISTENING_WEB_PORTS\n'
    ss -ltn '( sport = :80 or sport = :443 or sport = :8000 or sport = :8002 )'
    printf '\nRECENT_GPU_REQUESTS\n'
    tail -n 12 "$WORK/service.log"
    printf '\nSUPABASE_CONNECTIVITY\n'
    curl --silent --output /dev/null --connect-timeout 5 --max-time 10 --write-out '%{http_code}\n' https://plhfikiqeqqlermdjdko.supabase.co/auth/v1/health || true
    printf '\nNGINX_PUBLIC_CONFIGURATION\n'
    find /etc/nginx/sites-enabled -maxdepth 1 -type l -printf '%f\n' 2>/dev/null || true
    grep -hE '^[[:space:]]*(listen|server_name)[[:space:]]' /etc/nginx/sites-enabled/* 2>/dev/null || true
    printf '\nNONINTERACTIVE_ADMIN_CHECK\n'
    if sudo -n true 2>/dev/null; then echo AVAILABLE; else echo UNAVAILABLE; fi
    ;;
inspect)
    date -Is
    df -h "$WORK"
    nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu --format=csv
    "$CPU" - <<'PY'
import importlib.metadata as m, json, pathlib, platform, torch
names=['torch','torchvision','transformers','numpy','pillow','safetensors','tokenizers','huggingface-hub','fastapi','uvicorn','python-multipart','clip-client','requests']
packages={}
for name in names:
    try: packages[name]=m.version(name)
    except m.PackageNotFoundError: pass
result=dict(python=platform.python_version(), packages=packages, cuda_available=torch.cuda.is_available(), cuda_build=torch.version.cuda)
pathlib.Path('cpu-environment.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
PY
    find "$ROOT/cache/huggingface/hub/models--facebook--dinov2-base" -maxdepth 3 -type f -name '*.json' -o -type l -name '*.json'
    curl --fail --silent --max-time 5 http://127.0.0.1:8000/health
    printf '\n'
    ss -ltnp 'sport = :8002'
    if test -x "$GPU"; then "$GPU" -m pip show torch transformers; fi
    ;;
install)
    if test -f install.pid && kill -0 "$(cat install.pid)" 2>/dev/null; then
        echo 'GPU setup already running'; exit 0
    fi
    if test -f install.exit; then mv install.exit "install.previous.$(date +%s).exit"; fi
    nohup bash "$WORK/setup_gpu.sh" > "$WORK/install.log" 2>&1 < /dev/null &
    echo $! > install.pid
    echo "GPU setup started, PID $(cat install.pid)"
    ;;
pin_dependencies)
    "$CPU" - <<'PY'
import os, pathlib, signal, subprocess, time
work=pathlib.Path.cwd()
pid_file=work/'install.pid'
if pid_file.exists() and not (work/'install.exit').exists():
    pid=int(pid_file.read_text())
    proc=pathlib.Path(f'/proc/{pid}')
    command=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode()
    assert str(work/'setup_gpu.sh') in command, 'Refusing to stop an unrelated process'
    children=(proc/f'task/{pid}/children').read_text().split()
    for child in children:
        child_cmd=pathlib.Path(f'/proc/{child}/cmdline').read_bytes().replace(b'\0',b' ').decode()
        if str(work.parent/'navi-gpu-env/bin/python')+' -m pip install ' in child_cmd:
            os.kill(int(child),signal.SIGTERM)
            print('Stopped own dependency installer',child,flush=True)
    for _ in range(15):
        if (work/'install.exit').exists(): break
        time.sleep(1)
    assert (work/'install.exit').exists(), 'Installer has not exited; leave setup source intact'
freeze=subprocess.check_output([str(work.parent/'navi-cpu-env/bin/python'),'-m','pip','freeze'],text=True)
(work/'cpu-freeze.txt').write_text(freeze)
constraints=[]
for line in freeze.splitlines():
    name=line.split('==')[0].lower()
    if '==' in line and name not in {'torch','torchvision','triton'} and not name.startswith('nvidia-'):
        constraints.append(line)
(work/'cpu-dependency-constraints.txt').write_text('\n'.join(constraints)+'\n')
print('Pinned CPU dependency versions:',len(constraints))
PY
    ;;
status)
    date -Is
    if test -f install.exit; then printf 'INSTALL_EXIT='; cat install.exit; fi
    if test -f install.pid && kill -0 "$(cat install.pid)" 2>/dev/null; then echo INSTALL_RUNNING; fi
    if test -f install.log; then tail -n 12 install.log; fi
    if ! test -f install.exit; then
        find /tmp -maxdepth 2 -user "$(id -un)" -type f -name '*.whl' -printf '%f %s bytes\n' 2>/dev/null | tail -n 4 || true
    fi
    if ! test -f network-checked.txt; then
        "$CPU" - <<'PY'
import pathlib, requests, time
for url in ['https://pypi.org/simple/numpy/','https://pypi.tuna.tsinghua.edu.cn/simple/numpy/']:
    start=time.perf_counter()
    try:
        r=requests.head(url,timeout=8)
        print('INDEX_CHECK',url,r.status_code,round(time.perf_counter()-start,2),flush=True)
    except requests.RequestException as e: print('INDEX_CHECK',url,type(e).__name__,flush=True)
pathlib.Path('network-checked.txt').write_text('done')
PY
        find /tmp -maxdepth 2 -user "$(id -un)" -type f -name 'numpy-2.0.1*.whl' -printf '%f %s bytes\n' 2>/dev/null || true
    fi
    if test -f service.pid; then printf 'GPU_SERVICE_PID='; cat service.pid; fi
    if test -f service.log; then tail -n 5 service.log; fi
    curl --silent --max-time 3 http://127.0.0.1:8002/health || true
    printf '\n'
    ;;
start)
    "$GPU" -m pip freeze > gpu-freeze.txt
    if curl --fail --silent --max-time 3 http://127.0.0.1:8002/health > health-existing.json; then
        "$CPU" - <<'PY'
import json, pathlib
h=json.loads(pathlib.Path('health-existing.json').read_text())
assert h.get('device','').startswith('cuda') and h.get('source_sha256')
assert h['pid']==int(pathlib.Path('service.pid').read_text())
print('Existing GPU comparison service is healthy',json.dumps(h))
PY
        exit 0
    fi
    if ss -ltn 'sport = :8002' | tail -n +2 | head -n 1 | read -r occupied; then
        echo 'Port 8002 occupied; refusing to replace another service' >&2; exit 1
    fi
    "$CPU" - <<'PY'
import datetime, json, pathlib, subprocess
line=subprocess.check_output(['nvidia-smi','--id=1','--query-gpu=uuid,name,memory.free','--format=csv,noheader,nounits'],text=True).strip()
uuid,name,free=[v.strip() for v in line.split(',')]
assert int(free)>=2048, 'Not enough free memory on selected GPU 1'
selection=dict(physical_index=1,uuid=uuid,name=name,free_mib_before_start=int(free),selected=datetime.datetime.now().isoformat())
pathlib.Path('gpu-selection.json').write_text(json.dumps(selection,indent=2))
print(json.dumps(selection))
PY
    export CUDA_VISIBLE_DEVICES
    CUDA_VISIBLE_DEVICES=$("$CPU" -c 'import json;print(json.load(open("gpu-selection.json"))["uuid"])')
    export HF_HOME="$ROOT/cache/huggingface" TORCH_HOME="$ROOT/cache/torch"
    export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
    nohup "$GPU" -u "$WORK/navi_gpu_gateway.py" > "$WORK/service.log" 2>&1 < /dev/null &
    echo $! > service.pid
    for attempt in $(seq 1 40); do
        if curl --fail --silent --max-time 2 http://127.0.0.1:8002/health; then printf '\n'; exit 0; fi
        if ! kill -0 "$(cat service.pid)" 2>/dev/null; then tail -n 40 service.log; exit 1; fi
        sleep 1
    done
    echo 'GPU service is still starting; inspect service.log' >&2; exit 1
    ;;
benchmark)
    "$CPU" "$WORK/benchmark_gpu.py"
    ;;
stop)
    if test -f service.pid && kill -0 "$(cat service.pid)" 2>/dev/null; then
        navi_pid=$(cat service.pid)
        if ! tr '\0' ' ' < "/proc/$navi_pid/cmdline" | grep -Fq "$WORK/navi_gpu_gateway.py"; then
            echo 'PID is not this GPU service; refusing to stop it' >&2; exit 1
        fi
        kill "$navi_pid"
        echo "Stopped owned GPU service $navi_pid"
    fi
    ;;
*) echo 'Task not implemented yet' >&2; exit 2;;
esac
