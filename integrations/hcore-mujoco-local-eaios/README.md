# H-CoRE MuJoCo Local EAIOS Bridge

把 H-CoRE 的 MuJoCo 竞技场 (`arena_roboguide.xml`) 接成 RoboGuide 的
Local EAIOS, 为三个不同具身操作系统提供统一的本地执行入口。

**这是 deployment-owned bridge, 不属于 RoboGuide Core。** 它不拥有 Mission、
Execution Group、State Catalog、Artifact publication 或 Node Protocol 生命周期;
节点机器上仍只运行一个 `roboguide-node`, 本 bridge 只是它本地配置声明的
Local EAIOS endpoint。

## 为什么不改 H-CoRE 本体

ADR-0006: RoboGuide 的目标是**协调多个相同或不同 EAIOS**, 而不是要求设备运行
RoboGuide-owned Agent。因此:

- 物理后端 (MuJoCo) 与本体定义不动;
- 每个"操作系统槽位"只需要在顶端有一个 loopback HTTP facade, 把 canonical
  `ExecutionIntent` 翻译成自己的本地调用;
- RoboGuide Core 里不出现任何 om1 / robonix / ros2 字段。

## 组成

```
hcore_mujoco_local_eaios/
├── world.py    # Local How: 物理步进、A* 导航、视场/遮挡感知、终态观察
├── facade.py   # canonical What -> Local How: 5 条固定 HTTP 路由
└── __main__.py # 一个进程托管一个 MuJoCo 世界 + 四个角色 facade
```

一个进程只持有**一个** MuJoCo 世界, 四个 facade 端口共享同一份物理状态,
因此两台地面平台抢占同一区域、或某台车停进机械臂回转扇区时的空间冲突都是
真实可观测的, 而不是各自独立的沙盒。

## 运行

需要 `mujoco` 与 `numpy`(本机: `/home/sunweihao/miniconda3/envs/py310`),
以及 H-CoRE 侧 `hcore_nav.py`(路径由 `HCORE_MJC_DIR` 指定, 默认已指向工作区)。

```bash
cd integrations/hcore-mujoco-local-eaios
MUJOCO_GL=egl /home/sunweihao/miniconda3/envs/py310/bin/python \
  -m hcore_mujoco_local_eaios --speed 8

# 可选参数
#   --xml    场景文件 (默认 $HCORE_MJC_DIR/arena_roboguide.xml)
#   --roles  角色列表 (quadruped / rover / ptz / arm)
#   --ports  与 --roles 一一对应的端口 (默认 28111 28112 28113 28114)
#   --speed  仿真倍速, 1.0 为实时
#
# 录像 (可选, 详见下文"离屏录像")
#   --record PATH.mp4        离屏录制; 不传则不录
#   --record-fps 15          帧率 (按仿真时间节流)
#   --record-distance 5.5    相机距离 (米)
#   --record-cam NAME        改用场景内相机 (rover_cam/dog_cam/ptz_cam)
#   --record-width/--record-height  分辨率 (默认 960x540)
#
# 拍照 (PTZ 巡检 observation.survey, 可选)
#   --shot-dir DIR            四张照片落盘目录 (默认 artifacts/ptz-survey)
#
# 轨迹日志 (可选)
#   --trace-dir DIR           每个 execution 的逐条轨迹 -> DIR/trajectory-<role>.jsonl
#   --trace-interval 1.0      采样间隔 (按仿真时间, 与 --speed 无关)
```

## 离屏录像 (无头环境可用)

`--record <path.mp4>` 离屏渲染并流式写 mp4, 不需要 X server / VNC / 端口转发:

```bash
MUJOCO_GL=egl python -m hcore_mujoco_local_eaios --speed 8 \
    --record /var/tmp/rg-run/demo.mp4
```

实现上有三个坑, 代码里都已规避:

- **Renderer 必须在使用它的线程里创建**。EGL 上下文不能跨线程 `makeCurrent`,
  主线程建、物理线程渲染会报 `eglMakeCurrent ... EGL_BAD_ACCESS`, 故延迟到物理
  线程首次抓帧时才建。
- **取景用绝对距离, 不用 `stat.extent`**。大地面把 `extent` 撑到 11 m, 按 extent
  取景会让围墙只占画面一小块; 实测 `distance=5.5` 恰好框住 4x4 m 竞技场。
- **退出必须收尾**。mp4 的 `moov` atom 在 `close()` 时才写, 进程被 `kill` 直接杀掉
  会得到一个无法播放的文件, 因此注册了 SIGTERM 优雅退出。

节流按**仿真时间**而非墙钟, 所以视频时长等于仿真时长, 与 `--speed` 无关 —— 渲染开
销只会拉长墙钟耗时, 不会让画面快进。降帧率或分辨率可减少开销。

## 路由契约

与 `integrations/habitat-local-eaios` 完全一致, 因此 `node-config/v0.7`
的声明式 workflow 无需任何特例:

| 路由 | 响应 |
|---|---|
| `GET /v1/health` | `{"state":"ONLINE","detail":"<runtime>/<role> sim_t=..."}` |
| `GET /v1/capabilities/<op>` | `{"state":"READY"\|"UNAVAILABLE","detail":...}` |
| `POST /v1/executions` | `{"execution_id":..., "state":..., "detail":...}` |
| `POST /v1/executions/status` | `{"state":..., "detail":...}` |
| `POST /v1/executions/cancel` | `{"execution_id":..., "state":"CANCEL_REQUESTED"}` |

响应一律**扁平**: 节点把整个响应体按步骤 id 记进 workflow context 的
`/steps/<step_id>`, 因此 node-config 里的 `local_handle.pointer` 与
`execution_state.state_pointer` 直指响应体顶层字段 (再包一层 `steps` 会让
pointer 解析不到, 表现为 `mapping source ... does not exist`)。

状态机: `ACCEPTED → RUNNING → COMPLETED | FAILED | CANCELLED`。

## 终态依据 (terminal_basis)

只允许下列本地依据, 便于把"本地完成"与"Mission 成功"作为独立证据分别记录:

| basis | 含义 |
|---|---|
| `local-odometry-arrival` | 里程计判定到达目的地 |
| `local-sensor-observed` | 本地传感器真实观测到目标 (量程+视场+视线全通过) |
| `local-planner-no-path` | 本地 A* 规划不出路径 |
| `local-capability-rejected` | 本地能力集不支持该模态 (如让 PTZ 认 ArUco) |
| `local-timeout` | 超过 execution 时限 |
| `local-stop-observed` | 取消请求后真实观察到停机 |
| `local-manipulation-placed` | 夹爪在目的地真实松开 (搬运完成) |
| `local-workspace-blocked` | 回转扇区被实体占据, 无法回转 |
| `local-reach-out-of-workspace` | 抓取点或放置点超出臂展 (固定基座臂) |
| `local-object-not-movable` | 目标在本世界内没有可动自由度 |

## 已验证 (本机实测)

```
dog   mobility.navigate  -> zone_north   COMPLETED (destination reached)
rover mobility.navigate  -> marker6      COMPLETED (destination reached)
ptz   observation.verify -> qr6.detected COMPLETED (observed qr6 as qr)
ptz   observation.verify -> marker6      FAILED    (capability mismatch: ptz cannot sense aruco)

arm   object.relocate  crate1 -> arm_home_drop  COMPLETED (placed, 真实搬运: 转 180° 取物 + 转回放
                                                           下, complete_after=place)
arm   object.relocate  crate1 -> arm_pick_spot  FAILED    (rover 停在回转扇区 0.59 m/+22.5°)
arm   object.relocate  crate1 -> arm_pick_spot  COMPLETED (rover 移出扇区后重试)
arm   object.relocate  crate1 -> arm_pick_spot  FAILED    (扇区初始即双向皆挡: short by obs2 /
                                                           long by dog — probed both arcs)
arm   object.relocate  crate1 -> zone_north     FAILED    (destination out of workspace)
arm   object.relocate  teddy  -> arm_home_drop  FAILED    (object has no movable DOF)
rover object.relocate  crate1 -> arm_home_drop  FAILED    (rover has no manipulator)
```
