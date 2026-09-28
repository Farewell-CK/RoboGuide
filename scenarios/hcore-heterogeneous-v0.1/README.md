# 场景: H-CoRE 异构三体 (三种具身操作系统) — `hcore-heterogeneous-v0.1`

用 RoboGuide 协调三个**控制栈不同**的机器人, 验证"领域无关的多 EAIOS 调度"。

## 验证目标

RoboGuide 只看到 canonical `ExecutionIntent` 与本地证据, 全程不知道底下是哪种
具身操作系统、也不知道物理后端是 MuJoCo。要验的正是这一点 (ADR-0006)。

## 拓扑

```
Mission/Controller(8080) ──► Integration Server gRPC(50051)
                                     │  Node Protocol v0.4
        ┌──────────────┬──────────────┬──────────────┴──────────────┐
  roboguide-node   roboguide-node   roboguide-node             roboguide-node
  hcore-quadruped  hcore-rover      hcore-ptz                  hcore-arm
  openmind-om1     robonix-os       ros2-nav2-moveit2          roboos
  :28111           :28112           :28113                     :28114
        └──────────────┴──────────────┴─────────────────────────────┘
                   同一个 MuJoCo 世界 arena_roboguide.xml
      (四足狗 / 履带车 / PTZ 云台 / 机械臂共处, 空间冲突真实可观测)
```

| 节点 | 本体 | Local EAIOS | canonical operation | 能力集 |
|---|---|---|---|---|
| `hcore-quadruped` | 四足狗 | OpenMind OM1 | `mobility.navigate@v1` | ArUco |
| `hcore-rover` | 履带车 | Robonix OS | `mobility.navigate@v1` + `observation.verify@v1` | ArUco + 物体 |
| `hcore-ptz` | PTZ 云台 | ROS 2 + Nav2/MoveIt 2 | `observation.verify@v1` + `observation.survey@v1` | QR + 巡检拍照 |
| `hcore-arm` | 固定基座 360° 回转臂 | RoboOS | `object.relocate@v1` | 搬运 |

### 机械臂的三条硬规则 (对应用户需求)

1. **目标在臂展内 -> 直接抓取搬运**: 抓取点半径落在 `[0.22, 1.30] m` 且二连杆有解时,
   回转 -> 抓取 -> 回转 -> 放置, 终态 `COMPLETED` / 依据 `local-manipulation-placed`。
2. **回转是双向的**: `arm_yaw` 无 joint 限位 (真·连续回转), 因此短弧被挡时改为
   **反向绕行的长弧**; 两条弧都被挡才判失败。判定在**每个物理步**重算, 障碍在
   回转途中才进入扇区 -> 立即松手停机。
3. **两侧皆挡 -> `local-workspace-blocked-both-directions`**: 明细里分别点名两侧的
   阻挡实体 (如 `short by body:obs2; long by body:dog`)。这是本地依据,
   RoboGuide 侧只看到 `FAILED` + 该字符串 —— 它不知道也不关心是谁挡的。

扇区内的实体包括 5 个固定凸障碍、**可推动障碍 `obs2`**、以及共享世界里停进来的
**rover / 狗** —— 后两者使"障碍"可被**其它 EAIOS 动态制造与消除**, 从而构成跨
操作系统的恢复链 (见 `missions/`, 五个 MissionPlan v0.7: M0 相机巡检门禁 + 四步恢复链)。

### 第 0 帧就是双向遮挡: 目标物体在"障碍—狗"夹角的**外面**

这是本场景要的几何关系, 而不是巧合: 以基座 `(-1.20,-0.40)` 为顶点看方位角,

```
                        ● obs2  方位 +45°, r=0.90   (可推障碍, 封住短弧)
      长弧 ◄────────────┼────────────► 短弧
                        │  ▲ yaw=0 (臂的初始朝向)
       狗 ● 方位 -45°, r=0.92          ● crate1 方位 +175.9°, r=0.70 (正后方)
                        │             (在 obs2 与狗的夹角**外**)
```

* **两个阻挡体第 0 帧就已经站在回转环带里** (收臂扫掠半径 0.63 m): MJCF 的
  `dog pos = arm_block_far_spot` (-45°, r=0.92) 与 `obs2 pos` (+45°, r=0.90)
  各封一侧, 因此画面一开始就是真实遮挡, 不需要任何"先制造障碍"的前置动作;
* 机械臂初始朝向 (方位 0°) 落在 `obs2` 与狗的**夹角内**, 而目标 `crate1` 在
  夹角**外** (正后方 +176°) —— 伸向目标时顺弧必穿 `obs2`、逆弧必穿狗;
* 于是两条弧各被一个实体封住 —— 本地判定为
  `local-workspace-blocked-both-directions`, 顺逆都拿不到;
* 机械臂不是"一算就知道转不动": 它会先**左右各摆一次**去探两侧 (摆动只走到
  阻挡体在扇区里的起始方位之前, 因此是试探而非撞击), 两侧都撞上东西才判失败;
* 消除动作必须**分别**由两个 EAIOS 执行: OM1 让狗自己走开 (让出长弧),
  Robonix 再把 `obs2` 顶出扇区 (让出短弧)。

都撤走后机械臂才做一次完整搬运: **转 180° 到基座正后方抓起 `crate1`, 再转回
起始方位把它放下** (`complete_after=place`, 夹爪在目的地真的松开并落地才写
`local-manipulation-placed`; 默认的 `complete_after=grasp` 是"拿起即成功")。

狗被刻意做成**大狗**: 机身 1.00 × 0.44 m、背高 0.77 m、头顶 0.97 m
(`dog_geom` 就是这个占位足迹)。机械臂肩关节在 0.75 m、收臂回转时小臂从
1.31 m 斜下到 0.62 m —— 与犬躯干 (0.47~0.77 m)**在同一高度带**, 因此"狗挡住
机械臂"在画面上成立, 不只是判定里的数字。目标物 `crate1` 也从 0.16 m 放大到
**0.26 m 见方**, 抓取点相应抬到**顶面之上** (`ARM_GRIP_LIFT_M`), 避免掌心埋进
箱子 (穿模)。

工作空间语义点 (都必须在臂展内, 固定基座臂搬不出去):

| 语义点 | 坐标 | 相对基座 (r, 方位) | 用途 |
|---|---|---|---|
| `arm_pick_spot` | (-1.90, -0.35) | 0.70, +175.9° | `crate1` 初始位置 (**基座正后方**) |
| `arm_home_drop` | (-0.65, -0.40) | 0.55, 0° | 放下点 = 臂的**起始方位** |
| `arm_block_far_spot` | (-0.55, -1.05) | 0.92, -45° | 狗的初始位 (扇区内, 封长弧) |
| `arm_dog_out_spot` | (0.30, -1.90) | 2.12, -45° | 狗的撤离位 (环带外, 同射线) |
| `arm_clear_spot` | (0.356, 1.156) | 2.20, +45° | rover 撤离位 |
| `arm_push_out_stage` | (-1.90, 1.20) | 1.75, +114° | 顶 `obs2` **出**扇区的西北外侧站位 |
| `arm_push_out_clear` | (-0.19, 0.10) | 1.13, +62° | 顶出行程的**终点** (在障碍初始位之外 1.5 m) |

`obs2` 的初始位置 (`-0.564, 0.236` = 方位 +45°, r=0.90) 直接写在 MJCF 里,
没有对应语义点 —— 它第 0 帧就在扇区内, 由 rover 顶**出去**。

### 可移动障碍 `obs2` 与接触位掩码

`obs2` 是 5 个凸障碍里唯一带 `freejoint` 的 (质量 4 kg), 可被 rover 前铲真实推动,
因此"障碍"不再只能由机器人本体扮演。MuJoCo 的接触位掩码是**或**规则
(`(c1&a2)|(c2&a1)`), 所以按位分配:

| 实体 | contype / conaffinity | 与 rover 前铲 (2/2) 接触 |
|---|---|---|
| 地面 / 任务物体 `crate1` | 1 / 1 | 否 (不会被顺手推走) |
| 墙体 / 固定障碍 | 1 / 3 | 否 |
| `obs2` | 3 / 3 | **是** (唯一可推物) |

另外: 带 freejoint 的动态体不进 A* 静态栅格 (否则会绕行, "推障碍"永远不发生)。

## 运行

### 1. 启动 Local EAIOS (三个 facade 共享一个 MuJoCo 世界)

```bash
cd integrations/hcore-mujoco-local-eaios
MUJOCO_GL=egl /home/sunweihao/miniconda3/envs/py310/bin/python \
  -m hcore_mujoco_local_eaios --speed 8
```

自检 (任一端口):

```bash
curl -s http://127.0.0.1:28111/v1/health
curl -s http://127.0.0.1:28111/v1/capabilities/mobility.navigate
curl -s -X POST http://127.0.0.1:28111/v1/executions -d \
  '{"invocation":{"operation":"mobility.navigate@v1","parameters":{"destination":"zone_north"}}}'
```

### 2. 离线校验节点配置 (不需要 Controller / 真机)

本机自带的 `cargo 1.65` 不支持 edition 2024, 已装 rustup stable (rustc 1.98.1)
到 `/var/tmp`(`/home` 只剩 ~8G, 工具链与产物不能落在那里)。每个新 shell 先:

```bash
export RUSTUP_HOME=/var/tmp/rg-rustup CARGO_HOME=/var/tmp/rg-cargo \
       CARGO_TARGET_DIR=/var/tmp/rg-target
. $CARGO_HOME/env
```

然后:

```bash
cargo run -p roboguide-node -- --validate \
  scenarios/hcore-heterogeneous-v0.1/node-quadruped-om1.toml
cargo run -p roboguide-node -- --validate \
  scenarios/hcore-heterogeneous-v0.1/node-rover-robonix.toml
cargo run -p roboguide-node -- --validate \
  scenarios/hcore-heterogeneous-v0.1/node-ptz-ros2.toml
cargo run -p roboguide-node -- --validate \
  scenarios/hcore-heterogeneous-v0.1/node-arm-roboos.toml
```

三份均产出 `roboguide.extension-conformance/v0.2`, `offline_compile=true`、
`controller_contacted=false`、检查项全 `True`:

```
unique_capability_owner / exact_readiness / fixed_routes / request_mappings
execution_state_mapping / required_resources / selective_state_memory
```

产出 `roboguide.extension-conformance/v0.2` 报告: 证明 profile 标量确定、
每个 operation 有完整固定路由 workflow、且没有网络输入能选择 endpoint 或方法。

### 3. 起全链路 (一条命令)

```bash
bash scenarios/hcore-heterogeneous-v0.1/run-step1-bringup.sh   # Ctrl-C 结束, trap 清理全部子进程
```

### 4. 跑 MissionPlan v0.7: 跨 OS 制造/消除障碍 + 恢复链 (一条命令)

```bash
bash scenarios/hcore-heterogeneous-v0.1/run-step2-mission.sh

# 顺带离屏录像 (可下载回看, 不需要 X server)
RG_RECORD=/var/tmp/rg-run/hcore-het-demo.mp4 \
    bash scenarios/hcore-heterogeneous-v0.1/run-step2-mission.sh
```

录像实测: 642 帧 / **42.8 s** / 960x544 @15fps (本轮录像: `artifacts/step2-v9.mp4`),
四个 Mission 结果与不录像时完全一致 (渲染只影响墙钟, 不影响仿真推进)。
可用 `RG_RECORD_FPS` 调帧率。

**录像会主动剪掉空镜头**, 因此视频时长**短于**仿真时长:

| 开关 | 剪掉什么 |
|---|---|
| `--record-defer` | 起服务 → 节点注册 → 心跳/租约就绪这一大段完全静止的片头 |
| `--record-skip-idle` | 控制面在 Mission / Task 之间重走 Match → Propose → Commit 的几秒静止 |

判据是"本机是否还有 Execution 在跑" (`RoleRuntime.busy()`), 不是像素差 —— 机械臂
探测两侧时有意的停顿属于执行中状态, 不会被误剪。详见
`integrations/hcore-mujoco-local-eaios/README.md`。

依次提交 `missions/` 下的五个 Mission, 并打印本地终态依据:

| Mission | 内容 | 期望 |
|---|---|---|
| `mission-ptz-survey-reach` | PTZ 云台环绕场景一周: 四个方位各拍一张照片, 判定 `crate1` 是否在机械臂抓取范围内 (**门禁**, FAILED 则后续不提交) | COMPLETED (`local-reach-in-workspace`) |
| `mission-arm-sweep-blocked-both` | 第 0 帧狗占长弧、`obs2` 占短弧; 机械臂左右各摆一次 | FAILED (两侧皆挡) |
| `mission-dog-clears-sector` | OM1 狗自行走开, 让出长弧 | COMPLETED |
| `mission-rover-removes-obstacle` | Robonix rover **一次冲撞**把 `obs2` 顶出扇区, 让出短弧 | COMPLETED |
| `mission-arm-pick-and-place` | 机械臂转 180° 到正后方取物, 再转回起始方位放下 | COMPLETED (placed) |

### 谁去推障碍: capability constraint, 而不是硬钉节点

狗与车都声明 `mobility.navigate@v1`, 但只有 rover 的前铲与 `obs2` 的接触位掩码重叠
(`contype/conaffinity` 3/3), 狗走过去顶不动它。这个差异是**能力**差异, 因此用
MissionPlan 的 capability constraint 表达, 由 Control 在匹配时自行排除不具备者:

| 节点 | `mobility.navigate@v1` 的 `push-capable` |
|---|---|
| `hcore-rover` (有前铲) | `true` |
| `hcore-quadruped` (无接触) | `false` |

| Mission | 地面角色的约束 | 匹配结果 |
|---|---|---|
| M3 (把障碍顶出扇区) | `push-capable equals true` | 只有 `hcore-rover` |
| M2 (撤离扇区) | `push-capable equals false` | 只有 `hcore-quadruped` |

判定在 `core/domain/src/node_state.rs` 的 `capability_requirement_is_available`:
约束用 `is_some_and` 求值, **未声明该属性的节点会被排除**, 所以狗必须显式声明
`false` 而不是省略。属性名是场景内约定 (不改 Core 合同), 由节点与 Mission 双方遵守。

早期版本改用 `actor-placement.json` 把 actor 硬钉到节点 —— 那是用**部署策略**补
**语义缺口**: 计划里没说"需要能推的执行者", 只好在部署时指定。现已删除该文件,
`run-step2-mission.sh` 不再传它。保留 placement 的唯一正当理由是表达**测试意图**
而非能力差异 (例如"我想让另一个 OS 来堵长弧"), 本场景改用 `push-capable=false`
已能表达, 故不需要。

注意 `strict coverage`: placement 一旦非空就要求每个 Mission 的每个 actor 都被声明,
不能只对部分 Mission 生效 —— 这也是当初不能"只去掉 M1/M4 的钉死"的原因。

等价的手工步骤:

```bash
cargo run -p integration-server -- 127.0.0.1:50051 ./var/controller.sqlite3 \
  127.0.0.1:8080 127.0.0.1:8090 ./var/artifacts

# 三个终端, 各起一个 (每台节点机器只运行一个 roboguide-node)
cargo run -p roboguide-node -- scenarios/hcore-heterogeneous-v0.1/node-quadruped-om1.toml
cargo run -p roboguide-node -- scenarios/hcore-heterogeneous-v0.1/node-rover-robonix.toml
cargo run -p roboguide-node -- scenarios/hcore-heterogeneous-v0.1/node-ptz-ros2.toml
```

## Step 1 实测结果 (已跑通)

四个 facade (节点侧 OS 身份, RoboGuide 不消费):

```
28111 -> {"state":"ONLINE","detail":"openmind-om1/quadruped sim_t=..."}
28112 -> {"state":"ONLINE","detail":"robonix-os/rover sim_t=..."}
28113 -> {"state":"ONLINE","detail":"ros2-nav2-moveit2/ptz sim_t=..."}
28114 -> {"state":"ONLINE","detail":"roboos/arm sim_t=..."}
```

RoboGuide 控制面 `GET /v1/inventory` (`roboguide.inventory/v0.1`, 4 个独立进程):

| node_id | contracts | liveness | health |
|---|---|---|---|
| `hcore-arm` | `object.relocate@v1` | Reachable | Online |
| `hcore-ptz` | `observation.verify@v1` | Reachable | Online |
| `hcore-quadruped` | `mobility.navigate@v1` | Reachable | Online |
| `hcore-rover` | `mobility.navigate@v1`, `observation.verify@v1` | Reachable | Online |

资源: `hcore-arm-workspace` / `hcore-ptz-fov` / `hcore-quadruped-space` / `hcore-rover-space`。

机械臂本地语义自测 (bridge 侧, 与 RoboGuide 无关):

| 用例 | 结果 | 本地依据 |
|---|---|---|
| `crate1` 在臂展内, 扇区通畅 | `COMPLETED` | `local-manipulation-placed` |
| rover 停在回转扇区内 (0.59 m, +22.5°) | `FAILED` | `local-workspace-blocked` |
| rover 移出扇区后重试同一任务 | `COMPLETED` | `local-manipulation-placed` |
| 目的地 `zone_north` 超出臂展 | `FAILED` | `local-reach-out-of-workspace` |
| 抓 `teddy` (世界内无自由度) | `FAILED` | `local-object-not-movable` |
| 让 rover 接搬运 | `FAILED` | `local-capability-rejected` |

**inventory-v0.1 契约 `additionalProperties: false` 且没有 runtime 字段** —— 控制面
只按 canonical capability 匹配, 拿不到也不消费"这是哪种 OS"。这不是缺证据,
而正是 ADR-0006 领域无关性的运行时证明。OS 身份只在节点侧成立, 因此"三个不同
runtime_name 同时在册"由两侧证据共同确认: 上面三行 facade health 给出 OS 身份,
inventory 给出同册事实。

代码级补充证据: `core/` 与 `apps/` 的**生产**代码中 `om1|openmind|robonix|moveit|
mujoco|nav2` 命中数为 **0**(仅测试夹具把这些串当普通字符串用过)。

## Step 2 实测结果 (MissionPlan v0.7, 已跑通)

控制面看到的是 Mission / Task 状态 (它只知道 canonical capability):

| Mission | status | tasks |
|---|---|---|
| `mission-arm-sweep-blocked-both` | `Failed` | relocate **Failed** (两侧皆挡) |
| `mission-dog-clears-sector` | `Completed` | 1/1 Completed (狗走出扇区) |
| `mission-rover-removes-obstacle` | `Completed` | 3/3 Completed (站位 → 一次冲撞顶出环带 → 撤离) |
| `mission-arm-pick-and-place` | `Completed` | 1/1 Completed (转 180° 取物 + 转回放下的完整搬运) |

**节点分工 (constraint 生效的证据)**: 各节点 journal 里的 execution 条数 —— 不传
`actor-placement.json` 时, `push-capable` 约束独自决定了派给谁:

| 节点 | execution 数 | 承担的任务 |
|---|---|---|
| `hcore-rover` | 3 | M3 顶出扇区: 站位 → 一次冲撞 → 撤离 (全部需要推) |
| `hcore-quadruped` | 1 | M2 撤离扇区 (只需走过去) |
| `hcore-arm` | 2 | M1 取物 (失败) + M4 完整搬运 |

本地侧 (节点 journal + facade) 给出的终态依据 —— 这是控制面**拿不到也推导不出**的:

```
[rover]     COMPLETED basis=local-odometry-arrival                  ×3
[quadruped] COMPLETED basis=local-odometry-arrival                  ×1
[arm]       FAILED    basis=local-workspace-blocked-both-directions
                      rotation blocked in both directions (short by body:obs2; long by body:dog)
                      — probed both arcs
            COMPLETED basis=local-manipulation-placed
                      placed crate1 at destination (yaw: short+short)
```

三点可以直接读出:

1. **M1 的失败是"摆出来的"**: 明细里的 `probed both arcs` 说明机械臂先左右各
   摆了一次试探, 两侧分别撞上 `obs2` (短弧) 与 `dog` (长弧) 才判失败 —— 不是
   一次性静态判定;
2. **失败明细同时点名 `obs2` (Robonix 的障碍) 与 `dog` (OM1)** —— 消除动作必须
   分别由两个 EAIOS 各自执行: M2 让 OM1 的狗自己走开, M3 让 Robonix 把障碍顶出;
3. **`yaw: short+short`** —— M4 同一次搬运里先走短弧转 180° 对准正后方的
   抓取点、再走短弧转回起始方位放下, 终态依据是夹爪真的松开并落地
   (`local-manipulation-placed`), 而不是"拿起即成功" (`local-manipulation-grasped`)。

控制面在 M1 只看到 `Failed`, 恢复不是它"修好臂", 而是**重新编排**: M2/M3 由
另两个 OS 清场, M4 再让机械臂完成搬运 —— 这正是"多机调度"区别于"单机规划"的
那一层。

## 语义目的地与期望值

`mobility.navigate@v1` 的 `destination` 支持:

- `zone_south` / `zone_mid` / `zone_north` (三条互不重叠的带状区域)
- 任意目标 body 名: `marker77` `marker69` `marker90` `marker6` `marker50` `teddy`

`observation.verify@v1` 的 `expected` 支持:

- `qr6.detected` → QR 模态, **仅 PTZ 具备**
- `marker<N>.exists` → ArUco 模态, 四足狗与履带车具备
- `teddy.exists` → 物体模态, **仅履带车具备**

能力不匹配时 facade 在本地直接判 `FAILED`
(`local-capability-rejected`), 这正好可用于验证 L3 恢复:
RoboGuide 看到失败后重新 Match → Propose → Commit → Rebind。

## 证据规则 (不得违反)

- 终态只能由本地观察写入: `local-odometry-arrival` / `local-sensor-observed` /
  `local-timeout` / `local-planner-no-path` / `local-capability-rejected`。
- Mission 完成不能伪造本地 `COMPLETED`; 本地 `COMPLETED` 也不声称 Mission 成功。
- cancel 只持久化请求, 真正 `CANCELLED` 由物理循环观察到停机后写入。

## 已知限制

- **本场景的"恢复链"是编排层重提交, 不是计划内分支**: MissionPlan v0.7 的
  `relation.kind` 只有 7 种 (`requires-active` / `group-member-state` /
  `shared-spatial-reference` / `relative-pose` / `relative-distance` /
  `state-requirement` / `freshness-requirement`), 全是**并存与几何/时效约束**,
  没有 on-failure / fallback 语义 —— v0.8 的枚举与之**完全相同**, 同样表达不了。
  因此 M1 失败后的消除动作是**另两个 Mission** (M2/M3), 由人或上层策略提交:
  "跨 OS 恢复"的因果关系目前由提交顺序承载, 不由契约承载。要用一个 Mission 表达
  "失败即换 OS", 需要新增 relation kind (属于合同演进, 本次不做)。
- 节点把整个 HTTP 响应体按步骤 id 记进 workflow context 的 `/steps/<step_id>`,
  因此 facade 的响应**必须是扁平 JSON**。早期版本多包了一层 `{"steps": {...}}`,
  表现为 `mapping source /steps/dispatch/execution_id does not exist`。
- 节点 `state_directory` 相对 **config 文件所在目录**解析, 且目录必须先存在
  (不存在时节点直接 `Load` 失败退出); 上一轮残留的 journal 会让新会话在
  reconnect 时看到未知 execution 并拒绝路由
  (`reconciliation is required before routing`)。`run-step2-mission.sh` 已处理。
- MissionPlan 的 role 必须声明 `resources` (如 `{"kind":"space","units":1}`),
  否则节点侧 `required_resources` 无法提交, 表现为
  `required resource is not committed` 并进入无限 recovery。
- canonical catalog v0.3 没有 `aerial.*` / `ptz.watch.*`。本场景复用
  `mobility.navigate@v1` 与 `observation.verify@v1`, 用参数区分语义, 因此
  **不修改 Core 合同**。若需要精确区分 modality, 应另起 ADR 扩 catalog。
- 原论文 UAV 已换成四足狗 (OM1 官方支持四足本体), 因此"飞越障碍"能力消失,
  异构性落在控制栈与能力集上。
- RoboGuide Scheduler 当前未实现 preemption; 行为抢占属于 Local EAIOS 的 L0,
  不作为 RoboGuide 侧验收指标。
