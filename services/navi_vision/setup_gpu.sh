#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/seaway/sdb/niulinqiang/roboguide_demo
WORK="$ROOT/gpu-compare"
ENV="$ROOT/navi-gpu-env"
export PIP_CACHE_DIR="$ROOT/cache/pip-gpu"
export PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p "$WORK/tmp"
export TMPDIR="$WORK/tmp"
cd "$WORK"
trap 'printf "%s\n" "$?" > "$WORK/install.exit"' EXIT
exec 9>"$WORK/install.lock"
flock -n 9 || { echo 'Another GPU setup is running'; exit 1; }
df -h "$WORK"
if test -d "$ENV" && ! test -f "$ENV/.navi-isolated-gpu"; then
    echo 'An unrecognized environment exists; refusing to overwrite it' >&2
    exit 1
fi
if ! test -x "$ENV/bin/python"; then
    "$ROOT/navi-cpu-env/bin/python" -m venv "$ENV"
    touch "$ENV/.navi-isolated-gpu"
fi
"$ENV/bin/python" -m pip install --progress-bar off --timeout 60 --retries 3 \
    torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124
"$ENV/bin/python" - <<'PY'
import json, pathlib
packages=json.loads(pathlib.Path('cpu-environment.json').read_text())['packages']
pathlib.Path('gpu-requirements.txt').write_text(''.join(f'{n}=={v}\n' for n,v in packages.items() if n not in {'torch','torchvision'}))
PY
"$ENV/bin/python" -m pip install --progress-bar off --timeout 60 --retries 3 \
    -r "$WORK/gpu-requirements.txt" --index-url https://pypi.org/simple
"$ENV/bin/python" -m pip check
"$ENV/bin/python" - <<'PY'
import hashlib, json, pathlib, shutil
root=pathlib.Path('/home/seaway/sdb/niulinqiang/roboguide_demo')
work=root/'gpu-compare'
src=root/'Indoor-Navigation-for-visually-impaired/scripts/dinov2_http_gateway.py'
dst=work/'cpu_gateway_snapshot.py'
shutil.copy2(src,dst)
model=root/'cache/huggingface/hub/models--facebook--dinov2-base/snapshots/f9e44c814b77203eaa57a6bdbbd535f21ede1415'
files=[src,model/'config.json',model/'preprocessor_config.json',model/'model.safetensors']
manifest={'source':str(src),'model_revision':model.name,'files':{}}
for p in files:
    if not p.is_file(): raise RuntimeError(f'Model/source file missing: {p}')
    digest=hashlib.sha256()
    with p.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    manifest['files'][p.name]=digest.hexdigest()
(work/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps(manifest,indent=2))
PY
echo GPU_ENVIRONMENT_INSTALLED
