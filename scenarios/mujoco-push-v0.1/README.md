# MuJoCo 推箱场景 v0.1

用 MuJoCo 里的 rover 把箱子 `obs2` 推到目标点，并把它接入 RoboGuide 的节点体系
（`roboguide chat` → Mission → 节点 → `robonix_facade.py` → 世界）。

## 组成

| 文件 | 作用 |
|---|---|
| `actor-placement.json` | 实体摆位（箱子 / 标志桩 / 机械臂基座 / 目标点 / 车部署位） |
| `rover-node-v0.1.toml` | rover 节点配置（`driver="http"`，端点 `127.0.0.1:28112`，契约 `mobility.navigate@v1`） |
| `arm-node-v0.1.toml` | arm 节点配置（端点 `127.0.0.1:28114`，契约 `object.relocate@v1`） |
| `mission-push-clear-zone.json` | Mission 定义：`object.relocate` + `clear_keep_out_zone` 参数 |
| `acceptance.md` | 验收判据（真值、信念一致性、推挤几何） |
| `exp-push-bias.py` | 受控实验：空旷场地，只放目标箱，测"初始横向偏差 → 推偏修正" |
| `exp-belief.py` | 受控实验：只测定位（给故意偏移的先验，测不到立即停） |
| `trace-truth.py` | 每 2 s 采样 `obs2` 真值 → CSV |
| `analyze-drift.py` | 把日志"第 N 轮 箱子(x,y)"与真值按时间对齐，归因"推了但没统计 / 凭空跳变" |

> 仿真实现（`robonix_facade.py`、`mujoco_nav`、`mujoco_scene`）位于 `robonix-src/`，
> 按仓库约定**不入库**（上游源码树，本地检出）。

## 前置服务

```bash
# 1. 世界服务（MuJoCo）
python -m hcore_mujoco_world_server --port 28100 --speed 8

# 2. 导航 MCP
python -m mujoco_nav.service                     # :41825/mcp

# 3. facade（每角色一个 HTTP 端点，供 RoboGuide 节点使用）
python robonix-src/examples/mujoco/facade/robonix_facade.py --role rover   # :28112
python robonix-src/examples/mujoco/facade/robonix_facade.py --role arm     # :28114

# 4. RoboGuide controller / mission（chat 用）
./target/release/integration-server 127.0.0.1:50151 roboguide-controller.sqlite3 \
    127.0.0.1:8080 127.0.0.1:8090
ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP=1 .venv/bin/mission-service \
    --mission-config config/mission.toml \
    --service-config config/mission-service.toml --repository-root .
```

## 起节点（把仿真接进 RoboGuide）

```bash
./target/release/roboguide-node --validate scenarios/mujoco-push-v0.1/rover-node-v0.1.toml
./target/release/roboguide-node scenarios/mujoco-push-v0.1/rover-node-v0.1.toml

# 验证
.venv/bin/python -m roboguide_workbench.cli nodes        # 应出现 mujoco-rover
```

## 跑实验

```bash
export RBNX_WORLD_URL=http://127.0.0.1:28100 RBNX_PUSH_POLICY=local
export RBNX_ARENA_MAP=$PWD/scenarios/hcore-multi-embodiment-v0.1/arena-map.json
export RBNX_ROBOT_GEOMETRY=$PWD/scenarios/hcore-multi-embodiment-v0.1/robot-geometry.json

# 受控：空旷场地（箱子 0.9,-1.6 → 目标 0.9,-0.6）
python scenarios/mujoco-push-v0.1/exp-push-bias.py --box 0.9,-1.6 --target 0.9,-0.6 --budget 150

# 受控：初始横向偏差 0.00 / 0.20 / 0.40
python scenarios/mujoco-push-v0.1/exp-push-bias.py --offsets 0.00,0.20,0.40 --budget 60

# 定位专测（测不到即停）
python scenarios/mujoco-push-v0.1/exp-belief.py --offset 0.60 --n 4
```

## 归因（整轮跑批时用）

```bash
python scenarios/mujoco-push-v0.1/trace-truth.py --seconds 900 --period 2 --out /var/tmp/truth.csv &
python scenarios/mujoco-push-v0.1/analyze-drift.py --log <facade.log> --trace /var/tmp/truth.csv
```

## 已知坑

1. **`/v1/place` 必须带 `z ≈ 0.45`**：不带 z 会放在 z=0，与地面穿插后物理瞬间把箱子弹出 5~7 m。
2. **车不能 `place`**（无 freejoint，接口返回 404）：实验需"反向摆放箱子"来构造初始偏差。
3. **推后测量失败的根因是自车剔除**：贴住时箱子近侧面落在剔除区内，已改为按车体矩形剔除。
4. **正后方可能被 `arm_base` 挡住**：候选按"离固定件多远 + |偏角|"排序，侧后方是合法退让。
