#!/usr/bin/env bash
# Step 2 — 用 MissionPlan v0.7 调度四种 EAIOS: 机械臂双向回转 + 跨 OS 消除障碍
#
# 初始状态 (第 0 帧就已经双向遮挡, 无需任何"制造障碍"的前置动作):
#   狗 (quadruped)  停在方位 -45°、r=0.92 -> 封住**长弧**
#   obs2 (可推障碍) 停在方位 +45°、r=0.90 -> 封住**短弧**
#   crate1 (目标物) 在方位 +176° (机械臂正后方), 落在两者的夹角外
# 五个 Mission 依次提交, 构成一条跨操作系统的恢复链:
#   M0 PTZ 云台环绕场景一周: 四个方位各拍一张照片 -> 本地判定 crate1 是否在
#      机械臂抓取范围内 (门禁: 不在范围内则后续四步不再提交)
#   M1 机械臂左右各摆一次试探 -> 两侧皆挡 -> 本地 FAILED (both-directions)
#   M2 狗发现挡路后自行走开 -> 让出长弧
#   M3 地面平台把可移动障碍顶出扇区 -> 让出短弧
#   M4 机械臂转 180° 到正后方取物, 再转回起始方位放下 -> COMPLETED (placed)
#
# 用法: bash scenarios/hcore-homogeneous-v0.2/run-step2-mission.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

export RUSTUP_HOME=/var/tmp/rg-rustup CARGO_HOME=/var/tmp/rg-cargo CARGO_TARGET_DIR=/var/tmp/rg-target
. "$CARGO_HOME/env"
export MUJOCO_GL="${MUJOCO_GL:-egl}"

PY=/home/sunweihao/miniconda3/envs/py310/bin/python
RUN=/var/tmp/rg-run/hcore-hom-2
SCEN=scenarios/hcore-homogeneous-v0.2
rm -rf "$RUN"; mkdir -p "$RUN/artifacts"
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM

echo "### 1. 共享 MuJoCo 世界 + 四个 facade"
# RG_RECORD=<路径.mp4> 时顺带离屏录像: 渲染开销会拉长墙钟, 但视频时长按**仿真
# 时间**计 (与倍速无关), 画面不会快进。RG_RECORD_FPS 可调帧率 (默认 15)。
# 两个去空镜头的开关:
#   --record-defer      录像从**第一个 execution 到达本地 facade** 才开始写帧,
#                       剪掉"起服务/等注册/等心跳/等租约"那一大段静止。
#   --record-skip-idle  跳过"四个本体都没在跑"的仿真步, 剪掉控制面在 Mission/Task
#                       之间重走 Match -> Propose -> Commit 的那几秒静止。
# 因此视频时长 **短于** 仿真/墙钟时长, 但没有任何一段是不动的画面。
FACADE_ARGS=(--speed "${RG_SPEED:-8}" \
             --runtime-name "${RG_RUNTIME_NAME:-robonix-os}" \
             --slam "${RG_SLAM:-on}" --slam-seed "${RG_SLAM_SEED:-7}" \
             --slam-dump "$RUN/artifacts")
# 上帝地图回退开关: RG_NO_WORLD_FALLBACK=1 时, 自己的地图规划不出来就判失败,
# 不再悄悄换成仿真器栅格。这是"自建地图"claim 的硬约束 —— 上一轮实测 M3 的
# 后两次导航都走了回退, 留着它时任务能过但结论不成立。
if [ "${RG_NO_WORLD_FALLBACK:-0}" = "1" ]; then
    FACADE_ARGS+=(--no-world-fallback)
    echo "  上帝地图回退: 关闭 (规划只照自己建的地图)"
fi
if [ -n "${RG_RECORD:-}" ]; then
    FACADE_ARGS+=(--record "$RG_RECORD" --record-fps "${RG_RECORD_FPS:-15}" \
                  --record-defer --record-skip-idle)
    echo "  录制: $RG_RECORD (@${RG_RECORD_FPS:-15}fps, 剪掉片头与任务间静止)"
fi
# M0 巡检的四张照片落盘目录 (execution 参数 out_dir 可覆盖)
FACADE_ARGS+=(--shot-dir "$RUN/artifacts/ptz-survey")
# 逐条运行轨迹: 每个本体一个 JSONL (accepted / sample / terminal 三种事件)。
# 采样按仿真时间, 倍速不影响轨迹密度; RG_TRACE_INTERVAL 可调采样间隔 (默认 1 s)。
FACADE_ARGS+=(--trace-dir "$RUN" --trace-interval "${RG_TRACE_INTERVAL:-1.0}")
( cd integrations/hcore-mujoco-local-eaios && exec "$PY" -m hcore_mujoco_local_eaios "${FACADE_ARGS[@]}" ) \
    > "$RUN/bridge.log" 2>&1 &
for _ in $(seq 30); do curl -sf http://127.0.0.1:28114/v1/health >/dev/null && break; sleep 1; done
for p in 28111 28112 28113 28114; do printf "  %s -> " "$p"; curl -sf "http://127.0.0.1:$p/v1/health"; echo; done

echo "### 2. Integration Server (不传 actor placement: 由 capability constraint 选节点)"
"$CARGO_TARGET_DIR/debug/integration-server" 127.0.0.1:50051 "$RUN/controller.sqlite3" \
    127.0.0.1:8080 127.0.0.1:8090 "$RUN/artifacts" > "$RUN/server.log" 2>&1 &
for _ in $(seq 30); do curl -sf http://127.0.0.1:8080/healthz >/dev/null && break; sleep 1; done

echo "### 3. 四个 roboguide-node"
# 节点把本地状态写在 config 所在目录下 (相对路径), 必须**先建目录**: 目录不存在
# 时节点直接 Load 失败退出。同时清掉上一轮残留 —— 残留状态会让新会话在 reconnect
# 时看到未知 execution, 进而拒绝路由 ("reconciliation is required before routing")。
rm -rf "$SCEN"/node-state-*
mkdir -p "$SCEN"/node-state-{arm,ptz,quadruped,rover}
for n in quadruped-om1 rover-robonix ptz-ros2 arm-roboos; do
    "$CARGO_TARGET_DIR/debug/roboguide-node" "$SCEN/node-$n.toml" > "$RUN/node-$n.log" 2>&1 &
done
for _ in $(seq 60); do
    count=$(curl -sf http://127.0.0.1:8080/v1/inventory 2>/dev/null \
        | "$PY" -c 'import json,sys;print(len(json.load(sys.stdin)["nodes"]))' 2>/dev/null || echo 0)
    [ "$count" = "4" ] && break
    sleep 1
done
curl -sf http://127.0.0.1:8080/v1/inventory -o "$RUN/inventory.json"
echo "  在册节点: $("$PY" -c 'import json;print(",".join(sorted(n["node_id"] for n in json.load(open("'"$RUN"'/inventory.json"))["nodes"])))')"

echo "### 3.5 等四个节点完成心跳/租约后再提交"
# 节点在册 (inventory) 只说明注册成功; 控制面要等每个节点至少一次心跳才认为
# 它的 capability 与租约可用。首个 Mission 提交过早会一直停在 Ready —— 表现为
# 反复 CandidatesMatched 却永不派发, 而随后的 Mission 因为已经热好就能正常跑。
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

echo "### 4. 依次提交五个 MissionPlan v0.7 (M0 是相机巡检门禁)"
for m in m0-ptz-survey-reach m1-arm-sweep-blocked-both m2-dog-clears-sector \
         m3-rover-removes-obstacle m4-arm-pick-and-place; do
    echo "--- $m"
    curl -sf -X POST http://127.0.0.1:8080/v1/missions \
        -H 'Content-Type: application/json' --data-binary "@$SCEN/missions/$m.json" \
        -o "$RUN/submit-$m.json" || { echo "  提交失败"; tail -5 "$RUN/server.log"; continue; }
    "$PY" - "$RUN/submit-$m.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
print("   submit:", json.dumps(d, ensure_ascii=False)[:220])
EOF
    mid=$("$PY" -c 'import json;print(json.load(open("'"$SCEN"'/missions/'"$m"'.json"))["mission"]["id"])')
    # 轮询到终态
    for _ in $(seq 120); do
        sleep 2
        curl -sf "http://127.0.0.1:8080/v1/missions/$mid" -o "$RUN/state-$m.json" || continue
        # 只看 **Mission 顶层** status, 且大小写不敏感 (控制面返回驼峰
        # \"Completed\", 本地 facade 用全大写)。不能在整个文本里搜: Running 的
        # Mission 含有已完成的子任务, 会被误判成终态而提前退出轮询。
        if "$PY" -c "
import json,sys
d=json.load(open('$RUN/state-$m.json'))
sys.exit(0 if str(d.get('status','')).upper() in ('COMPLETED','FAILED','CANCELLED') else 1)"; then
            break
        fi
    done
    "$PY" - "$RUN/state-$m.json" "$m" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
def walk(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("state", "task_id", "task_ref", "node_id", "terminal_basis",
                     "reason", "detail", "execution_state", "local_state"):
                out.append(f"{k}={v}")
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)
out = []
walk(d, out)
print("   state:", json.dumps(d, ensure_ascii=False)[:600])
# 每个 Task 实际匹配到哪个节点 —— 这是 capability constraint 生效的直接证据:
# 需要推障碍的 Task 必须只匹配 hcore-rover, 只需走过去停着的必须只匹配 hcore-quadruped。
print("   match:", " | ".join(out))
EOF
    # M0 是门禁: 相机拍完四个面后本地判定"目标不在机械臂抓取范围内" -> 停在这里,
    # 后面的清障与搬运链不再提交 (依据与照片路径见上面的 state/detail)。
    if [ "$m" = "m0-ptz-survey-reach" ]; then
        if [ ! -f "$RUN/state-$m.json" ] || "$PY" -c "
import json,sys
d=json.load(open('$RUN/state-$m.json'))
sys.exit(0 if str(d.get('status','')).upper() in ('FAILED','CANCELLED') else 1)"; then
            echo "  >>> 目标不在机械臂抓取范围内, 后续四步不再提交"
            exit 0
        fi
        echo "  >>> 目标在机械臂抓取范围内, 继续后续四步"
    fi
done

echo "### 5. 本地终态依据 (只能由本地观察写入, 控制面读不到也推导不出)"
"$PY" - "$SCEN" <<'EOF'
import json, os, sqlite3, sys, urllib.request
scen = sys.argv[1]
# 节点 -> 本地 facade 端口 (本地 handle 只在 Local EAIOS 侧可解析)
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
    except Exception as exc:  # noqa: BLE001
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
echo "逐条运行轨迹: $RUN/trajectory-{quadruped,rover,ptz,arm}.jsonl "
echo "  (事件: accepted=登记参数 / sample=逐秒明细+姿态 / terminal=终态与本地依据)"
echo "查看实时事件: curl -s http://127.0.0.1:8080/v1/events | python -m json.tool"
