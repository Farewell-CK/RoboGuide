# 验收判据 · mujoco-push-v0.1

## 主判据（任务层）

| 指标 | 阈值 | 来源 |
|---|---|---|
| 箱子真值到目标点距离 | ≤ 0.30 m | `RBNX_PUSH_ACCEPT_TOL` |
| 信念与真值偏差 | ≤ 0.15 m | `scenarios/mujoco-push-v0.1/analyze-drift.py` |
| "推了但没统计" 次数 | 0 | 同上（真值动 ≥0.15 而信念未动） |
| "凭空跳变" 次数 | 0 | 同上（信念动 ≥0.15 而真值未动） |

## 过程判据（推挤几何）

| 指标 | 阈值 | 说明 |
|---|---|---|
| 推前后方（车在箱子后方） | ≤ −0.15 m | 为正表示车在目标一侧，会推反 |
| 推前横向偏置 | ≤ 0.35 m | 过大即侧推，箱子横滑 |
| 车头与"车→箱子中心"夹角 | ≤ 10° | `_push_toward` 内已校验 |
| 单轮有效推进 | 0.20~0.40 m | 低于 0.15 视为空推 |

## 读链路判据（RoboGuide 侧）

| 指标 | 期望 |
|---|---|
| `roboguide nodes` | 至少 1 个节点（`mujoco-rover` / `mujoco-arm`） |
| `roboguide capabilities <node>` | 含 `object.relocate@v1`（arm）或 `mobility.navigate@v1`（rover） |
| `chat --ask "现在有哪些节点"` | 节点数一致，且带"依据：本轮 Controller 读取" |

## 已知环境约束

- `/v1/place` 必须带 `z ≈ 0.45`，否则箱子与地面穿插被物理弹飞 5~7 m；
- 车（rover）没有 freejoint，不能 `place`，只能用"反向摆放箱子"构造初始偏差；
- 世界内 `arm_base(-1.20,-0.40)` 会挡住箱子正后方（初始位置），需允许 ±35°/±60° 侧后方站位。
