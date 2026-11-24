#!/usr/bin/env bash
# Step 1 (cargo 产物直跑) — 共享世界 + 四个**真实 robonix provider** 作为 Local EAIOS。
#
# Local EAIOS 层与 run-step1-bringup.sh **完全一样** (同一份
# scenarios/hcore-shared-world.sh, 同一组端口 28111-28114, 同一套 REST 契约) ——
# 那条"一个 Python 进程假装的四个角色"的旧链路已整体删除, 所以两者不再有真假之分。
# 这里多出来的只有两件环境上的事:
#   * 直接跑 $CARGO_TARGET_DIR/debug 下的 cargo 产物, 并自己设 RUSTUP_HOME/CARGO_HOME;
#   * 50051 被 robonix atlas 占用时, 自动把 integration-server 退到 50052, 并改道
#     节点配置副本 (robonix 的 atlas 默认端口就是 50051, 两套体系会抢同一个端口)。
#
# 节点配置一个字都没改 —— 还是 scenarios/hcore-multi-embodiment-v0.1/node-*.toml。
#
# 验收点: 四个节点同时在册, 且每个节点上报的能力 = 它背后 provider **真正拥有**的能力
# (chassis 没有感知能力, 它的端点就答 UNAVAILABLE —— 不借 ptz 的能力凑清单)。
#
# 用法: bash scenarios/hcore-multi-embodiment-v0.1/run-step1-bringup-native-robonix.sh
# 退出: Ctrl-C (trap 会清理全部子进程, 包括独立会话里的世界与 provider)
set -euo pipefail
cd "$(dirname "$0")/../.."

# 本机自带 cargo 1.65 不支持 edition 2024; rustup stable 装在 /var/tmp
export RUSTUP_HOME=/var/tmp/rg-rustup CARGO_HOME=/var/tmp/rg-cargo CARGO_TARGET_DIR=/var/tmp/rg-target
. "$CARGO_HOME/env"
export MUJOCO_GL="${MUJOCO_GL:-egl}"
export RBNX_WORLD_SPEED="${RBNX_WORLD_SPEED:-8}"

PY=/home/sunweihao/miniconda3/envs/py310/bin/python
RUN=/var/tmp/rg-run/hcore-het-2-native
rm -rf "$RUN"; mkdir -p "$RUN/artifacts"

cleanup() {
    kill $(jobs -p) 2>/dev/null || true
    # run-shared-world.sh 的世界与 provider 跑在独立会话里, jobs 管不到它们, 单独收。
    pkill -f "[h]core_mujoco_world_server" 2>/dev/null || true
    pkill -f "[_]driver/driver.py" 2>/dev/null || true
    pkill -f "[c]hassis_driver.driver" 2>/dev/null || true
    pkill -f "[r]obonix_facade" 2>/dev/null || true
    # 隔离改造后 run-shared-world.sh 会起四个 Atlas + 四份 scene/两份 nav, 一并收掉。
    pkill -f "[r]obonix-atlas" 2>/dev/null || true
    pkill -f "[m]ujoco_scene.service" 2>/dev/null || true
    pkill -f "[m]ujoco_nav.service" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 1. 共享世界 + 四个真实 provider + 四个 Local EAIOS 端点 (28111-28114)。
( setsid bash robonix-src/examples/mujoco/run-shared-world.sh > "$RUN/bridge-native.log" 2>&1 & )
for _ in $(seq 240); do
    ready=1
    for p in 28111 28112 28113 28114; do
        curl -sf "http://127.0.0.1:$p/v1/health" >/dev/null 2>&1 || ready=0
    done
    [ "$ready" = "1" ] && break
    sleep 1
done
if [ "${ready:-0}" != "1" ]; then
    echo "四个 Local EAIOS 端点没全部就绪, 看 $RUN/bridge-native.log" >&2
    exit 1
fi

# 2. Integration Server: gRPC 50051 / 控制面 8080 / artifact 8090
#
# 50051 同时也是 robonix 的 atlas (注册中心) 的默认端口 —— 两套体系抢同一个端口。
# 端口空着就用 scenarios/ 下的原节点配置; 被占了就退到 50052, 并把节点配置里的
# server_endpoint 改道 (只改 $RUN 下的副本, 不动受版本管理的原文件)。
CONTROL_PORT=50051
if ss -ltn 2>/dev/null | grep -q ':50051 '; then
    CONTROL_PORT=50052
    echo "[warn] 50051 被占用: $(ss -ltnp 2>/dev/null | grep ':50051 ' | sed 's/.*users://')"
    echo "[warn] integration-server 改用 $CONTROL_PORT, 节点配置副本指向同一端口"
fi
NODE_DIR="scenarios/hcore-multi-embodiment-v0.1"
if [ "$CONTROL_PORT" != "50051" ]; then
    mkdir -p "$RUN/nodes"
    for n in quadruped-om1 rover-robonix ptz-ros2 arm-roboos; do
        sed "s#127.0.0.1:50051#127.0.0.1:${CONTROL_PORT}#" \
            "scenarios/hcore-multi-embodiment-v0.1/node-$n.toml" > "$RUN/nodes/node-$n.toml"
    done
    NODE_DIR="$RUN/nodes"
fi

"$CARGO_TARGET_DIR/debug/integration-server" "127.0.0.1:$CONTROL_PORT" "$RUN/controller.sqlite3" \
    127.0.0.1:8080 127.0.0.1:8090 "$RUN/artifacts" > "$RUN/server.log" 2>&1 &
for _ in $(seq 30); do curl -sf http://127.0.0.1:8080/healthz >/dev/null && break; sleep 1; done

# 3. 四个节点: 每台节点机器只运行一个 roboguide-node
for n in quadruped-om1 rover-robonix ptz-ros2 arm-roboos; do
    "$CARGO_TARGET_DIR/debug/roboguide-node" \
        "$NODE_DIR/node-$n.toml" > "$RUN/node-$n.log" 2>&1 &
done
for _ in $(seq 60); do
    count=$(curl -sf http://127.0.0.1:8080/v1/inventory 2>/dev/null \
        | "$PY" -c 'import json,sys;print(len(json.load(sys.stdin)["nodes"]))' 2>/dev/null || echo 0)
    [ "$count" = "4" ] && break
    sleep 1
done

curl -sf http://127.0.0.1:8080/v1/inventory -o "$RUN/inventory.json" || true

echo "=== 四个端点的 OS 身份 (节点侧, RoboGuide 不消费) ==="
for p in 28111 28112 28113 28114; do printf "  %s -> " "$p"; curl -sf "http://127.0.0.1:$p/v1/health"; echo; done

echo "=== RoboGuide 控制面 /v1/inventory ==="
"$PY" - "$RUN/inventory.json" <<'EOF'
import json
import sys

d = json.load(open(sys.argv[1]))
print(f"  schema   : {d['schema_version']}")
print("  node_id           contracts                                      liveness    health")
for n in sorted(d["nodes"], key=lambda x: x["node_id"]):
    print("  {:16s}  {:46s}  {:10s}  {}".format(
        n["node_id"], ",".join(n["contracts"]), n["liveness"], n["reported_health"]))
print("  注: 每个节点的 contracts 就是它背后那个真实 provider 的能力集;")
print("      chassis 端点没有 observation.*, 所以 rover 节点只报 mobility.navigate。")
EOF

echo "=== 进程 ==="
ps -eo pid,comm | grep -E 'integration-serv|roboguide-node|world_server|driver.py' | grep -v grep || true
echo
echo "日志与工件: $RUN"
echo "Ctrl-C 结束"
wait
