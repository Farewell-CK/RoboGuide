#!/usr/bin/env bash
# Step 1 — 全链路就绪: 共享 MuJoCo 世界 + Integration Server + 三个异构 roboguide-node
#
# 验收点: 三个 runtime_name 不同的节点同时在册 (ADR-0006 异构 EAIOS 协调)。
# 用法:   bash scenarios/hcore-heterogeneous-v0.1/run-step1-bringup.sh
# 退出:   Ctrl-C (trap 会清理全部子进程)
set -euo pipefail
cd "$(dirname "$0")/../.."

# 本机自带 cargo 1.65 不支持 edition 2024; rustup stable 装在 /var/tmp
# (/home 仅剩 ~8G, 工具链与产物不能落在那里)
export RUSTUP_HOME=/var/tmp/rg-rustup CARGO_HOME=/var/tmp/rg-cargo CARGO_TARGET_DIR=/var/tmp/rg-target
. "$CARGO_HOME/env"
export MUJOCO_GL="${MUJOCO_GL:-egl}"

PY=/home/sunweihao/miniconda3/envs/py310/bin/python
RUN=/var/tmp/rg-run/hcore-het-1
rm -rf "$RUN"; mkdir -p "$RUN/artifacts"
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM

# 1. 共享 MuJoCo 世界 + 四个 facade (一个进程托管, 空间冲突真实可观测)
( cd integrations/hcore-mujoco-local-eaios && exec "$PY" -m hcore_mujoco_local_eaios --speed 8 ) \
    > "$RUN/bridge.log" 2>&1 &
for _ in $(seq 30); do curl -sf http://127.0.0.1:28111/v1/health >/dev/null && break; sleep 1; done

# 2. Integration Server: gRPC 50051 / 控制面 8080 / artifact 8090
"$CARGO_TARGET_DIR/debug/integration-server" 127.0.0.1:50051 "$RUN/controller.sqlite3" \
    127.0.0.1:8080 127.0.0.1:8090 "$RUN/artifacts" > "$RUN/server.log" 2>&1 &
for _ in $(seq 30); do curl -sf http://127.0.0.1:8080/healthz >/dev/null && break; sleep 1; done

# 3. 四个节点: 每台节点机器只运行一个 roboguide-node
for n in quadruped-om1 rover-robonix ptz-ros2 arm-roboos; do
    "$CARGO_TARGET_DIR/debug/roboguide-node" \
        "scenarios/hcore-heterogeneous-v0.1/node-$n.toml" > "$RUN/node-$n.log" 2>&1 &
done
for _ in $(seq 60); do
    count=$(curl -sf http://127.0.0.1:8080/v1/inventory 2>/dev/null \
        | "$PY" -c 'import json,sys;print(len(json.load(sys.stdin)["nodes"]))' 2>/dev/null || echo 0)
    [ "$count" = "4" ] && break
    sleep 1
done

curl -sf http://127.0.0.1:8080/v1/inventory -o "$RUN/inventory.json"

echo "=== 四个 facade 的 OS 身份 (节点侧, RoboGuide 不消费) ==="
for p in 28111 28112 28113 28114; do printf "  %s -> " "$p"; curl -sf "http://127.0.0.1:$p/v1/health"; echo; done

echo "=== RoboGuide 控制面 /v1/inventory ==="
"$PY" - "$RUN/inventory.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"  schema   : {d['schema_version']}")
print(f"  node_id           contracts                                    liveness    health")
for n in sorted(d["nodes"], key=lambda x: x["node_id"]):
    print("  {:16s}  {:44s}  {:10s}  {}".format(
        n["node_id"], ",".join(n["contracts"]), n["liveness"], n["reported_health"]))
print("  resources:", ", ".join(r["resource_id"] for n in d["nodes"] for r in n["resources"]))
print("  注: inventory-v0.1 契约 additionalProperties=false 且不含 runtime 字段")
print("      -> 控制面只按 canonical capability 匹配, 这是领域无关性的运行时证据")
EOF

echo "=== 进程 ==="
ps -eo pid,comm | grep -E 'integration-serv|roboguide-node|python' | grep -v grep || true
echo
echo "日志与工件: $RUN"
echo "Ctrl-C 结束"
wait
