# RoboGuide V2 架构基线

> 当前仓库架构语义的 source of truth。原始
> [`RoboGuide_Architecture_Baseline_V2.docx`](RoboGuide_Architecture_Baseline_V2.docx)
> 保留最初 V2 基线；后续已接受的演进由本文和编号 ADR 共同记录。

![RoboGuide V2 总体架构](../../images/roboguide-v2-overall-architecture.png)

## 1. 系统定位

RoboGuide 是面向异构具身智能体协作的通用分布式操作系统框架。它联合调度
Capability、Compute、Space 和 Time，同时保留每个节点的本地自治能力。

RoboGuide 不只是一个 Scheduler，还负责资源抽象、共享状态、任务与执行生命
周期、分布式调用、协同和恢复语义。

## 2. 逻辑架构

| 组件 | 职责 |
| --- | --- |
| Mission / Application | 提供外部 Mission / Goal，不直接控制设备 |
| Mission Intelligence | 持有文本指令的解释/澄清闭环，生成带 Context/ContextRole 的完整 MissionPlan、Task Graph 和 Execution Requirements |
| Control Plane | 完成能力匹配、分配提案、共享资源协调、计划提交、Group 内 TaskExecution 绑定和恢复决策 |
| Mission Orchestration | 持有完整 MissionPlan，推进 DAG readiness，并根据 Runtime execution facts 明确驱动 Mission/Group 终态 |
| State & Memory Plane | 横向维护证据、共享系统视图、分配状态、Shared Belief 和分域记忆 |
| Embodied Execution Group | 由 Control/Runtime 承载 Mission-level 多 Task 分布式执行上下文 |
| Distributed Embodied Runtime | 持续承载已 Commit 的 Group/TaskExecution 运行上下文，管理 execution identity、事件、timer、取消和 checkpoint/resume |
| Execution Coordination Relations | 在已 Commit 的并发 Role execution 之间维护与 NodeId 解耦的持续运行时约束 |
| Integration | 提供 Node Protocol、Messaging、Transport、Session 和 Router，不拥有执行生命周期 |
| Local Embodied Systems | 保留感知、导航、运动、硬件控制和即时安全能力 |
| Physical World | 被执行过程改变，并持续向系统反馈 Observation |

逻辑组件可以共址，也可以分布部署。部署拓扑不得改变组件的职责和权威语义。

外部用户入口是 Mission Request，而不是完整 MissionPlan。Mission Intelligence 在 instruction
仍有 open questions 时停留在 `NeedsClarification`，不得创建 Group；只有无歧义并通过计划
审查与部署风险策略后，才把内部 MissionPlan 提交给 Orchestration。Request/dialogue 的持久化
属于 Mission Intelligence，不是 State Node projection 或 Runtime execution state。完整边界见
ADR-0018。

## 3. 核心抽象

### Embodied Node（具身节点）

可被发现、能够执行任务或提供资源的系统参与者。节点类型可以包括 Robot、
Perception、Interaction、Compute 和 Infrastructure Node。Node 不等同于 Robot。

每台参与节点机器运行一个通用 `roboguide-node`，作为 RoboGuide Runtime 在该机器上的
接入端。它通过 Node Protocol 主动连接 RoboGuide Server，并在进程内部使用声明式
Local Integration Engine 连接一个或多个 Local Embodied Systems。配置与通用 driver 属于
Node Service；具体 EAIOS 的 facade 在部署侧维护，不是每种 EAIOS 各自部署的 RoboGuide 服务
或编译期插件。

Local Integration Engine 只执行部署者提供的、启动时完整校验的本地配置。配置声明
Local System、Capability、Sensor、Resource、固定 Endpoint、受限字段映射和执行生命
周期；不得把厂商 SDK、ROS Topic、Atlas/Pilot 等 Local How 提升为全局协议或 Control
语义。新增 Local EAIOS 不修改或重新编译 RoboGuide Server 与 `roboguide-node`。

### Capability 与 Resource

Capability 描述 Node 当前能够执行什么；静态能力支持不代表运行时一定可用。
RoboGuide 联合调度四类资源：

- Capability：可执行的具身或计算能力；
- Compute：CPU、GPU、NPU、模型和执行容量；
- Space：位置、路线、区域、占用和共享物理设施；
- Time：前置关系、同步窗口、截止时间和占用区间。

### Embodied Execution Group（具身执行组）

Mission 进入实际执行阶段后形成的长期分布式执行上下文，由 Members、Roles、多个
TaskExecution、已提交的 Resource Bindings、Recovery Context 和 Lifecycle 组成。v0.x
默认一个 Mission 创建一个 Group，但该策略不是未来拆分多个 Group 的领域硬约束。

Role 是 Task 内与具体 Node 解耦的职责槽位：Capability 说明 Node 是否具备承担
该 Role 的能力，Assignment 记录当前由哪个 Node 承担，Resource Binding 记录执行
该职责已经提交的资源。Task 完成只释放属于该 Task 的临时 binding/reservation，不销毁
Group。节点故障时，Group 内可以 partial release、rebind，并经 `Adapted → Active`
继续执行；只有 Mission 完成或最终失败后，Group 才进入 `Completed/Failed → Released`。

Member 与 Binding 必须区分：GPU Node 可以是 Member，GPU quota 是 Compute
Binding；走廊是 Spatial Binding，不是 Member。Group Manager 属于 Control Plane，
负责 committed binding、reservation、rebind 和 release authority；Group 的 live execution
context 由 Runtime 承载并跨节点运行。State 只保存二者的事实和投影。

### Execution Coordination Relation

Task DAG 表达完成前置关系，不表达两个已经运行的执行单元之间持续成立的约束。
MissionPlan v0.4 因此允许 `CoordinationContext` 声明有向 Execution Coordination Relation，并声明该 Context 的 Execution Coupling Mode。
关系端点引用 `(TaskId, RoleId)` 逻辑执行槽；Group 接受后补全 Mission/Group identity，Runtime
再把逻辑槽解析到当前 execution attempt。关系绝不直接引用 NodeId，因此 replacement/rebind
不会改变其 Mission 语义。

关系 specification 属于 Mission Intelligence 和被接受的完整 MissionPlan；Control 仍只拥有
commitment、binding 和 recovery decision；Runtime 只拥有 live endpoint resolution、关系状态、
有序归约和 checkpoint。State Plane 的 durable Event Log 保存 relation evidence，但 v0.1 不建立
第二份 live relation projection，也不主动触发恢复。

v0.1 只定义 `requires-active`：target execution 处于 Accepted/Running 时，source execution
必须持续处于 Accepted/Running。Runtime 将关系归约为 `Dormant`、`Pending`、`Satisfied`、
`Violated` 或 `Unknown`。`Violated/Unknown` 形成 coordination-required evidence，并 fence
受约束 Task 的成功归约；即使关系重新变为 `Satisfied`，也必须由 Control/应用恢复流程显式
确认后解除 fence。Runtime 不选择 replacement、不修改资源承诺，也不把关系违例伪装成
某个 Node 的物理失败。完整边界见
[`ADR-0020`](../../decisions/0020-execution-coordination-relations.md)。

v0.4 增加 `Independent`、`SequentialHandoff`、`ConcurrentCooperation` 和
`TightlyCoupledCooperation`。Mode 属于 Context，并可由 Task override；它只声明所需机制，
不固化控制算法。Context 可用明确的 `ContextRole + StateExportId + payload schema` binding
声明选择性的 pose/velocity，并直接按 logical Task/Role 读取 Runtime-owned execution status；
两类 evidence 组成 Group shared view，另可声明 typed map revision/frame；
Runtime/Orchestration 以只读方式从 State evidence 组装视图，并按现有 receive-time/TTL
返回 Fresh/Stale/Unknown，不从 channel 名称猜测字段语义。
Tightly-coupled Context 可声明 transport-neutral peer channel descriptor 与 Runtime 生命周期；
Node Protocol 只承载当前 session 与注册 Local EAIOS 识别的双端、receive-relative readiness
evidence，不承载 peer data-plane 消息。等待 readiness 的 committed/bound Task 保持 Ready，待
两端证据成立后由现有事件循环 dispatch，不把等待误作 Mission 提交失败。
Node config v0.6 可为每个 LocalSystem 声明至多一个固定 HTTP GET observer，读取该 Local
EAIOS 已建立端点的有界 readiness set；owner 与 TTL 来自启动配置而非响应。采样失败不制造
负证据，旧确认按 receive-time 到期并由 Runtime fence。
高频 relative-state 计算、纠偏、formation/grasp/safety control 保留在 Local EAIOS。Typed relation
的 state key、frame、freshness 等字段不包含阈值、公式或 DSL。当前 executable profile 只开放
`requires-active` 和 `shared-spatial-reference`；后者复用 typed localization evidence 校验当前
attempt/owner/map revision/frame 并进入现有 relation fence。详见
[`ADR-0026`](../../decisions/0026-execution-coupling-and-group-views.md) 与
[`ADR-0027`](../../decisions/0027-runtime-coordination-evidence-completion.md)。

## 4. 决策与承诺语义

```text
Plan → Match → Schedule → Propose → Coordinate → Commit → Bind → Execute
```

1. Capability Matching 输出 Candidate Set，回答 `Who can?`；
2. Embodied Scheduler 输出 Assignment Proposal，回答 `Who should / Where / When?`；
3. Shared Resource Coordination 检测竞争，并执行 Reservation、Negotiation 或重新分配；
4. Commit 使资源义务生效，并在 Allocation / Reservation State 中可观察；
5. Mission Orchestration 在执行阶段从完整 DAG 创建一个 Mission-level Group 和全部 TaskExecution；
6. Control 将每个 Task 的 Committed Plan 绑定回同一个 Group；
7. Runtime 接收 committed execution configuration，承载绑定后的执行过程并产生 canonical
   execution facts；Integration 只负责把 command/event 送达正确的 Node session。
8. Runtime 按 Mission relation specification 把当前 execution attempts 解析回逻辑 Task/Role
   端点，持续归约跨 execution 约束；该状态不改变 Proposal/Commit/Binding authority。

未提交的 Proposal 绝不能被当作已经生效的资源分配。

Mission Intelligence 中的 Actor 只表达跨 Task 的逻辑参与者和语义连续性，不携带物理
Node identity。若部署或实验必须指定某个 Actor 由某个 Node 实现，该关系作为独立的
Control-owned placement constraint 输入：它只收窄首次 Matching 的 Candidate Set，仍需通过
Schedule、Propose、Commit 和 Group Bind 才形成 authoritative `ActorBinding`。placement
constraint 本身不预留资源，也不属于 MissionPlan、Runtime 或 State projection。当前 v0
recovery 不具备 Actor 迁移 authority：已绑定或有 placement 的 Actor 只能在其权威 Node
上恢复；该 Node 不可用时 Candidate Set 为空，Group 保持 Blocked，不能借 Rebind 静默换狗。
未来 Actor 迁移必须增加显式 Control decision、事件与独立架构决策。

Canonical capability identity 使用 `namespace.name@version`；按最后一个 `.` 分隔 name，
因此 namespace 可以分层而 name 必须是单 segment。Node Config、Node Protocol 与结构化
MissionPlan 必须遵守同一可逆规则，详见 ADR-0017。

## 5. 状态、证据、信念与记忆

State & Memory 是横向基础设施，包含：

- Node / Resource State；
- Capability State；
- Task / Execution State；
- Spatial & World Model；
- Allocation / Reservation State；
- Shared Belief；
- Distributed Memory。

State 不假设单一 Global Truth。共同上层模型将 `Node`、`World`、`RoboGuide` 三类对象与
`Desired`、`Committed`、`Reported`、`Observed`、`Derived`、`Belief` 六类语义正交组合；
每条记录保留 source、channel、versioned payload schema、source-local observation time、
RoboGuide-local receive time、TTL 和可选 confidence。不同来源对同一对象的记录独立保留，
State 不自动执行 last-writer-wins 跨来源覆盖，也不自动把 Observation 提升为 Belief。

统一 State API 是对现有 authority 的只读 federation，类似 VFS 提供共同语义而允许底层
异构。Mission Orchestration 提供 Desired，Control 提供 Committed，Node/Shared Node State
提供 Reported/Observed，Runtime/Orchestration projection 提供 Derived；Belief 必须来自显式
命名的 provider。查询 facade 不成为新的写入 authority，也不绕过 Control、Runtime 或
Mission lifecycle。

Node Config v0.6 允许不同 EAIOS 选择性声明 State exports 和 Memory providers。State export
固定 local-system owner、对象、Reported/Observed 语义、schema、TTL、采样周期和本地
observation workflow；采样失败只让旧记录变 stale。部署 facade 必须保证 observation
无副作用，离线配置检查不能替代运行时验证。Memory provider 声明静态最大 scope，并可用固定
HTTP、dynamic gRPC 或 MCP workflow 实现 discovery/export/import；底层数据库、文件布局和厂商
接口保持异构。v0.5 provider 仍可作为 metadata-only 配置启动。Node Protocol v0.3 携带完整声明 snapshot 和有界
`StateObservationBatch`，沿用 application-accepted durable ACK；v0.2 endpoint 已退役并明确
返回迁移错误。

### Selective Memory Catalog v0.1

Memory 与实时 State 分离。v0.1 支持 Execution、Spatial、Semantic、Experience、Artifact
五类 immutable Memory revision，以及 Local、Execution Group、Global scope。Catalog 保存
owner、provider、visibility、schema、provenance、可选 CAS reference 和 node-local replica
evidence，但不取得本地 Memory 的 semantic ownership。

Scope、Visibility、Placement 必须分开解释：Scope 决定谁可语义消费；Visibility 决定 metadata
发现和 content exchange policy；Placement 是哪个 Node/provider 已 staging/import 的 evidence。
因此 `Local + Discoverable` 合法，其他 Node 可看到 metadata 但不可消费内容；Artifact reference
只表示已验证的共享 CAS content identity/availability，不表示 Node-local placement。manifest owner
是 semantic authority，实际副本由 `(NodeId, ConsumerProviderId, status)` evidence 表示。

节点侧 Local Memory Provider 是类似 VFS 的统一操作合同，不是统一存储实现。配置的
HTTP/dynamic gRPC/MCP workflow 是真实 EAIOS adapter boundary，真实 EAIOS 保留 Memory semantic
和 backend-storage authority。Node 内部 `LocalMemoryLedger`/`FilesystemMemoryLedger` 只保存用于
幂等与 fallback discovery 的 immutable manifest，并从中重建 JSONL index；真实 import workflow
成功后 ledger 不复制 payload bytes。缺少 workflow 时同一 filesystem 组件才作为 reference
backend fallback。配置的 `storage_directory` 只属于该 ledger/fallback 和受控 export handoff，
不是 EAIOS 存储位置。

其中 `discover` workflow 返回的是 provider 已授权 RoboGuide 发布的 publish-eligible
immutable Memory 集合，不是 Local EAIOS 的全部 Memory。Node Service 只负责传递查询/上下文、
做 manifest/scope 校验并执行 export、Artifact upload 和 publication mechanism，不拥有
Memory promotion 或 sharing selection policy；未配置 workflow 时才使用已记录 manifest 的
reference fallback。

Node 主动 export，消费者对明确 revision 选择性 import。ExecutionGroup 是 manifest 的 live
scope，由 invocation 的逻辑 `group_id` 做本地一致性校验，不是 static provider config；当前并未
形成 Controller-to-Node distributed Group-scoped authorization/handoff，不能把本地校验描述为完整
的跨 restart/rebind authority。

Memory、Artifact 与 Index 三者不合并：Memory 解释 owner/kind/scope/schema/provenance，Artifact
Store 只保存 digest-addressed opaque bytes，Index 只加速本地 metadata retrieval。Runtime 不成为
Memory DB，State 不保存 Memory blob；provider/CAS/catalog failure 只形成 retry/fence evidence，
不直接终结 Task 或 Mission。Replica mutation 必须由接收 Node 的 exact consumer provider 通过
admission；durable identity 为 `(MemorySelector, NodeId, ConsumerProviderId)`，同一 Node 的多个
provider 不互相覆盖，且 Imported evidence 不可被后续失败尝试降级。当前 Node Protocol 尚不包含
selective-import command，显式 consumer selection 与 durable command acceptance 仍是后续协议
工作，不能用自动全量拉取替代。

`Discoverable` Memory 可以只有 metadata；`Exchangeable` Memory 必须引用已经通过 digest
与 size 校验的 Artifact CAS bytes。消费者按明确 revision 选择性 pull，并记录
Staged/Imported/Rejected evidence；系统不全量复制、不建立 P2P 同步，也不把 replica evidence
当作 Task 完成。五类 manifest 使用共同 catalog，强类型领域扩展仍可增加约束。

### Spatial Memory Slice v0.1

首个 Distributed Spatial Memory 实现把地图作为 State & Memory Plane 的不可变 Artifact：
`MapId`/`MapRevisionId` 是逻辑引用，SHA-256 `ContentDigest` 是 bytes 身份。State 只保存
manifest、provenance、lineage、固定物理 `SpatialAnchor` 和 Node replica 状态；地图二进制
由独立 Artifact data plane 的 content-addressed store 持有。Producer 和 Consumer 可以属于
不同 Mission，Consumer 通过预分配 revision 显式 pull，不发生 Task-level handoff、ownership
transfer 或 Runtime 动态 output binding。

Node Protocol v0.3 不承载地图 bytes。Integration 提供 streaming Artifact transport，Node
Service 在受控 cache/sandbox 中完成 digest 校验和本地路径映射，再交给 Local EAIOS。当前 v0
只覆盖 immutable publish/import/localization verification；实时同步、融合、active-map 选择、
删除/GC 和安全策略延后。完整边界见 [`ADR-0016`](../../decisions/0016-distributed-spatial-memory.md)
与 [`contracts/spatial/v0.1`](../../../contracts/spatial/v0.1/README.md)。

Typed map catalog 是通用 Memory 的首条验证链路，而不是第二套 Memory authority。
`/v1/memories` 通过只读 adapter 暴露 map revision；带
`roboguide.spatial-memory/v0.1` schema 的发布仍必须走 `/v1/maps`，继续执行 anchor、lineage、
replica 和 localization evidence 校验。通用合同见
[`contracts/memory/v0.1`](../../../contracts/memory/v0.1/README.md)，State 合同见
[`contracts/state/v0.1`](../../../contracts/state/v0.1/README.md)，完整 ownership 决策见
[`ADR-0024`](../../decisions/0024-federated-state-and-selective-memory.md)。
强 localization evidence 同时可投影到当前 attempt 的 Runtime relation 与 Group spatial view；
Spatial Memory 仍是 durable evidence authority，Runtime 只保留 live reducer 所需的 identity，
不会保存 map bytes、pose stream 或本地控制状态。

```text
Observation → Source / Provenance → Timestamp → Freshness / Uncertainty
            → Fusion / Reconciliation → Shared Belief
```

Shared Belief 是面向决策的视图，不等于绝对 Ground Truth。冲突或过期证据必须
能够被表达和保留。Memory 具有 Local、Execution Group 和 Global 三种作用域；
Group 专属上下文默认不向全局广播。

## 6. Runtime 与本地自治

Runtime 是持续驱动已经 Commit 的分布式具身执行运行下去的执行环境。它维护
Mission-level Group 的 live context、TaskExecution 状态、execution identity、依赖推进、
timer、取消、事件归约以及 checkpoint/resume；它不执行 Matching、Scheduling、Reservation、
Commit 或 replacement selection。

Runtime 同时维护已接受 Mission 的 live execution relations，但只对当前事实进行保守归约。
进程恢复会把仍依赖非终态 execution 的关系恢复为 `Unknown` 并保持 reconciliation fence；
新的 physical attempt 只要重新占据同一个 `(GroupId, TaskRef, RoleId)` 逻辑槽，关系即可重新
解析，而无需修改 specification。当前中央 `execution_id` 与 physical attempt 的完整分离仍受
RT-G3 Gate 约束，关系实现不得用 NodeId 或可复用 execution 字符串充当稳定语义端点。

Integration 定义 Node Protocol、Messaging、Transport、Session、Router 和 wire conversion。
DDS、ROS 2、gRPC、MQTT 和序列化属于 Integration 实现选型，不因此获得 execution
lifecycle authority。当前 `core/integration` 只包含 formal Node Protocol v0.3 wire/session/router，
不依赖 Control、State 或 Runtime。依赖这些 authority 的 `IntegrationRuntimeBridge` 属于
Controller application composition，位于 `core/orchestration`，只把已验证的 transport facts
交给既有 Control/State/Runtime，不选择 replacement 或 Local How。

Node Service / Local Integration Engine 将 canonical execution intent 映射到本地 How，并维护
节点侧 durable execution continuity；它不管理 Mission 或 Group 生命周期。旧同步 HTTP
NodeGateway 已退役；`core/artifact-store` 是独立的 filesystem CAS infrastructure，属于
Artifact 数据平面，不是新增 Local EAIOS 的生产插件机制。具体 EAIOS 的 SDK、Immediate
How 与 Local Safety 属于 deployment-owned `integrations/` facade。

节点侧部署边界固定为单一 `roboguide-node` 服务。其 Local Integration Engine 可内置
多种通用传输驱动，但具体能力 owner 在单个 Node 配置内必须唯一，不得在未知物理执行
状态下自动切换本地系统或重放动作。

Extension Conformance v0.1 复用 Node Service 的配置编译器，要求 Node Config v0.6 为每个
exact capability 声明 readiness，并离线验证唯一 owner、固定 endpoint/method/service/tool、
受限 request mapping、execution state mapping、required resources 与选择性 State/Memory
声明。验证不联系 Controller
或 Local EAIOS，并显式声明没有执行 runtime/hardware probe。未知、timeout、重复 execution
identity 和 restart ambiguity 的 fencing 是 Node Service implementation guarantee，由独立的
engine/journal tests 覆盖，不冒充当前 deployment 的动态认证结果。
开发者路径与真实三-driver 配置样例见
[`docs/extensions/device-extension-conformance-v0.1.md`](../../extensions/device-extension-conformance-v0.1.md)，
ownership 决策见 [`ADR-0021`](../../decisions/0021-device-extension-boundary-conformance.md)，
旧 HTTP adapter 退役与 Artifact Store 隔离见
[`ADR-0022`](../../decisions/0022-retire-legacy-adapters-and-isolate-artifact-store.md)。

Node Protocol 的 `Registered` 与 sequence `Ack` 不是 transport receipt。Integration 通过
completion envelope 等待 Controller composition 使用既有 authority 接受并持久化 fact，
只有成功后才回复 Node；Integration 本身不解释或产生该 decision。语义见
[`ADR-0023`](../../decisions/0023-application-accepted-node-protocol-facts.md)。

Global Coordination 负责 `What / Who / When / Shared Where`。Local Embodied
Systems 保留 `Immediate How`、Navigation、Local Planning、Perception、Motion、
Hardware Control 和 Safety。

## 7. 对账与恢复

```text
Detect → Reconcile → Adapt
```

恢复只升级到完成任务所需的最低层级：

| 层级 | 所有者 | 处理方式 |
| --- | --- | --- |
| L0 | Local Autonomy | 避障、短程重规划、运动重试、安全停机 |
| L1 | Runtime | 重连或恢复调用/通信 |
| L2 | Execution Group | 替换成员、重新绑定或调整 Group |
| L3 | Scheduler / Coordination | 重新 Propose、Coordinate 和 Commit |
| L4 | Mission Intelligence | Task Graph 已无法满足 Mission 时重新规划 |

## 8. 已冻结不变量

- Proposal 与 Commit 相互区分；
- 已提交 Binding 是可观察的系统状态；
- Group 成员关系和 Binding 具有明确生命周期；
- Local Safety 不能被远程全局控制覆盖；
- Shared Belief 表达不确定性、过期性、来源和冲突；
- Node 在线状态与 Capability 可用性相互区分；
- 任务完成是系统级 Execution State，不是单次动作的返回值；
- Task DAG 与 live execution relation 相互补充：前者控制 readiness，后者约束并发运行；
- Execution relation 的稳定端点是逻辑 Task/Role，不是 NodeId 或 adapter-local handle；
- 恢复必须针对当前世界重新对账，不能重放过期命令；
- State 和 Memory 具有作用域；
- State record 必须保留 object semantic、source、channel 和 receive-time，不宣称全局真值；
- Memory local ownership、shared discoverability 与 selective exchange 相互区分；
- 替换实现不能改写架构语义。

## 9. 开放架构问题

Phase 1 真机 observation 必须区分 local-system/process health 与 exact canonical capability
readiness。Node 负责本地 probe，State 保存最新事实，Control 的共享 eligibility predicate
消费事实；Integration 不推断 readiness。Spatial localization 的强验证证据属于横向 State &
Memory evidence，必须关联 execution 与 artifact identity，不能由 Runtime 或 `has_map=true`
自行推断。渐进方案见
[`ADR-0019`](../../decisions/0019-capability-readiness-and-localization-evidence.md)。

V2 有意保留七个问题：State Authority、Spatial Authority、Control Topology、
Execution Group Authority、Scheduling vs Runtime Coordination、Temporal Assurance
和 Resource Commitment Semantics。跟踪列表以及 MVP 决策见
[`implementation-backlog.md`](../../implementation-backlog.md)。

Execution Coordination Relation 当前从 execution lifecycle 推导 `requires-active`，并从 strong
localization evidence 推导 `shared-spatial-reference`；它仍不解析 hazard、距离、速度或触觉等
领域信号，也不提供硬实时 stop/pause actuation。其余 typed relation 虽可由 contract 表达，
但会被 implementation profile 在 Group 创建前拒绝。版本化条件事实、deadline/window、目标侧
协调命令和安全认证必须在获得真实场景证据后分别演进；Local Safety 始终保留最终权威。

## 10. 版本关系

V2 取代 V1.1，成为当前权威架构基线。V1.1 保存在
[`../v1.1/README.md`](../v1.1/README.md)，用于历史比较。架构变化必须先更新
基线，再同步总体架构图、README、PPT、论文和实现文档。
