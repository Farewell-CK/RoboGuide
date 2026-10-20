# 有界主动观测与证据封口

本扩展由 deployment-owned Habitat Local EAIOS 实现。公共 MissionPlan v0.8、Core 的
Proposal/Commit/Bind 与 Task satisfaction/release 权限保持原有边界。实现决策见
[ADR-0094](../decisions/0094-bounded-observation-and-local-contract-recovery.md)。

| 入口 | 可以执行 | 完成证据 |
|---|---|---|
| `observation.verify@v1` | 读取既有缓存，无模型/运动/额外 step | 正或负的有效读数；不证明 condition true |
| `observation.acquire@v1` | 本地有界接近指定实体与观察 | 当前原始缓存明确检测到该实体 |
| Contract recovery | 执行前要求模型重新选择 | 新返回的 action 独立通过相同 Guard |

acquire 的唯一参数是 `expected=detected(entity-ref)`。当前 profile 没有独立相机云台、
全场搜索或任意动作控制能力；预算也不证明超时目标不可能达到。新的 capability attributes
只有来源明确的 acquisition mode、visual modality、允许移动与本地 cycle 上限。
运动方式复用 Node 已有的 `base-type`（如 legged/flying），完整投影到 MI capability class；
主动观测的 `viewpoint-positioning` 则从同一注册事实约束为 ground/flight。Control 使用已有
typed capability constraints 做 Matching；逻辑 Actor 名称和文字描述均不选择物理端点。
当前部署仍未提供 FOV、gimbal 或 visibility guarantee，MI 不得猜测。

当前检测能力通过注册的 `condition-format=detected(entity-ref)` 声明精确参数格式，
该字段完整进入 MI capability class。acquire 的完成条件仅为检测成立；接近实体是
本地采集手段，并不证明已到达目的地。需要同时到达和检测的 Mission 仍须保留独立的
移动意图和 satisfaction 要求，不能将任意联合目标填入这个部署的 `expected` 参数。

deployment v0.4 增加 `enable_observation_acquisition`、
`observation_acquisition_max_steps`（1–4096）与 `contract_recovery_attempts`（0–3）。
它们由声明冻结，子进程实际使用值须与 Local How v0.10 一致；acquire 还要求原始传感器、
语义相机和导航技能可用。默认 Python adapter 开关关闭。新的感知/重排 deployment
模板显式选择本轮开发配置，旧冻结运行继续使用自己的旧模板。

开启 acquisition 的当前代码输出 Local How v0.14，保留 v0.11 的闭合
`observation_control=bounded-visual-feedback/v0.1` 声明。每次真实模型动作请求包含最新的
缓存 true/false/unavailable、原 Task/attempt/world/entity、sample step 和原始剩余预算；
读取不到保持 unavailable，同 step 冲突、跨 attempt/world 或时间回退拒绝。初始已有真实
positive sample 时直接完成 acquisition，零额外模型/运动，仍不声称联合目标已成立。

该 Local EAIOS 合同将选中的 wait 映射为 `min(16, remaining_simulator_steps)` 步暂停，
然后由原始高层策略继续决策。实际工具描述预先披露此持续时间，raw call 不变；映射证据
保留原始500步 wait 与实际派发。它不改 vendor 文件、技能实现或官方总步数，不扩展
导航/操作权限，也不更新原始 acquisition deadline。它是明确的 Local How 改动，不能在
与原生 EMOS 对照中声称动作调度完全等价。普通导航、搬运、idle 与历史 v0.9/v0.10
运行保留自己的等待合同。持续可见、驻留或多目标保持仍需实际能力和官方终态证据，不能
由这个反馈 profile 推导。

v0.12 还显式声明 `fresh-acquisition-navigation-command/v0.1`：仅在 Guard 接受实际模型
选择、原始 CrabAgent 完成同目标/同 agent 的 `nav_to_obj` 映射后，清除该端点 Oracle
动作的 `prev_nav_done` 与 `skill_done` 命令标记。原始动作此前对相同已完成目标直接跳过
计算并清除完成信号；新 acquisition 会因此没有运动、也没有完成反馈。现在每次真实新调用
重新进入原始到达计算，不复用历史成功、不写入 true、不调用额外 action/Gym/sensor，不清除
已采样目标点或路径规划器。来源 action 或目标不匹配时在修改前拒绝。其他端点、非 acquisition
与历史声明不变。`fresh-navigation-command` 证据绑定 attempt、raw call、映射和前后标记。
该修复不保证视野可见；导航完成后真实检测仍可能是 false，原始 acquisition 预算仍会耗尽。

v0.13 的 `observation_view_search=bounded-base-yaw-view-search/v0.1` 提供显式本地工具
`scan_for_object(target_obj)`。它只在 acquisition 与原始底盘控制接口均加载后对模型提供，
参数必须是该 attempt 的同一实体。Guard 通过后保留原始 CrabAgent 的工具名与参数，不把
模型选择的导航或等待替换成扫描。新的独立本地技能复用原始 HRL/Wait 的入口、计数与退出
机制，通过原始 joint action vector 发出自身端点的 yaw 分量；仍由原 actor/Gym 执行唯一
一次 step。支持实际 BaseVelAction、BaseVelNonCylinderAction（含 lateral layout）及
DroneBaseVelAction，启动时检查真实实例、端点归属、action layout、角速度和控制频率。

每次扫描最多32步、命令累计转角最多90度、角速度不超过1.5rad/s，并截短到原始 acquisition
剩余预算。没有平移命令、相机节点修改、传感器创建或抓放动作；原始底盘碰撞/运动结果仍然
有效，因此零平移命令也不承诺物理位置绝对不变。持物、来源改变、错误实体或过期 attempt
均拒绝。普通 navigation、relocation 和 idle 不获得扫描权限；新技能在 attempt 结束或
初始化失败时撤销，不编辑 peer 或 vendor 文件。扫描退出仅记录 `view-scan-finished`，
不意味着检测为 true，也不建立官方或 Mission 成功。它不能保证消除遮挡或修正垂直视野，
不能宣称与原生 EMOS 动作工具集等价。

v0.14 增加 `observation_motion_feedback=observed-own-scan-rotation/v0.1`。每个真实scan调用
在派发前冻结自身原始quaternion，只在自身scan skill的既有成功Gym step后读当前quaternion。
下一次模型输入、tool receipt和终止归档保留同一call ID/sequence的rotation-observed、
no-rotation-observed或unavailable。完全缺失基线或部分读取失败不能证明静止；q与-q不表示
不同朝向。内存只保留起点、最新值和标量计数，不在每步写盘或序列化JSON。

原始计时技能仍返回原始结果；扫描计时结束不证明已经转动，更不证明检测或benchmark成功。
读取失败不影响原动作或异常；运动阻塞的原因保持unavailable，不能由零转角猜测碰撞。
没有自动重试、改写动作、替换目标、改变footprint/碰撞容差/NavMesh或增加物理控制权限。
不同Local How控制器的几何可行性不一致需要独立的部署合同审查，不能靠放宽检查掩盖。

物理诊断开关还启用 `perception-diagnostics.jsonl`，以16步采样周期读取同一次既有 Gym
缓存。每条记录保留精确 entity/attempt/world、各 detector 的自身结果、原始整数语义图像
中目标像素数、原传感器的严格 `count > pixel_threshold` 比较、相机矩阵几何及当前缓存的
跨 agent 并集。相机锥内只表示几何推导，遮挡未知；缓存并集也不冒充官方谓词执行结果。
单 agent false 与官方 any-agent 条件必须区分，历史正样本不锁存为当前 truth。

诊断不调用 sensor/render/predicate/Gym/actor/RNG；不序列化完整图像。每条最多4个 detector、
每个最多8个相机、总计4,194,304像素比较；最多4096条记录，32条有界批写，报告丢样与开销。
读取与写盘失败不影响执行，终态补采失败也必须 flush 已有批次，并保留原执行异常。
关闭物理诊断时不读取图像或相机矩阵。该证据用于定位缓存/像素/视角差异，不能代替
正式 verifier。实际检测保持原始缓存语义，官方 `is_detected` 仍由 Habitat 独立计算。

planning world v0.4 的 `entity_geometry` 是最多 64 条 reset position，字段为
`entity_id`、`frame_id`、三项 integer `position_mm` 和 `up_axis=y`；未知位置进入 `gaps`。
它只是一个已观测快照，不能当作 route、future goal 或检测真值。

live `GET /v1/executions/closure` 返回 Mission、session digest、producer phase 和实际
publication flags。`closed` 表示发布尝试已经结束，只有 `evidence_flushed=true` 且无归档
错误时才能声称必要终态证据已封口。实际执行失败仍可拥有完整的失败归档。
Runner 最多观察 30 秒 / 64 页小状态响应，将每次读取保存在
`evidence-closure-archive.json`；失败归属 `HARNESS_ARCHIVAL`，不覆盖 `run-failure.json`。
legacy adapter 的 closure 为 unavailable；只有 live 的已接受 Request 启用该附加 barrier。

feasibility v0.6 的 `finality_profile` 固定三个中立字段：
`verifier_publication=after-all-session-slots`、`endpoint_capacity=1`、
`resource_release=after-task-satisfaction`。Controller 在资源保留的 final slots 上进行
有界候选匹配，并在创建 Group 前拒绝无法闭合的容量冲突；这不授予任何预留或释放权限。

感知与重排的 live deployment 还在每次 reset 后、Mission Service 启动前发布
`roboguide.execution-planning-evidence/v0.2`。该 artifact 将同一份 semantic evidence 的
`run/episode/scene/dataset` identity、Habitat 官方 terminal verifier source，以及
endpoint 数量、capacity-one、resource release、verifier publication 和明确的
`execution_topology` 支持绑定到一个 digest。旧 v0.1 保持可读，不获得其未发布的能力。
Planner、Reviewer 和 Repairer 看到的是这份不可变的 deployment declaration；它不是 live
Node inventory，也不选择 Actor 或 Physical Entity。MI 检查同一 Task 的同时 Role 需求和
终态 verifier 在所有 session slots 结束前持有的容量；independent Tasks 只有在 Control
实际释放后才能复用 endpoint。Planner/regenerate/Repairer 的新草案还通过 Controller
`/v1/missions/validate-plan` 复用提交时的发布合同校验，保留原始草案与多条结构化错误，
进入原有有界恢复。HTTP 失败、缺失接口和来源不匹配保持系统失败；前检不能授予提交、
调度、资源或物理可达性保证。需同步部署包含该接口的 Controller，不能回退为跳过前检。
逐 Task 可及时发布的独立 verifier 不受全局终态 source 的生命周期限制。
只读回复 v0.2 回显同一拓扑声明；模式、能力与资源生命周期的具体错误保持各自 code/path。
当任务需要条件为 true，snapshot verify 的负读数不能代替主动 acquisition；局部 acquisition
先后成功也不能被锁存为联合终态成功。诊断复用原缓存，不额外调用官方谓词或 render。
详见 [ADR-0109](../decisions/0109-published-plan-validation-and-bounded-draft-recovery.md)。缺失、篡改或跨 reset
的 artifact 会让 live Mission Service 在请求前 fail closed；offline prepare 可以只验证配置
模板，不宣称已经拥有本次 reset 的 verifier evidence。

真实验证必须同时保留 frozen input、精确源码/model/部署 identity、MI 请求与草案、
Control assignments、实际动作和反馈、initial/reset/terminal diagnostics、官方结果和
closure/provenance/admission。应分别汇报 true、false、unavailable，不能将本地 acquisition
完成当作联合官方成功，也不能把小规模诊断当成 E1 成绩。
