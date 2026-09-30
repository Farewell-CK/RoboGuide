# 实验报告：H-CoRE 多具身操作系统协同调度 + 本体自带三维建图定位 (`hcore-slam-v0.3`)

- 场景版本：`hcore-slam-v0.3`（脱胎于 `hcore-homogeneous-v0.2`：四个槽位全部 `robonix-os`）
- 报告日期：2026-09-28
- 状态：Step 1（全链路贯通）与 Step 2（五个 MissionPlan v0.7）均已跑通
- 本轮新增：**地面平台改用自己装的深度相机 + 雷达建三维地图并自己定位**，
  导航闭环里不再出现世界真值

---

## 1. 实验要验证什么

一句话：**让一个完全不知道底层机器人是什么的调度器，去协调四种控制栈互不相同的机器人，在同一个物理世界里完成一件需要它们互相配合才能做成的事。**

被验证的核心命题（ADR-0006 领域无关性）：RoboGuide 控制面全程只能看到 canonical 能力契约
（如 `mobility.navigate@v1`）与本地上报的证据，**看不到**底下跑的是 OpenMind OM1 还是
ROS 2，也看不到物理后端是 MuJoCo。

为此构造了一件"必须跨操作系统配合"的事：

> 机械臂要把 `crate1` 从基座正后方 (`arm_pick_spot`) 搬到起始方位的放下点
> (`arm_home_drop`)。但它的回转扇区**第 0 帧**就被两个实体各封一侧 —— 可推障碍
> `obs2` 占短弧、四足狗占长弧。于是"臂转不动"这件事的原因分布在两个不同的 EAIOS 上，
> 消除动作也只能各由它们执行：狗自己走开、rover 把障碍顶出去，之后臂才转得动。

---

## 2. 系统拓扑

```
脚本/人 ──HTTP──► Controller 8080          接 Mission、管生命周期
                  Integration Server        同进程：Control 权威 + Mission 编排
                        │ gRPC 50051        Node Protocol v0.4（节点反向注册）
        ┌───────────────┼───────────────┬───────────────┐
   roboguide-node  roboguide-node  roboguide-node  roboguide-node     ← 4 个独立进程
        │ HTTP           │               │              │
    28111 OM1      28112 Robonix   28113 ROS2      28114 RoboOS      ← Local EAIOS（被仿真的 OS）
        └───────────────┴───────────────┴──────────────┘
                同一个 MuJoCo 世界 arena_roboguide.xml
```

涉及的合同版本：

| 合同 | 版本 |
|---|---|
| MissionPlan | `roboguide.mission-plan/v0.7` |
| Node 配置 | `roboguide.node-config/v0.7` |
| Node Protocol | v0.4 (gRPC) |
| Inventory（控制面可见的节点清单） | `roboguide.inventory/v0.1` |
| 扩展一致性报告 | `roboguide.extension-conformance/v0.2` |

---

## 3. 智能体清单

共 **4 个物理本体 / 4 个节点进程**。其中 **3 个参与了 Step 2 的任务**，PTZ 云台仅在册未派活
（见 §6 边界）。

| 节点 | 本体 | 仿真的 Local EAIOS | canonical 能力契约 | 感知模态 | 运动 |
|---|---|---|---|---|---|
| `hcore-quadruped` | 四足狗 | **OpenMind OM1** | `mobility.navigate@v1` | ArUco（2.5 m） | 0.55 m/s，转向 1.6 rad/s |
| `hcore-rover` | 履带车 | **Robonix OS** | `mobility.navigate@v1` + `observation.verify@v1` | ArUco（2.5 m）+ 物体（1.6 m） | 0.35 m/s，转向 1.0 rad/s |
| `hcore-ptz` | PTZ 云台 | **ROS 2 + Nav2 / MoveIt 2** | `observation.verify@v1` | QR（8.0 m） | 固定（仅 pan/tilt） |
| `hcore-arm` | 固定基座回转臂 | **RoboOS** | `object.relocate@v1` | — | 360° 连续回转 |

各节点占用的独占资源：`hcore-quadruped-space` / `hcore-rover-space` / `hcore-ptz-fov` /
`hcore-arm-workspace`（均为 `space` 类型，容量 1）。

### 3.1 机械臂（本实验的主角）

固定基座二连杆 + 360° 回转立柱，`arm_yaw` **无 joint 限位**，因此可真正连续回转。

| 参数 | 值 | 含义 |
|---|---|---|
| 基座 | `(-1.20, -0.40)`，肩高 0.75 m | — |
| 大臂 / 小臂 | 0.75 m / 0.70 m | 臂展 1.45 m |
| 工作空间半径 | 1.30 m | **可达性**判定（抓取点够不够得着） |
| 收臂扫掠半径 | ≈ 0.63 m | **回转**时真正扫过的圆 |
| 内侧占位 | 0.22 m | 基座自身，不计入阻挡 |
| 收臂姿态 | 肩 -0.85，肘 2.30 rad | 回转过程中保持 |

两个半径的区别是本实验一个关键物理点：回转时臂是**收着的**、对准后才伸展，所以判定"转的
时候会不会撞到"只能用 0.63 m。若误用臂展 1.30 m，距基座 0.94 m 的西墙会把所有长弧全部否决，
双向回转形同虚设。

### 3.2 物理世界

- 竞技场：`arena_roboguide.xml`（源自 H-CoRE MuJoCo 复现场景）
- 4 个固定凸障碍 `obs1/3/4/5`（静态 geom）+ 1 个**可推动障碍 `obs2`**（`freejoint`，4 kg）
- 可搬运物 `crate1`；无自由度物体 `teddy`；ArUco 标记 `marker6/50/69/77/90`；QR `qr6`

`obs2` 是唯一能被推动的实体，靠接触位掩码隔离（MuJoCo 的掩码是**或**规则
`(c1&a2)|(c2&a1)`，故按位分配）：

| 实体 | contype / conaffinity | 与 rover 前铲（2/2）接触 |
|---|---|---|
| 地面 / `crate1` | 1 / 1 | 否（不会被顺手推走） |
| 墙体 / 固定障碍 | 1 / 3 | 否 |
| `obs2` | 3 / 3 | **是**（唯一可推物） |

带 `freejoint` 的动态体不进 A* 静态栅格 —— 否则 rover 会绕开它，"推障碍"永远不会发生。

语义点：

语义点全部以基座 `(-1.20,-0.40)` 为极点给出 `r / 方位`，由 `geom_audit.py` 从 MuJoCo
模型**实测**复核（不再是手写常量）：

| 语义点 | 坐标 | r | 方位 | 用途 |
|---|---|---|---|---|
| `arm_pick_spot` | (-1.90, -0.35) | 0.70 | +175.9° | `crate1` 位置：**基座正后方**的目标物 |
| `arm_home_drop` | (-0.65, -0.40) | 0.55 | 0° | 放下点 = 臂的**起始方位** |
| `arm_block_far_spot` | (-0.55, -1.05) | 0.92 | -45° | 狗的**初始位**（MJCF `dog pos`），只封长弧（逆弧） |
| `arm_dog_out_spot` | (0.30, -1.90) | 2.12 | -45° | 狗的扇区外撤离点（同射线） |
| `arm_clear_spot` | (0.356, 1.156) | 2.20 | +45° | rover 撤离点（朝**远离**障碍的一侧） |
| `arm_push_out_stage` | (-1.90, 1.20) | 1.75 | +114° | 顶障碍**出**环带前的西北外侧站位 |

`obs2` 初始位 `(-0.564, 0.236)` = 方位 **+45°、r=0.90**（写在 MJCF 里，无语义点）：
它第 0 帧就在回转环带内，由 rover 顶**出去**。

### 3.3 双向遮挡的几何定义（本轮重排）

二维俯视，以机械臂底座 `B=(-1.20,-0.40)` 为极点：

- 机械臂 `yaw=0` 时末端指向方位 **0°**，落在 `obs2(+45°, r=0.90)` 与
  `dog(-45°, r=0.92)` 的夹角 **[-45°, +45°] 之内**（臂顶点在角内）；
- 目标物 `crate1` 在方位 **+175.9°（r=0.70）**，在该夹角 **之外**（基座正后方）；
- 于是臂伸向目标时：短弧（0°→+176°）必穿 `obs2`，长弧（0°→-184°）必穿 `dog` ——
  双向遮挡成立，两个方向各由一个不同的 EAIOS 封住；
- **两个阻挡体第 0 帧就在环带里**（收臂扫掠半径 0.629 m，二者 r 均为 0.9 m），
  因此遮挡是**初始状态**而不是靠某个 Mission 制造出来的。

几何由两个脚本给出，可复算：`geom_audit.py`（世界坐标 + 2D 包围盒 + 方位覆盖区间 +
两两间隙）与 `smoke_geom.py`（四种状态下的回转判定、腿部抖动量化、栅格占用）。

---

## 4. 已完成的实验

### 4.1 Step 1 — 全链路贯通

四个 facade 分别以各自的 OS 身份上报在线：

```
28111 -> {"state":"ONLINE","detail":"openmind-om1/quadruped sim_t=..."}
28112 -> {"state":"ONLINE","detail":"robonix-os/rover sim_t=..."}
28113 -> {"state":"ONLINE","detail":"ros2-nav2-moveit2/ptz sim_t=..."}
28114 -> {"state":"ONLINE","detail":"roboos/arm sim_t=..."}
```

控制面 `GET /v1/inventory` 看到 4 个独立节点进程：

| node_id | contracts | liveness | health |
|---|---|---|---|
| `hcore-arm` | `object.relocate@v1` | Reachable | Online |
| `hcore-ptz` | `observation.verify@v1` | Reachable | Online |
| `hcore-quadruped` | `mobility.navigate@v1` | Reachable | Online |
| `hcore-rover` | `mobility.navigate@v1`, `observation.verify@v1` | Reachable | Online |

**领域无关性的运行时证据**：`roboguide.inventory/v0.1` 契约 `additionalProperties: false` 且
**没有 runtime 字段** —— 控制面拿不到也不消费"这是哪种 OS"。这不是缺证据，而正是要证明的
性质本身。OS 身份只在节点侧成立（上面四行 facade health），"四个不同 OS 同时在册"由两侧
证据共同确认。

代码级补充：`core/` 与 `apps/` 的**生产**代码中 `om1|openmind|robonix|moveit|mujoco|nav2`
命中数为 **0**（仅测试夹具把这些串当普通字符串用过）。

节点配置离线校验（`--validate`）四份全通过，产出 `roboguide.extension-conformance/v0.2`，
`offline_compile=true`、`controller_contacted=false`，检查项全 `True`：
`unique_capability_owner` / `exact_readiness` / `fixed_routes` / `request_mappings` /
`execution_state_mapping` / `required_resources` / `selective_state_memory`。

### 4.2 机械臂本地语义自测（bridge 侧，与 RoboGuide 无关）

| 用例 | 结果 | 本地依据 |
|---|---|---|
| `crate1` 在臂展内，扇区通畅 | `COMPLETED` | `local-manipulation-placed` |
| rover 停在回转扇区内（0.59 m，+22.5°） | `FAILED` | `local-workspace-blocked` |
| rover 移出扇区后重试同一任务 | `COMPLETED` | `local-manipulation-placed` |
| 目的地 `zone_north` 超出臂展 | `FAILED` | `local-reach-out-of-workspace` |
| 抓 `teddy`（世界内无自由度） | `FAILED` | `local-object-not-movable` |
| 让 rover 接搬运 | `FAILED` | `local-capability-rejected` |

### 4.3 Step 2 — 四个 MissionPlan v0.7

主线：机械臂搬 `crate1`；两个阻挡体（狗 / 可推障碍）**第 0 帧**就各封一侧，
恢复链由另外两个 EAIOS 分别清场，最后机械臂完成一次完整搬运。

| Mission | 编排内容 | 结果 |
|---|---|---|
| **M1** `blocked-both` | 初始状态即双向遮挡：狗占长弧、`obs2` 占短弧 → 臂左右各摆一次试探 | `Failed` 1/1（relocate Failed，双向皆挡） |
| **M2** `dog-clears-sector` | 狗自己走开到 `arm_dog_out_spot`，让出长弧 | `Completed` 1/1 |
| **M3** `rover-removes-obstacle` | rover 绕到西北外侧，**一次冲撞**把 `obs2` 顶出环带后自己撤离 | `Completed` 3/3 |
| **M4** `arm-pick-and-place` | 臂转 180° 到正后方抓起 `crate1`，再转回起始方位放下 | `Completed` 1/1（`placed`） |

本地终态依据（控制面拿不到也推导不出）：

```
[arm] FAILED    basis=local-workspace-blocked-both-directions
                rotation blocked in both directions (short by body:obs2; long by body:dog)
                — probed both arcs                                                          ← M1
      COMPLETED basis=local-manipulation-placed
                placed crate1 at destination (yaw: short+short)                             ← M4
[quadruped] COMPLETED basis=local-odometry-arrival                                          ← M2
[rover]     COMPLETED basis=local-odometry-arrival ×3                                       ← M3
```

四条可以直接读出：

1. **M1 的失败是"摆出来的"**：`probed both arcs` 说明机械臂先朝短弧侧、再朝长弧侧
   各摆了一次（摆动只走到阻挡体在扇区里的起始方位之前，因此是试探而非撞击），
   两侧分别撞上 `obs2` 与 `dog` 才判失败；
2. **失败明细同时点名 `obs2` 与 `dog`** —— 消除动作必须分别由两个 EAIOS 各自执行：
   M2 让 OM1 的狗自己走开，M3 让 Robonix 把障碍顶出；
3. M3 的"顶出"要让障碍**中心 r > 0.99 m**（扫掠半径 0.629 + 障碍外接圆 0.36），
   否则机械臂**持物**回转时末端扫掠半径变大，仍会被它挡住。一次冲撞就能做到，
   但**行程终点必须写在障碍初始位置之外**：推进量 = 行程 − 接触间隙 0.51 m，
   若终点写成障碍自身的位置，rover 顶到障碍原位就停、推进量被钳死在半个身位
   （实测只推动了 0.45 m，`obs2` 留在 r=1.02 处仍会挡住持物回转）。把终点改为
   `arm_push_out_clear`（障碍初始位之外约 1.5 m）后，单趟就把 `obs2` 从 r=0.90
   顶到 r=1.39，M3 由五步压缩为三步；
4. M4 的 **`yaw: short+short`** 是一次**完整搬运**：先走短弧转 180° 对准正后方的
   抓取点，再走短弧转回起始方位放下，终态依据是夹爪真的松开并落地
   （`local-manipulation-placed`）而不是"拿起即成功"（`local-manipulation-grasped`）。

#### 本轮为让四个终态成立所做的修正

| 修正 | 原因 |
|---|---|
| MJCF `dog pos` 与 `obs2 pos` 直接给成扇区内坐标（`-0.55,-1.05` / `-0.564,0.236`） | 上一版让 rover 先把障碍顶进扇区，初始画面里两者离臂太远，看不出遮挡；现在第 0 帧就是双向遮挡 |
| 两侧皆挡时先"左右各摆一次"再判失败（`_probe_goals`） | 静态判定在录像里看不到任何动作；摆动目标角取在阻挡体的扇区起始方位之前，因此不会真的撞上去（本场景是几何 sweep 判定，撞上去只会穿模） |
| 新增 `complete_after=place`，M4 由"拿起即成功"改为"搬到目的地放下才算完成" | 需求是"转 180° 取物 → 转回起始方位放下"的完整动作 |
| 新增语义点 `arm_home_drop`（方位 0°、r=0.55） | 放下点取在起始方位；r 取得比抓取点小，是为了压住"持物回转"的扫掠半径，避免被刚推出环带的 `obs2` 重新挡住 |
| `arm_push_out_stage` 由 `(-1.05, 0.85)` 改到 `(-1.90, 1.20)`，并新增行程终点 `arm_push_out_clear` | 原站位朝东南顶时顶推方向与径向近乎垂直，障碍只在环带里横移；站位取远、终点取在障碍初始位之外，一次冲撞的行程才够（见上面第 3 条） |
| 录像加 `--record-defer` 与 `--record-skip-idle` | 原来从 bridge 启动就开始录，片头有几十秒完全静止的画面（等注册/心跳/租约），Mission 之间还各有一段约 6–8 s 的编排静止；现在只对"本机有 Execution 在跑"的时段写帧 |

视频长度因此从 137.7 s 压到 **42.8 s**，与四个 Mission 的纯动作时长基本相等。

机械臂下坠扎地已在此前修掉：初始姿态显式置为收臂（`ARM_STOW`），保持臂型时**只锁定一次**关节角
再下发位置指令（每步改写指令会让弹簧力恒为 0，末端以阻尼端速度持续下扎，实测空闲 2 s 从 z=0 掉到
-0.32 m）。本次复测：空闲 5 s 末端稳定在 z=+0.613 m。

### 4.4 谁去推障碍：capability constraint 取代硬钉节点

狗与车都声明 `mobility.navigate@v1`，但只有 rover 前铲与 `obs2` 的掩码重叠，狗顶不动它。
这个**能力差异**用 MissionPlan 的 capability constraint 表达，由 Control 匹配时自行排除：

| 节点 | `mobility.navigate@v1` 的 `push-capable` |
|---|---|
| `hcore-rover`（有前铲） | `true` |
| `hcore-quadruped`（无接触） | `false` |

| Mission | 地面角色约束 | 匹配结果 |
|---|---|---|
| M3（把障碍顶出扇区） | `push-capable equals true` | 只有 `hcore-rover` |
| M2（撤离扇区） | `push-capable equals false` | 只有 `hcore-quadruped` |

判定在 `core/domain/src/node_state.rs::capability_requirement_is_available`，约束用
`is_some_and` 求值 —— **未声明该属性的节点会被排除**，所以狗必须显式声明 `false` 而非省略。

**实测分工证据**（不传任何 placement 文件，纯由约束决定）：

| 节点 | execution 数 | 承担的任务 |
|---|---|---|
| `hcore-rover` | 5 | M3 站位 → 冲撞 → 退回 → 再冲撞 → 撤离（全部需要推） |
| `hcore-quadruped` | 1 | M2 撤离扇区（只需走过去） |
| `hcore-arm` | 2 | M1 取物（失败）+ M4 完整搬运 |

早期版本用 `actor-placement.json` 把 actor 硬钉到节点 —— 那是用**部署策略**补**语义缺口**。
现已删除，约束接管。（附带约束：placement 的 strict coverage 要求一旦非空就覆盖每个
Mission 的每个 actor，不能只对部分 Mission 生效。）

---

## 5. 体现了什么能力

### 5.1 已验证的能力

**① 领域无关的多 EAIOS 调度（核心命题）**
四种互不相识的控制栈（OM1 / Robonix / ROS 2+Nav2+MoveIt2 / RoboOS）在同一控制面下被
匹配、派发、回收资源，控制面全程不知道它们是谁。这是本实验存在的理由。

**② 能力驱动的匹配，且能用约束细化**
不只按契约名匹配，还能按**能力属性**排除。M1/M3 需要"能推的执行者"，调度器从两个
`mobility.navigate@v1` 节点中自行选出 rover —— 指派从部署硬编码提升为语义声明。

**③ 跨 OS 的空间冲突真实可观测、可制造、可消除**
障碍不是静态布景：`obs2` 由 Robonix 推入，`dog` 由 OM1 停入。失败的因果分布在两个
EAIOS 上，消除动作也各归其主。这是"多机调度"区别于"单机规划"的那一层。

**④ 本地终态权威**
终态只能由本地观察写入（`local-odometry-arrival` / `local-manipulation-placed` /
`local-workspace-blocked-both-directions` …）。Mission 完成不能伪造本地 `COMPLETED`，
本地 `COMPLETED` 也不声称 Mission 成功。控制面在 M1 只看到 `Failed`，是谁挡的它不知道。

**⑤ 资源独占与依赖推进**
每个 role 声明 `space:1`，Commit 后独占；Task 按 `depends_on` DAG 推进。

**⑥ 崩溃可恢复**
事件日志 + checkpoint 同一事务提交；节点 reconnect 时校验 session 当前性。

**⑦ 本地 EAIOS 的运行时自适应**（在 RoboGuide 视野之外，但真实存在）
臂的"短弧被挡 → 改走反向长弧"、A* 路径规划、接触推动 —— 都是每个物理步重算的运行时
决策。机械臂搬运中的两条物理细节也在此期间被修正：

- 扫掠半径取收臂姿态的 0.63 m 而非臂展 1.30 m（否则西墙误挡所有长弧）；
- 扇区相交判定改为沿实体**外缘采样**（约 0.10 m 步长），而非"中心距 + 外接圆半径"。
  后者会同时犯两种错：细长墙体被当成巨盘而过度阻挡；箱子边角已伸进扫掠圆却因中心在
  圈外被漏判，导致臂持物撞上去（运动学搬运会把箱子弹飞）。

**⑧ 本体自带的三维建图与定位（本轮新增，详第 6 节）**
地面平台不看世界真值：自己打射线、自己建 6 cm 体素的三维地图、自己在图里定位，
导航闭环与"看见了没有"都只用估计量。本轮实测定位误差 mean 4.7~4.8 cm、max 7.1 cm，
三次导航的路径全部规划在自己建的地图上。

**⑧ 几何统一到世界坐标 + 真实碰撞箱（本轮重排）**

此前坐标是逐个手调的散落常量，"谁挡住哪一段方位"只能靠注释推断。现在统一为：

- `geom_audit.py` 直接从 MuJoCo 模型读出每个实体的世界坐标、2D 包围盒、相对臂基座的
  `r / 方位 / 方位覆盖区间`，以及两两之间的包围盒间隙（正=分离，负=重叠）。3.3 节的
  整张表都是它的输出，不是手写值。
- 三个"穿模/抖动"因此被定位并修掉：
  - **小车穿过白色标志柱**：ArUco 立柱原先既不进 A\* 栅格（`group 3` 被栅格化跳过）
    也不参与接触（继承默认 `contype=0`），于是规划与物理都当它不存在。现给立柱补上
    `contype/conaffinity`，并把 `group 3` 的静态体纳入 `OccupancyGrid` —— 规划绕开、
    物理顶住，两处都要有，只修一处仍会穿。
  - **狗开场腿部抖动**：腿部 geom 继承 `density=400`（每段约 0.8 kg）挂在阻尼 0.05 的
    铰链上，落地后被重力拉成持续摆动。改为显式轻质量（0.02 kg）+ 阻尼 0.30，并在
    世界初始化时直接给站立姿态（此前 hinge 默认 0=伸直，靠执行器拉回，落地必抖一下）。
    实测 2 s 内腿部关节角速度峰值 0.039 rad/s（静止）。
  - **狗移动穿过障碍**：此前"推障碍"排在"狗移动"之前，且两者在同一侧。现在 M1 的顺序
    是 rover 就位 → 狗沿 -45° 射线走进扇区 → rover 沿 +45° 把障碍顶进扇区，两条射线
    相差 90°，路径互不穿越；狗的进出都走同一条射线，因此不会横穿障碍。

**⑨ 成功条件改为"拿起即成功"**

搬运到放置点这一段会把**目标物自身**变成回转阻挡体，使终态判定被它自己污染。现在
`object.relocate` 的真实闭合 + 持物即写入 `COMPLETED`
（`local-manipulation-grasped`）；放下、收臂、转回初始朝向是之后的收尾动作，让下一个
Mission 仍能演示一次完整回转，而不是"已经对准 → 瞬间完成"。终态后物体仍被夹爪带着
（每个物理步跟随），不会凭空掉下去。

### 5.2 尚未体现的能力（明确边界）

诚实区分"系统做的"与"人预设的"：

| 事项 | 现状 |
|---|---|
| **自动恢复** | 没有。M1 失败后系统什么都没做，是脚本按顺序提交了 M2/M3。恢复链的因果**在脚本里，不在系统里** |
| **换能力的重调度** | 没有。L3 recovery（`schedule_recovery`）只能给同一 Task 同一 Role 换一台**具备同样能力**的节点，换不了能力 |
| **自动规划** | 未接入。四个 MissionPlan 是手写 JSON 直接 POST。真正的规划器（`mission/`，LLM 驱动的解释→规划→审查→修复）存在但本次绕过 |
| **MissionPlan 内的分支** | 契约不支持。v0.7 的 `relation.kind` 只有 7 种并存/几何/时效约束，**没有** on-failure / fallback；v0.8 枚举与之完全相同。故 M2/M3 本该是 M1 的分支，却只能拆成两个独立 Mission |
| **失败原因的机器可读性** | 不足。`terminal_basis` 只是一行给人看的字符串，系统无法据此反查"该派谁去清" —— 这是自动恢复的直接断点 |
| **PTZ / 观察能力** | 仅在册，四个 Mission 均未派给它任务（M1-M4 无 `observation.verify`） |

一句话总结当前定位：**本实验验证了"领域无关的调度机制"，没有验证"自动恢复"或"自动规划"。**
它证明了多机调度这个问题的真实存在，但"该派谁去清障碍"这一步目前没有系统在做。

---

## 6. 本体自带的三维建图与定位（本轮新增）

### 6.1 改了什么

前两版里地面平台的位置、路径、"看见了吗"全部来自仿真真值，闭环里流的是上帝视角的坐标。
本轮把这条捷径拆掉，两型地面平台各装一套传感器，数据链完全走自己的观测：

```
MJCF 场景 ──(mj_ray 真射线求交 + 噪声)──► 雷达 / 深度相机回波
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
        体素三维地图 (log-odds)      里程计 (未标定, 含漂移)      关键帧 ICP (相对运动)
                    │                         │                         │
                    │  扫描匹配 (扫描 vs 地图)│◄── 在线标定尺度 ────────┘
                    └────────► 位姿估计 ◄─────┘
                                      │
                        ┌─────────────┼─────────────┐
                        ▼             ▼             ▼
                 导航闭环反馈    A* 规划 (用自己地图)  "看见了吗" 判定
```

| 环节 | 实现 | 真值的使用 |
|---|---|---|
| 雷达 | 360°×3 层、8 m、2° 分辨率，σ=1 cm+1% 量程，2% 随机丢点 | 只用于产生回波 |
| 深度相机 | 640×480、70°、5 m，0.5% 深度噪声、1% 像素缺失 | 只用于产生回波 |
| 三维地图 | 6 cm 体素、log-odds 占据、命中前后沿途清空 | 用**估计位姿**融合（不用真值） |
| 里程计 | 关节速度积分，出厂尺度误差 6% σ | 注入误差，随后被标定掉 |
| 定位 | 关键帧 ICP（相对运动）+ 扫描↔地图匹配（绝对纠正） | 无 |
| 到点判定 | 估计位姿距离 < 0.28 m（真值判据是 0.15 m） | 无 |

### 6.2 过程中踩到并修掉的三个坑（都写进了代码注释）

1. **地图是"拿自己的估计位姿建的"，所以匹配看不见自己的误差。**
   位姿偏 δ，地图整体跟着偏 δ，于是"扫描 vs 地图"永远自洽 —— 实测误差已经 0.5 m，
   匹配分数还是 0.98。结论：**地图匹配不能当常规纠正源**，否则只是在自己的噪声里
   随机游走。现在它只在分数明显掉下来（< 0.90，说明被撞偏/打滑/出现新结构）时才介入。
2. **逐帧 ICP 反而放大误差。** 雷达 10 Hz 时两帧之间只走 3 cm，而 ICP 解算噪声本身就在
   1~2 cm —— 逐帧做等于把噪声按 √帧数 累积（实测把 0.05 m 放大到 0.12 m）。
   改成"相对关键帧走出 0.25 m 或转过 0.15 rad 才对齐一次"。同理，被顶走的障碍 obs2
   会被 ICP 误认成"自己在动"，所以对应阈值逐轮收紧（0.40 → 0.08 m）。
3. **在线标定不能用地图来标定自己。** 最早拿"地图匹配位移 / 里程计位移"当比例，
   把尺度从 1.12 拉到 0.84，误差翻倍。标定必须用**与地图无关的观测**（ICP 的相对运动），
   且要注意 ICP 累积的是*修正量*，观测到的真实位移是 `里程计位移 + 修正量`。

### 6.3 实测数据（seed=7，五次 Mission 全链路）

| 本体 | 定位误差 mean | max | 末态 | 雷达扫描 | 地图体素 | 覆盖率 | 规划来源 |
|---|---|---|---|---|---|---|---|
| 四足狗 | 0.048 m | 0.063 m | 0.034 m | 521 | 5266 | 27.3% | self-map |
| 地面车 | 0.047 m | 0.071 m | 0.025 m | 904 | 7986 | 32.4% | self-map ×3 |

- 三次导航（站位 → 顶出 → 撤离）的路径**全部**规划在本体自己的地图上，一次都没回退到仿真栅格。
- 导出的点云（PLY）范围 `x[-2.7, 2.8] y[-3.3, 3.1] z[0.11, 1.49]`，与 6×6 m 场地和
  ~1.5 m 墙高一致，说明建出来的图在几何上是对的。
- 里程计在线标定：出厂 1.008 → 收敛到 0.98~1.01（真值 1.000）。
- 算力：带 SLAM 后物理 + 感知的实时因子约 **2.8x**（不带时 ~5x），故脚本倍速从 8 降到 2。

### 6.4 诚实边界

| 事项 | 现状 |
|---|---|
| "看见目标了吗" | 判定用的是真实射线遮挡 + 量程 + 视场，但**目标是谁、在哪**仍由 ArUco/YOLO 那一层给出（这里直接取用），没有做真正的视觉识别 |
| 绝对定位 | 没有 GPS / 先验地图 / 已知地标。误差不随路程线性增长（ICP 与标定压住了），但初始误差不会归零 —— 这是纯 SLAM 的本质限制 |
| 回环检测 | 没有。本实验路径不回环，长期大范围运行仍会累积漂移 |
| 相机参与 | 深度相机在建图（近处补充点云）与"看见了吗"判定中起作用；点云着色/纹理没有用到 RGB |

---

## 7. 如何复现

```bash
cd RoboGuide
export RUSTUP_HOME=/var/tmp/rg-rustup CARGO_HOME=/var/tmp/rg-cargo CARGO_TARGET_DIR=/var/tmp/rg-target
. $CARGO_HOME/env

# Step 1：起全链路（Ctrl-C 结束，trap 清理全部子进程）
bash scenarios/hcore-slam-v0.3/run-step1-bringup.sh

# Step 2：四个 MissionPlan v0.7（约 2.5 分钟）
bash scenarios/hcore-slam-v0.3/run-step2-mission.sh
```

Step 2 依次做五件事：起共享 MuJoCo 世界 + 四个 facade → 起 Integration Server（**不传**
placement）→ 起四个节点并等到 4 个在册 → 依次提交四个 Mission 并轮询终态 → 从节点
journal + facade 读本地终态依据。

离线校验（不需要 Controller / 真机）：

```bash
cargo run -p roboguide-node -- --validate scenarios/hcore-slam-v0.3/node-quadruped-om1.toml
cargo run -p roboguide-node -- --validate scenarios/hcore-slam-v0.3/node-rover-robonix.toml
cargo run -p roboguide-node -- --validate scenarios/hcore-slam-v0.3/node-ptz-ros2.toml
cargo run -p roboguide-node -- --validate scenarios/hcore-slam-v0.3/node-arm-roboos.toml
```

本机自带 `cargo 1.65` 不支持 edition 2024，rustup stable 装在 `/var/tmp`（`/home` 空间不足，
工具链与产物不能落在那里）。

---

## 8. 其它已知限制

- 节点把整个 HTTP 响应体按步骤 id 记进 workflow context 的 `/steps/<step_id>`，因此 facade
  响应**必须是扁平 JSON**（多包一层会表现为 `mapping source /steps/dispatch/execution_id
  does not exist`）。
- 节点 `state_directory` 相对 **config 文件所在目录**解析，且目录必须先存在（否则节点直接
  `Load` 失败退出）；上一轮残留 journal 会让新会话在 reconnect 时拒绝路由
  （`reconciliation is required before routing`）。`run-step2-mission.sh` 已处理。
- MissionPlan 的 role 必须声明 `resources`，否则节点侧 `required_resources` 无法提交，
  表现为 `required resource is not committed` 并进入无限 recovery。
- canonical catalog v0.3 没有 `aerial.*` / `ptz.watch.*`。本场景复用 `mobility.navigate@v1`
  与 `observation.verify@v1`，用参数区分语义，因此**不修改 Core 合同**。若需精确区分
  modality，应另起 ADR 扩 catalog。
- 原论文 UAV 已换成四足狗（OM1 官方支持四足本体），故"飞越障碍"能力消失，异构性落在
  控制栈与能力集上。
- RoboGuide Scheduler 未实现 preemption；行为抢占属于 Local EAIOS 的 L0，不作为 RoboGuide
  侧验收指标。
