#!/usr/bin/env bash
# Step 3 — 把 LLM 主智能体 (Mission Intelligence) 接到这条多本体链上
#
# 与 run-step2-mission.sh 的唯一区别: 任务不再以 5 个写死的 MissionPlan JSON (M0–M4)
# 提交, 而是把**一句自然语言**交给主智能体, 由它现场规划再交给 Control 下发:
#
#   一句自然语言指令 (默认 instructions/01-clear-the-band.txt)
#     -> 主智能体 Mission Intelligence (LLM, 现场决策)
#          Interpreter (指令 -> GroundedIntent)
#          Planner     (意图 -> MissionPlan v0.8)   <- 真实大模型, 非固定模板
#          Reviewer / Repairer (审查与受限修复)
#     -> MissionPlan (roboguide.mission-plan/v0.8)
#     -> Control: Match -> Propose -> Commit (按 canonical capability 选本体, 不看 OS 名)
#     -> 四个 robonix 子智能体: hcore-quadruped / hcore-rover / hcore-ptz / hcore-arm
#     -> 共享 MuJoCo 世界里真实执行 (自建地图 + 真实传感器)
#
# 复用 run-step2 的健壮性处理: CONTROL_PORT 50051 被占用时退到 50052 并把节点配置
# 副本里的 server_endpoint 改道 (只改 $RUN 下的副本, 不动受版本管理的原文件)。
# 复用 run-step3 的主智能体流程: 启动 apps/mission-service, 把指令交给它现场出计划。
#
# 要点: 端口处理沿用本场景 run-step2 (CONTROL_PORT 50051/50052 + NODE_DIR 副本重定向),
# 因为这条链历史上就常和 atlas 抢 50051。
#
# 用法: bash scenarios/hcore-multi-embodiment-v0.1/run-step3-llm-mission.sh [指令文件]
# 环境:
#   RG_INSTRUCTION  自然语言指令文件 (默认 scenarios/.../instructions/01-clear-the-band.txt)
#   RG_RUN          运行目录 (默认 /var/tmp/rg-run/hcore-het-llm-1)
#   RG_SPEED        仿真倍速 (默认 8)
#   RG_MI_TIMEOUT   等待主智能体整条链路返回的墙钟上限, 秒 (默认 3600)
# 前置: OPENAI_API_KEY (可写在仓库根 .env, 本脚本会自动加载)
set -euo pipefail
cd "$(dirname "$0")/../.."

export RUSTUP_HOME=/var/tmp/rg-rustup CARGO_HOME=/var/tmp/rg-cargo CARGO_TARGET_DIR=/var/tmp/rg-target
. "$CARGO_HOME/env"
export MUJOCO_GL="${MUJOCO_GL:-egl}"

PY=/home/sunweihao/miniconda3/envs/py310/bin/python
SCEN=scenarios/hcore-multi-embodiment-v0.1
RUN="${RG_RUN:-/var/tmp/rg-run/hcore-het-llm-1}"
INSTRUCTION_FILE="${RG_INSTRUCTION:-${1:-$SCEN/instructions/01-clear-the-band.txt}}"
MISSION_CONFIG="$SCEN/mission-llm.toml"
MI_TIMEOUT="${RG_MI_TIMEOUT:-3600}"

rm -rf "$RUN"; mkdir -p "$RUN/artifacts" "$RUN/evidence"

cleanup() {
    kill $(jobs -p) 2>/dev/null || true
    # Local EAIOS 层 (世界 + 四个 provider + facade) 跑在独立会话里, jobs 管不到。
    pkill -f "[h]core_mujoco_world_server" 2>/dev/null || true
    pkill -f "[r]obonix_facade" 2>/dev/null || true
    pkill -f "[_]driver/driver.py" 2>/dev/null || true
    pkill -f "[c]hassis_driver.driver" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "### 0. 主智能体的 LLM 凭据"
# 凭据只从进程环境变量读取 (mission/src/mission/config.py), 仓库不会自动加载 .env。
if [ -f .env ]; then
    set -a; . ./.env; set +a
    echo "  已加载 .env"
fi
: "${OPENAI_API_KEY:?主智能体需要 OPENAI_API_KEY (export 或写在仓库根 .env)}"
# 中继是远程明文 HTTP, 主智能体默认拒绝; 场景脚本显式放开。
export ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP="${ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP:-1}"
echo "  模型配置: $MISSION_CONFIG"
echo "  指令文件: $INSTRUCTION_FILE"

echo "### 1. 共享 MuJoCo 世界 + 四个真实 robonix provider + facade"
# 录像、轨迹 JSONL、PTZ 巡检照片这几个开关随假链路一起删掉了: 它们都是 RoleRuntime
# 的能力, 新链路里没有等价项。
RBNX_WORLD_SPEED="${RG_SPEED:-8}" RBNX_RUN_DIR="$RUN/shared-world" \
    bash scenarios/hcore-shared-world.sh > "$RUN/bridge.log" 2>&1

echo "### 2. Integration Server (不传 actor placement: 由 capability constraint 选节点)"
# 50051 同时也是 robonix atlas (注册中心) 的默认端口 —— 两套体系抢同一个端口。
# 而本场景的 Local EAIOS 层 (hcore-shared-world.sh) 起的四个 provider 要就近注册到
# atlas, 所以 atlas 常在, 50051 通常是被占的那一侧。端口空着就用 scenarios/ 下的原
# 节点配置; 被占了就退到 50052, 并把节点配置里的 server_endpoint 改道 —— 只改 $RUN
# 下的副本, 不动受版本管理的原文件。
# 第 6 个位置参数是 actor placement (本场景不用, 传空串让它被忽略), 第 7 个是部署侧的
# 物理实体注册表。主智能体现场产出的是 mission-plan v0.8, 而 v0.8 的物理实体绑定要求
# Control 装有 registry, 否则匹配阶段就会 409 (physical entity registry does not cover
# every constrained candidate Node)。
# 50051-50054 是四个本体的 Atlas (quadruped/rover/arm/ptz), 控制面**永远不用这一段**。
# 不能"先看 50051 空不空再决定": Atlas 是在后台起的, 探测那一刻可能还没绑上, 于是脚本
# 选了 50051, 下一秒 Atlas 起来就 AddrInUse (实测踩过两次)。固定从 50055 起挑。
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
if ! curl -sf http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
    echo "控制面没起来, 看 $RUN/server.log" >&2
    tail -5 "$RUN/server.log" >&2
    exit 1
fi

echo "### 3. 四个 roboguide-node (子智能体)"
# 节点把本地状态写在 config 所在目录下 (相对路径), 必须**先建目录**: 目录不存在
# 时节点直接 Load 失败退出。同时清掉上一轮残留 —— 残留状态会让新会话在 reconnect
# 时看到未知 execution, 进而拒绝路由 ("reconciliation is required before routing")。
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
curl -sf http://127.0.0.1:8080/v1/inventory -o "$RUN/inventory.json"
echo "  在册子智能体: $("$PY" -c 'import json;print(",".join(sorted(n["node_id"] for n in json.load(open("'"$RUN"'/inventory.json"))["nodes"])))')"

echo "### 3.5 等四个节点完成心跳/租约后再提交"
# 节点在册 (inventory) 只说明注册成功; 控制面要等每个节点至少一次心跳才认为
# 它的 capability 与租约可用。首个 Mission 提交过早会一直停在 Ready —— 表现为
# 反复 CandidatesMatched 却永不派发。
"$PY" - <<'EOF'
import json, time, urllib.request

need = {"hcore-arm", "hcore-ptz", "hcore-quadruped", "hcore-rover"}
seen = set()
for _ in range(60):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/v1/events", timeout=5) as r:
            d = json.loads(r.read())
        for e in d.get("events", []):
            p = e.get("payload", {}).get("NodeHeartbeatAccepted")
            if p:
                seen.add(p.get("node_id"))
    except Exception:
        pass
    if need <= seen:
        break
    time.sleep(2)
print("  节点心跳就绪:", "全部" if need <= seen else f"仍缺失 {sorted(need - seen)}")
EOF
sleep 5

echo "### 3.6 生成权威规划世界证据 (部署地图 + 现场雷达回波)"
# 主智能体规划前必须知道"谁还挡在回转环带里"; 这份事实由生产者现算:
#   arena-map.json (人类写的初始位/几何) + /v1/raycast (本体自己的雷达回波)
# 不读仿真器的实体真值坐标 —— 见 hcore_mujoco_world_server/planning_world_evidence.py。
PYTHONPATH="$PWD/integrations/hcore-mujoco-world-server:${PYTHONPATH:-}" \
    "$PY" -m hcore_mujoco_world_server.planning_world_evidence \
    --map "$SCEN/arena-map.json" \
    --world http://127.0.0.1:28100 \
    --out "$RUN/evidence/authoritative-planning-world-evidence.json" \
    --controller "http://127.0.0.1:$CONTROL_PORT" \
    --run-id "run-$(date +%Y%m%dT%H%M%S)" \
    || { echo "  世界证据生成失败, 主智能体无从判断占用"; exit 1; }

echo "### 4. 启动主智能体 (Mission Intelligence, LLM)"
sed -e "s|STATE_DB_PLACEHOLDER|$RUN/mission-service.sqlite3|" \
    -e "s|PLANNING_WORLD_EVIDENCE_PLACEHOLDER|$RUN/evidence/authoritative-planning-world-evidence.json|" \
    "$SCEN/mission-service-llm.toml" > "$RUN/mission-service-llm.toml"
uv run python apps/mission-service/main.py \
    --mission-config "$MISSION_CONFIG" \
    --service-config "$RUN/mission-service-llm.toml" \
    --repository-root "$PWD" > "$RUN/mission-service.log" 2>&1 &
for _ in $(seq 60); do curl -sf http://127.0.0.1:8070/healthz >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8070/healthz >/dev/null || { echo "  主智能体未就绪"; tail -20 "$RUN/mission-service.log"; exit 1; }
echo "  主智能体在线: http://127.0.0.1:8070"

echo "### 5. 把一句自然语言交给主智能体 (同步: 返回即代表整条链路跑完)"
"$PY" - "$RUN" "$INSTRUCTION_FILE" "$MI_TIMEOUT" <<'EOF'
import json, sys, time, urllib.request

run, instruction_path, timeout_s = sys.argv[1], sys.argv[2], float(sys.argv[3])
instruction = open(instruction_path, encoding="utf-8").read().strip()
print(f"  指令: {instruction[:200]}{'...' if len(instruction) > 200 else ''}")
started = time.monotonic()
body = json.dumps({"instruction": instruction}).encode()
req = urllib.request.Request("http://127.0.0.1:8070/v1/mission-requests", data=body,
                             headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=timeout_s) as response:
    record = json.loads(response.read())
elapsed = time.monotonic() - started
json.dump(record, open(f"{run}/mi-record.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(f"  主智能体耗时: {elapsed:.1f}s  request_id={record.get('request_id')} "
      f"mission_id={record.get('mission_id')}")
print(f"  lifecycle: {record.get('lifecycle')}  repair_attempts={record.get('repair_attempts')}")
plan = record.get("plan")
if plan:
    for task in plan.get("tasks", []):
        for role in task.get("roles", []):
            intent = role.get("execution_intent", {})
            op = intent.get("operation", {})
            caps = [c["contract"]["namespace"] + "." + c["contract"]["name"] + "@"
                    + c["contract"]["version"] for c in
                    role.get("requirements", {}).get("capabilities", [])]
            cons = [f"{c['attribute']}={c['value']}" for cap in
                    role.get("requirements", {}).get("capabilities", [])
                    for c in cap.get("constraints", [])]
            print(f"  task {task['id']}: {op.get('namespace')}.{op.get('name')}@{op.get('version')} "
                  f"params={json.dumps(intent.get('parameters'), ensure_ascii=False)} "
                  f"caps={caps} constraints={cons}")
else:
    print("  (未产出计划)")
issues = record.get("issues") or []
if issues:
    print("  issues:")
    for issue in issues:
        print(f"    - {json.dumps(issue, ensure_ascii=False)[:400]}")
if str(record.get("lifecycle", "")).upper() != "ACCEPTED":
    print("  >>> 主智能体没有把计划提交给 Control (见上面的 lifecycle/issues)")
    sys.exit(3)
EOF

echo "### 6. 轮询 Control 侧 Mission 终态"
"$PY" - "$RUN" <<'EOF'
import json, sys, time, urllib.request

run = sys.argv[1]
record = json.load(open(f"{run}/mi-record.json", encoding="utf-8"))
mission_id = record["mission_id"]
deadline = time.monotonic() + 1800
state = {}
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8080/v1/missions/{mission_id}",
                                    timeout=10) as response:
            state = json.loads(response.read())
    except Exception as exc:
        print("  查询失败:", exc)
    status = str(state.get("status", "")).upper()
    if status in ("COMPLETED", "FAILED", "CANCELLED"):
        break
    time.sleep(2)
json.dump(state, open(f"{run}/mission-state.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("  status:", state.get("status"))
def walk(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("state", "task_id", "node_id", "terminal_basis", "reason", "detail"):
                out.append(f"{key}={value}")
            walk(value, out)
    elif isinstance(node, list):
        for value in node:
            walk(value, out)
out: list[str] = []
walk(state, out)
print("  match:", " | ".join(out[:40]))
EOF

echo "### 7. 各子智能体的本地终态依据 (只能由本地观察写入, 控制面读不到)"
"$PY" - "$SCEN" <<'EOF'
import json, os, sqlite3, sys, urllib.request

scen = sys.argv[1]
PORT = {"arm": 28114, "rover": 28112, "quadruped": 28111, "ptz": 28113}


def basis_from_facade(kind: str, handle: str) -> tuple[str, str, str]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT[kind]}/v1/executions/status",
        data=json.dumps({"execution_id": handle}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        return d.get("state", "?"), d.get("detail", ""), d.get("terminal_basis", "")
    except Exception as exc:
        return "?", f"facade unreachable: {exc}", ""


for kind in ("ptz", "rover", "quadruped", "arm"):
    db = os.path.join(scen, f"node-state-{kind}", "execution-journal.sqlite3")
    if not os.path.exists(db):
        continue
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT execution_id, local_handle, status, reason FROM executions "
        "ORDER BY execution_id").fetchall()
    con.close()
    print(f"  [{kind}]")
    for eid, handle, status, reason in rows:
        mark = {"completed": "COMPLETED", "failed": "FAILED"}.get(status, status)
        if handle:
            st, detail, basis = basis_from_facade(kind, handle)
            print(f"    {mark:9s} basis={basis or '-':38s} {detail}")
        else:
            print(f"    {mark:9s} (no local handle) {reason}")
EOF

echo
echo "日志与工件: $RUN"
echo "  主智能体返回体: $RUN/mi-record.json (含现场生成的 MissionPlan 与 lifecycle)"
echo "  Control 终态:   $RUN/mission-state.json"
echo "  主智能体日志:   $RUN/mission-service.log"
echo "  逐条运行轨迹:   $RUN/trajectory-{quadruped,rover,ptz,arm}.jsonl"
