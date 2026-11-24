#!/usr/bin/env bash
# Step 2b — 阶段 2a: 一条**粗目标 -> 子智能体 pilot -> 本体真动**的硬编码链路
#
# 和 run-step2 的区别只有一处, 但那一处就是这阶段要验的东西:
#
#   run-step2:  MissionPlan 里写 destination 地名
#                 -> 节点 -> facade -> 写死翻译 (scene.goal_region -> nav.navigate)
#
#   run-step2b: MissionPlan 里只写一句 instruction (人话, 无坐标无步骤)
#                 -> 节点 -> facade -> **pilot** -> 它自己看 Atlas 能力清单、自己出计划
#                 -> executor 逐个调 (scene / nav) -> 本体真的动
#
# 也就是说: 上层仍然只说"开到北区东侧", 中间那一层从"查表翻译"换成了"现场规划"。
#
# 用法: bash scenarios/hcore-multi-embodiment-v0.1/run-step2b-pilot-mission.sh
# 环境:
#   RG_MISSION        提交哪条计划 (默认 m5-pilot-coarse-goal)
#   RG_RUN            运行目录 (默认 /var/tmp/rg-run/hcore-2a-pilot)
#   RG_SPEED          仿真倍速 (默认 8)
#   RBNX_VLM_UPSTREAM / RBNX_VLM_MODEL  pilot 用的模型网关 (默认与本场景 mission-llm.toml 一致)
#   OPENAI_API_KEY    VLM 凭据 (可从仓库根 .env 加载)
# 前置: 无 (脚本自己起共享世界 / 四个 provider / facade / 控制面 / pilot / executor)
set -euo pipefail
cd "$(dirname "$0")/../.."

export RUSTUP_HOME=/var/tmp/rg-rustup CARGO_HOME=/var/tmp/rg-cargo CARGO_TARGET_DIR=/var/tmp/rg-target
[ -f "$CARGO_HOME/env" ] && . "$CARGO_HOME/env"
export MUJOCO_GL="${MUJOCO_GL:-egl}"

PY=/home/sunweihao/miniconda3/envs/py310/bin/python
SCEN=scenarios/hcore-multi-embodiment-v0.1
RUN="${RG_RUN:-/var/tmp/rg-run/hcore-2a-pilot}"
MISSION="${RG_MISSION:-m5-pilot-coarse-goal}"
ROVER_ATLAS="${RG_ROVER_ATLAS:-127.0.0.1:50052}"
PILOT_LISTEN="${RG_PILOT_LISTEN:-127.0.0.1:50072}"
EXECUTOR_LISTEN="${RG_EXECUTOR_LISTEN:-127.0.0.1:50062}"
# pilot / executor 是 robonix 的上游 crate, 与 RoboGuide 平级 (不在本仓库里),
# 所以只能用绝对路径; 换机器时用 RG_ROBONIX_UPSTREAM 覆盖。
ROBONIX_UPSTREAM="${RG_ROBONIX_UPSTREAM:-$(cd "$(dirname "$0")/../.." && pwd)/../robonix-upstream}"
PILOT_BIN="${RG_PILOT_BIN:-$ROBONIX_UPSTREAM/target/debug/robonix-pilot}"
EXECUTOR_BIN="${RG_EXECUTOR_BIN:-$ROBONIX_UPSTREAM/target/debug/robonix-executor}"
for b in "$PILOT_BIN" "$EXECUTOR_BIN"; do
    [ -x "$b" ] || { echo "找不到可执行文件: $b (先 cargo build -p robonix-pilot -p robonix-executor)"; exit 1; }
done

rm -rf "$RUN"; mkdir -p "$RUN/artifacts" "$RUN/evidence" "$RUN/pilot-sessions"

cleanup() {
    kill $(jobs -p) 2>/dev/null || true
    # Local EAIOS 层 (世界 + 四个 provider + facade) 跑在独立会话里, jobs 管不到。
    pkill -f "[h]core_mujoco_world_server" 2>/dev/null || true
    # 世界服务对 SIGTERM 不敏感 (实测收尾后仍占着 28100, 下一轮直接起不来), 补一刀。
    sleep 1
    pkill -9 -f "[h]core_mujoco_world_server" 2>/dev/null || true
    pkill -f "[r]obonix_facade" 2>/dev/null || true
    pkill -f "[_]driver/driver.py" 2>/dev/null || true
    pkill -f "[c]hassis_driver.driver" 2>/dev/null || true
    pkill -f "[r]obonix-pilot" 2>/dev/null || true
    pkill -f "[r]obonix-executor" 2>/dev/null || true
    # 四个 Atlas 由 run-shared-world.sh 起、不在本进程的 jobs 里; 留着会让下一轮
    # "端口已经有进程在跑" 直接失败。
    pkill -f "[r]obonix-atlas" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "### 0. pilot 的模型凭据"
if [ -f .env ]; then
    set -a; . ./.env; set +a
    echo "  已加载 .env"
fi
: "${OPENAI_API_KEY:?pilot 需要 VLM 凭据 (OPENAI_API_KEY, 或写在仓库根 .env)}"
VLM_UPSTREAM="${RBNX_VLM_UPSTREAM:-http://101.43.45.215:8080/v1}"
VLM_MODEL="${RBNX_VLM_MODEL:-gpt-6-luna}"
echo "  网关=$VLM_UPSTREAM 模型=$VLM_MODEL"
echo "  计划=$MISSION"

echo "### 1. 共享 MuJoCo 世界 + 四个 provider + facade (rover 这一路改走 pilot)"
# 只有 rover 的 facade 打开 pilot 通道; 其余三个角色行为与原来一字不差。
RBNX_WORLD_SPEED="${RG_SPEED:-8}" RBNX_RUN_DIR="$RUN/shared-world" \
    RBNX_FACADE_PILOT_ROLES=rover RBNX_PILOT_ENDPOINT="$PILOT_LISTEN" \
    bash scenarios/hcore-shared-world.sh > "$RUN/bridge.log" 2>&1

echo "### 2. Integration Server (按 capability constraint 选节点)"
# 50051-50054 在这个栈里是**四个本体的 Atlas** (quadruped/rover/arm/ptz), 一定被占。
# 所以控制面不能"退一位到 50052" —— 那正好是 rover 的 Atlas, 实测 AddrInUse 起不来。
# 只往 Atlas 段之外找: 50055 起。
# 50051-50054 是四个本体的 Atlas 区, 控制面永远不用; 也不"先看 50051 空不空" ——
# Atlas 在后台起, 探测那一刻可能还没绑上, 下一秒就 AddrInUse (实测踩过)。
CONTROL_PORT=""
for cand in 50055 50056 50057 50058 50059; do
    if ! ss -ltn 2>/dev/null | grep ":${cand} " >/dev/null; then
        CONTROL_PORT=$cand
        break
    fi
done
[ -n "$CONTROL_PORT" ] || { echo "50055-50059 都被占用, 无法起控制面"; exit 1; }
echo "  integration-server 用 $CONTROL_PORT (Atlas 区 50051-50054 不碰)"
NODE_DIR="$SCEN"
if [ "$CONTROL_PORT" != "50051" ]; then
    mkdir -p "$RUN/nodes"
    for n in quadruped-om1 rover-robonix ptz-ros2 arm-roboos; do
        sed "s#127.0.0.1:50051#127.0.0.1:${CONTROL_PORT}#" \
            "$SCEN/node-$n.toml" > "$RUN/nodes/node-$n.toml"
    done
    NODE_DIR="$RUN/nodes"
fi

"$CARGO_TARGET_DIR/debug/integration-server" "127.0.0.1:$CONTROL_PORT" "$RUN/controller.sqlite3" \
    127.0.0.1:8080 127.0.0.1:8090 "$RUN/artifacts" "" \
    "$SCEN/physical-entity-registry.json" > "$RUN/server.log" 2>&1 &
for _ in $(seq 30); do curl -sf http://127.0.0.1:8080/healthz >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8080/healthz >/dev/null || { echo "  控制面没起来"; tail -20 "$RUN/server.log"; exit 1; }

echo "### 3. 四个 roboguide-node"
rm -rf "$SCEN"/node-state-*
mkdir -p "$SCEN"/node-state-{arm,ptz,quadruped,rover}
for n in quadruped-om1 rover-robonix ptz-ros2 arm-roboos; do
    "$CARGO_TARGET_DIR/debug/roboguide-node" "$NODE_DIR/node-$n.toml" > "$RUN/node-$n.log" 2>&1 &
done
for _ in $(seq 60); do
    count=$(curl -sf http://127.0.0.1:8080/v1/inventory 2>/dev/null \
        | "$PY" -c 'import json,sys;print(len(json.load(sys.stdin)["nodes"]))' 2>/dev/null || echo 0)
    [ "$count" = "4" ] && break
    sleep 1
done
curl -sf http://127.0.0.1:8080/v1/inventory -o "$RUN/inventory.json" || true
echo "  在册子智能体: $("$PY" -c 'import json;print(",".join(sorted(n["node_id"] for n in json.load(open("'"$RUN"'/inventory.json"))["nodes"])))' 2>/dev/null || echo '?')"

echo "### 4. pilot + executor (接 rover 的 Atlas)"
# pilot 只注册在它服务的那台本体的 Atlas 上: 它看到的 scene/nav 就是这台车的,
# 不会拿到狗或机械臂的能力去规划。
SESS="$RUN/pilot-sessions"
SCRIBE_CONSOLE_LEVEL=warn ROBONIX_SESSION_DIR="$SESS" \
    "$EXECUTOR_BIN" --atlas "$ROVER_ATLAS" --listen "$EXECUTOR_LISTEN" --id executor-rover \
    > "$RUN/executor.log" 2>&1 &
ROBONIX_SESSION_DIR="$SESS" \
    "$PILOT_BIN" --atlas "$ROVER_ATLAS" --listen "$PILOT_LISTEN" --id pilot-rover \
    --vlm-upstream "$VLM_UPSTREAM" --vlm-model "$VLM_MODEL" \
    --vlm-api-key "$OPENAI_API_KEY" --vlm-format openai \
    > "$RUN/pilot.log" 2>&1 &
for _ in $(seq 60); do
    ss -ltn 2>/dev/null | grep -q ":${PILOT_LISTEN##*:} " && break
    sleep 1
done
ss -ltn 2>/dev/null | grep -q ":${PILOT_LISTEN##*:} " || {
    echo "  pilot 没起来, 看 $RUN/pilot.log"; tail -20 "$RUN/pilot.log"; exit 1; }
echo "  pilot 在 $PILOT_LISTEN, executor 在 $EXECUTOR_LISTEN"

echo "### 4.5 rover 初始位 (任务前后的位移就是这条链的证据)"
"$PY" - <<'EOF' | tee "$RUN/rover-before.txt"
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:28100/v1/entity/rover", timeout=5) as r:
    pos = json.loads(r.read())["pos"]
print(f"  rover before: ({pos[0]:.3f}, {pos[1]:.3f})")
EOF

echo "### 5. 提交粗目标计划 ($MISSION)"
curl -sf -X POST http://127.0.0.1:8080/v1/missions \
    -H 'Content-Type: application/json' --data-binary "@$SCEN/missions/$MISSION.json" \
    -o "$RUN/submit.json" || { echo "  提交失败"; tail -5 "$RUN/server.log"; exit 1; }
MID=$("$PY" -c 'import json;print(json.load(open("'"$RUN"'/submit.json"))["mission_id"])' 2>/dev/null \
      || "$PY" -c 'import json;print(json.load(open("'"$SCEN"'/missions/'"$MISSION"'.json"))["mission"]["id"])')
echo "  mission_id=$MID"

STATUS=""
for _ in $(seq 900); do
    curl -sf "http://127.0.0.1:8080/v1/missions/$MID" -o "$RUN/mission-state.json" || true
    STATUS=$("$PY" -c 'import json;print(str(json.load(open("'"$RUN"'/mission-state.json")).get("status","")).upper())' 2>/dev/null || echo "")
    case "$STATUS" in COMPLETED|FAILED|CANCELLED) break ;; esac
    sleep 2
done
echo "  mission status: $STATUS"

echo "### 6. rover 自己的终态依据 (只能由本地观察/pilot 那一轮写入)"
# 读之前先说明口径: 这里的 COMPLETED 只表示"pilot 那一轮的能力调用没有失败",
# 不表示"粗目标达成了"。pilot 的 final_text 可能自己说"没到指定坐标"(实测有),
# 而节点仍旧 COMPLETED —— 两者是不同的两件事。目标是否达成要靠 verifier (M 的
# satisfaction.verifier), 那是 2b/3 的活; 2a 只证明"粗目标能真的驱动本体"。
"$PY" - "$SCEN" "$RUN" <<'EOF'
import json, os, sqlite3, sys, urllib.request
scen, run = sys.argv[1], sys.argv[2]
# 节点把 state_directory 相对**配置文件所在目录**解析: 控制面端口被占用时用的是
# $RUN/nodes 下的配置副本, 日志就落在那里而不是 scenarios/ 下。两处都找。
cands = [os.path.join(scen, "node-state-rover", "execution-journal.sqlite3"),
         os.path.join(run, "nodes", "node-state-rover",
                      "execution-journal.sqlite3")]
db = next((p for p in cands if os.path.exists(p)), "")
if not db:
    print("  没有 rover 的执行日志"); raise SystemExit
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = con.execute("SELECT execution_id, local_handle, status, reason FROM executions"
                   " ORDER BY execution_id").fetchall()
con.close()
for eid, handle, status, reason in rows:
    if not handle:
        print(f"  {status:9s} (no local handle) {reason}")
        continue
    req = urllib.request.Request("http://127.0.0.1:28112/v1/executions/status",
                                 data=json.dumps({"execution_id": handle}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
    print(f"  {d.get('state','?'):9s} basis={d.get('terminal_basis','-'):24s} {d.get('detail','')}")
EOF

echo
echo "### 7. rover 终态位"
"$PY" - <<'EOF' | tee "$RUN/rover-after.txt"
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:28100/v1/entity/rover", timeout=5) as r:
    pos = json.loads(r.read())["pos"]
print(f"  rover after:  ({pos[0]:.3f}, {pos[1]:.3f})  目标 (1.6, 1.6)")
EOF

echo
echo "日志与工件: $RUN"
echo "  pilot 输出:   $RUN/pilot.log (它的 TEXT/PLAN/NODE 全文)"
echo "  facade 输出:  $RUN/shared-world/mujoco_facade*.log (/var/tmp 下按角色分文件)"
echo "  Control 终态: $RUN/mission-state.json"
echo "  粗目标原文:   $SCEN/missions/$MISSION.json"

[ "$STATUS" = "COMPLETED" ] || { echo ">>> 计划没成功, 看上面的 detail 与 pilot.log"; exit 3; }
