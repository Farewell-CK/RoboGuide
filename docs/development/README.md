# 开发基线

> 状态：Bootstrap 进行中；MVP 定义仍为 Draft。起草日期：2026-08-17。
> V2 架构仍是权威基线。本文档将 V2 的职责转换为工程边界，但不冻结传输、
> 数据库、Schema 或算法。

## 1. 开发原则

1. 架构先于目录和框架。只有在具备单一职责、明确依赖方向、测试和负责人时，
   才创建模块。
2. 从模块化单体开始。代码中必须能够约束逻辑边界，但 MVP 不先拆成微服务集合。
3. 核心必须与仿真器和硬件无关。Isaac Sim、ROS 2、厂商 SDK 和真实机器人都通过
   deployment-owned facade 接入。
4. 第一份可执行证据使用确定性的 Fake Nodes。仿真和真实硬件是并行验证轨道，
   不是核心开发的前置条件。
5. 已冻结的 V2 语义优先于实现便利。边界变化需要 ADR；如果改变架构语义，
   还必须更新 V2 基线。

## 2. 仓库布局

下面的布局体现 V2 的职责边界。完整 MVP 仍为 Draft，因此未来路径只有在拥有
第一份真实实现后才会创建。

```text
core/
  domain/                  纯领域类型、不变量和状态机
  ports/                   由核心拥有、与传输无关的接口
  control/                 Node、匹配、调度、提案、Mission-level Group、TaskExecution、恢复和 Allocation projection
  runtime/                 发现、调用、Heartbeat、Lease 和诊断
  state/                   Shared Node、Allocation、source-aware State 与 Memory Catalog projection
  artifact-store/          filesystem ArtifactBlobStore implementation
  integration/             正式 gRPC Node Protocol v0.3 wire、session 和 router
  orchestration/           Mission orchestration 与 Controller integration-fact composition
  node-service/            单一 Node Service、声明式本地引擎和 durable journal
  testkit/                 Fake Nodes、虚拟时钟、Fixture 和故障注入
apps/
  controller/              组合根和进程生命周期
  integration-server/      多 Node gRPC session 与独立 Artifact HTTP composition root
  mission-service/         文本 Mission Request、澄清/审批与内部 plan submission 组合根
  roboguide-node/          每台节点机器唯一的通用 RoboGuide 服务
  real-node-smoke/         formal Node Protocol v0.3 probe 与合成 Execute simulation
mission/
  src/mission/             Mission Request 状态机、规划、合同校验和模型/Controller 适配器
  prompts/v0/              可版本化、可评审的 Interpreter、Planner 与 Reviewer Prompt
  tests/                   Mission 合同与 Adapter 的离线测试
simulation/                未来的仿真器集成适配器，首次实现时再创建
contracts/mission/         版本化的跨语言 Mission Plan 合同
contracts/node/            版本化的异构 EAIOS Node Contract wire binding
contracts/state/           source-aware State 上层语义
contracts/memory/          selective Memory catalog/exchange 上层语义
contracts/spatial/         版本化的不可变地图 manifest 合同
integrations/              部署拥有的 Local EAIOS adapter；只负责 vendor/local How mapping
config/                    不含凭据的运行配置
scenarios/                 版本化场景输入和预期事件轨迹
tests/system/              仅用于黑盒跨进程测试
tools/quality/             标准 Linter 未覆盖的仓库检查
```

当前 Bootstrap 已创建 `core/domain`、`core/ports`、`core/state`、`core/control`、
`core/orchestration`、
`core/runtime`、`core/artifact-store`、`core/integration`、`core/node-service`、`core/testkit`、
`apps/controller`、`apps/integration-server`、`apps/mission-service`、`apps/roboguide-node`、
`apps/real-node-smoke` 和 `mission`。Mission 通过
`contracts/mission/` 下的版本化 artifact 向 Rust 应用边界提供 Task Graph，
不在 Rust 进程中嵌入 Python。`core/state` 当前包含 Shared Node State、非权威 Allocation
View、source-aware State record 与通用/Spatial Memory catalog 的真实实现；旧 HTTP
reference transport 和 configured command bridge 已
退役，Artifact CAS 独立位于 `core/artifact-store`。正式 Local EAIOS 扩展由
`core/node-service` 的 Local Integration Engine 承担，deployment-owned facade 位于
`integrations/`，不代表真实 EAIOS backend 或硬件已经完成。Controller 组合 bridge 位于
`core/orchestration`，使 `core/integration` 能独立于 Control/State/Runtime 编译。
没有维护实现前，不得创建未来的 `simulation` 或系统测试路径。禁止提交
空目录。

## 3. 模块边界

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| Domain | ID、值对象、不变量和生命周期状态机 | I/O、SDK、存储和调度基础设施 |
| Ports | Clock、Event Log、Node Registry 等核心接口 | 厂商或传输类型 |
| Control | Match、Propose、Coordinate、Commit、Group 生命周期和恢复决策 | 硬件命令或本地运动 |
| Runtime | Discovery、消息语义、Invocation、Heartbeat 和 Lease | 全局资源选择 |
| State | Shared Node、非权威 Allocation View、source-aware records 与可重建 Memory Catalog | 调度决策、Lease authority、Reservation commitment、Group lifecycle、跨来源 truth fusion 或 artifact bytes |
| Artifact Store | filesystem CAS、digest/path safety、分块上传和端口实现 | Map/Task/Group 状态、Control commitment、设备 workflow |
| Integration | formal gRPC Node Protocol、session/lease fencing、Node router 和 wire conversion | Control/State/Runtime composition、Local How 与调度选择 |
| Controller composition | `core/orchestration` 中把 Node Protocol facts 接到 Control/State/Runtime 的 bridge | transport framing、Local EAIOS endpoint 或 reservation policy |
| Node Service | 单一节点服务、声明式 Local Integration Engine 和 durable journal | 每种 EAIOS 的代码插件或独立 RoboGuide 服务 |
| Deployment integrations | Robonix/ROS/vendor-specific Local How 与受控本地文件边界 | Mission/Group/Task 状态、Control 决策、State Catalog、Node Protocol authority |
| Mission Intelligence | 文本解释、澄清、Task Graph 草案、风险审批和 deliberation persistence | Node assignment、Commit、Group/Runtime execution lifecycle |
| Apps | 依赖组装、配置、启动和关闭 | 领域规则 |
| Quality Tools | 标准 Linter 未覆盖的静态仓库检查 | 运行时行为和生产依赖 |

允许的 Rust 依赖方向：

```text
apps -> node-service -> integration -> ports -> domain
apps -> orchestration -> integration/control/runtime/state -> ports -> domain
apps -> artifact-store -> ports -> domain
```

`domain` 不依赖其他内部项目。禁止循环依赖。MVP 阶段禁止在 Rust 核心中嵌入
Python；节点侧 Local How 仅通过配置固定的 HTTP、gRPC 或 MCP endpoint 通信。

### Heterogeneous EAIOS Integration Contract v0.2

Domain `ExecutionIntent` 将 canonical `OperationRef` 与 scalar parameters 绑定到
`PlannedTask` 的具体 Role；它与 RoleRequirement、Node assignment 和本地 Skill 名分离。
Matching/Scheduler 不解析 intent，Runtime 不翻译 intent。单一 `roboguide-node` 内的声明式
Local Integration Engine 使用启动时冻结的 HTTP、dynamic gRPC 或 MCP workflow 完成本地映射。

`NodeGateway` 位于 `core/ports/node_gateway.rs`，status 是 fallible，且错误分类不包含具体
传输类型。该同步 port 仍由 Runtime/testkit 的 legacy 测试合同使用，但旧 HTTP 实现已退役。
正式 registration/status/execute/state wire 属于 `core/integration` 的 Node Protocol v0.3。status
失败时 Runtime 只更新 liveness `Unreachable`，不会覆盖本地系统最后上报的 health。
`SystemMonotonicClock` 为真实进程提供 RoboGuide-local receive time。

### Device Extension Conformance v0.1

设备扩展的唯一正式机制是 `core/node-service` 内的 Local Integration Engine。新 Local EAIOS
只需部署自己的 HTTP、dynamic gRPC 或 MCP facade，并在 Node Config v0.5 中声明固定
connection、唯一 capability owner、exact readiness、execute/status/cancel workflow、受限
request mapping、状态映射、required resources，以及选择性的 State export/Memory provider；
不得在 RoboGuide core 增加厂商分支。
`core/integration` 只负责 formal Node Protocol wire/session/router，Controller 的
`IntegrationRuntimeBridge` 位于 `core/orchestration`。完整可验证路径和真实配置样例见
[`docs/extensions/device-extension-conformance-v0.1.md`](../extensions/device-extension-conformance-v0.1.md)，
对应 ownership 与 conformance 决策见
[`ADR-0021`](../decisions/0021-device-extension-boundary-conformance.md)；旧 HTTP adapter
退役与 Artifact Store 隔离见
[`ADR-0022`](../decisions/0022-retire-legacy-adapters-and-isolate-artifact-store.md)。
Node Protocol application acceptance 语义见
[`ADR-0023`](../decisions/0023-application-accepted-node-protocol-facts.md)。

离线命令不会打开 endpoint 或访问 Controller：

```bash
cargo run -p roboguide-node -- --validate \
  scenarios/extension-conformance-v0.1/node.toml
```

成功报告只证明静态配置；共享生命周期规则作为 Node Service implementation guarantee
单独列出，并不表示当前 facade 已执行 runtime probe。认证、真实状态值、物理副作用、
Local Safety、取消和重启语义仍需在 deployment-owned facade/硬件上单独验证。

每个 canonical capability 在 Node 配置内只有一个 local-system owner；endpoint、method、
tool 和 descriptor 都由本地配置固定，网络输入只能进入受限 JSON Pointer/白名单函数映射。
SQLite WAL journal 在本地 dispatch 前持久化 execution identity，Unknown 不自动重放。

`apps/real-node-smoke` 默认仅 probe formal Node Protocol；`--simulate-execute` 配合显式
Controller HTTP endpoint 提交一个 synthetic Mission，经真实 Match/Commit/Dispatch 后只发送
合成 execution facts。每次 probe 使用唯一 capability contract，只能匹配本次 synthetic Node，
不触发真实 action，也不构成 real-device verification。

### State & Memory Plane — Slice v0.1: Shared Node State

当前已实现的 State Port 为 `SharedNodeStateReader` 和
`SharedNodeStateWriter`。领域对象 `NodeStateSnapshot` 组合
`NodeRegistration`、Local EAIOS 上报的 `NodeStatus` 与 RoboGuide 观察到的 timestamped
liveness；`core/state` 使用 `BTreeMap` 保存最新已接受事实，并拒绝旧 observation
覆盖同一状态维度中的更新事实。

Control 不再私有保存 `NodeRegistration` 或 `NodeStatus`。Registration 和 Heartbeat
通过 Writer 更新 Shared State；Matching、Proposal validation 和 Rebind validation
通过 Reader 读取当前事实。State 不输出最终 `schedulable` 结论：health、freshness
TTL、lease validity 和 requirement eligibility 仍由 Control 判定。

### State & Runtime Integration — Slice v0.1: Node Observation Ingestion

Runtime 通过 `NodeGateway.status()` 从 Local EAIOS / Vendor facade 获取 health，形成
transport-neutral `NodeHealthObservation`，并仅依赖 `SharedNodeStateWriter` 写入 State。
Runtime 不依赖 `core/state` concrete crate，也不保存跨 Mission 的 shared snapshot。

Reported Health 与 Liveness 是独立事实：前者来自 Local EAIOS，后者来自 RoboGuide 的
可达性观察。Runtime 成功读取 node status 时，以 Runtime Clock 记录 `Reachable`；
Control 暂时仍在 lease expiry 时写入 `Unreachable`，但不再把 reported health 改成
`Offline`。Matching 综合 reported health、health freshness、liveness、active lease 和
capability，State 本身不输出 `schedulable`。

本切片没有 Reconciliation loop，也不会根据 State 变化自动 Block、partial release 或
rebind。Runtime networking、真实 Local EAIOS facade 和 Lease/Heartbeat 最终 ownership 仍未解决。

### State & Runtime Integration — Slice v0.2: Observation Time Semantics

Node health ingestion 明确区分两个时间：`NodeStatus.observed_at` 是 Local EAIOS 的
source-local observation time；`NodeHealthObservation.received_at` 是 RoboGuide
Runtime/Control 接收该 observation 的本地时间。Registration 以 admission timestamp
初始化 receive time；Runtime 使用自身 `Clock.now()`；Heartbeat 使用 `received_at`。
这些入口均保留 source time，不要求 source 使用 RoboGuide Clock。

State 当前按 RoboGuide `received_at` 决定同一 Node health observation 的新旧，即使
source time 数值回退，只要 receive time 更新也会接纳。Control 的
`max_status_age_ms` 同样只计算 `now - received_at`。Liveness 的 `observed_at` 与 Lease
的 issued/expiry time 继续使用 RoboGuide-local 时间域。

Receive-ordering 只是当前 deterministic bootstrap policy，不是 distributed event
ordering solution。本切片没有实现 NTP/PTP、clock offset estimation、Lamport/Vector/
HLC 或全局时钟同步，也没有实现 Reconciliation。

### State & Memory Plane — Source-aware State v0.1

`StateRecordReader/Writer` 和 `StateRecordProjection` 保存带来源的 State channel。对象由
`Node/World/RoboGuide + object_type + object_id` 标识；语义为
`Desired/Committed/Reported/Observed/Derived/Belief`。精确
`(object, semantic, source, channel)` key 使用 RoboGuide `received_at` 与 source sequence
排序，独立来源不会互相覆盖。payload 是最大 64 KiB 的 versioned JSON，并保留 TTL、
source-local time 与可选 confidence。

Node Protocol v0.3 只接受当前完整 registration 已声明的 Reported/Observed export，单 batch
最多 64 条、总 payload 最多 512 KiB，并使用 heartbeat/readiness 共用的 management
sequence。`IntegrationRuntimeBridge` 在一个 candidate projection 中原子验证整个 batch，
然后持久化 `StateRecordObserved` evidence；它不更新 health、lease、Control binding 或
Runtime lifecycle。Node 采样失败不发送替代值，已接受记录按 TTL 变 stale。

Controller 的 `/v1/state/providers` 与 `/v1/state/records` 是只读 federation：MissionPlan、
Control Group、Shared Node State、Mission/Runtime projection 和 external State records 仍由
原模块持有。当前没有 Belief provider，API 也没有通用写入口。合同与 authority 见
[`contracts/state/v0.1`](../../contracts/state/v0.1/README.md) 和
[`ADR-0024`](../decisions/0024-federated-state-and-selective-memory.md)。

### Control Plane — Embodied Scheduler v0.1

`DeterministicBootstrapScheduler` 是 stateless policy component，不存入 `ControlPlane`。
Normal 输入 `TaskRequirement + CandidateSet + SharedNodeStateReader`，输出
`TaskSchedulingDecision`；Recovery 输入 role-scoped `RecoveryCandidateSet`，输出
`RecoverySchedulingDecision` 或 `NoSelection`。两条路径共用同一私有 `select_role`，不重新
执行 health/freshness/liveness/lease/capability eligibility filtering，也不遍历 Candidate
Set 之外的 State nodes。

Policy 对 Candidate NodeId 稳定排序；无 ResourceKind 时选择空 resource list，有
ResourceKind 时对 selected Node 的同类 declared ResourceId 稳定排序，并选择一个尚未在
当前 Task decision 使用的资源。多 Role 仅采用 declaration-order first-feasible greedy，
不做 backtracking；无法形成完整 decision 时返回 `NoFeasibleSelection(RoleId)`。

Scheduler Decision 只是 selection evidence，不是 Assignment Proposal、reservation、Group
binding 或 State truth。Composition layer 仍分别调用 `propose`/`propose_role_recovery`、
Commit 和 Bind/Rebind。Scheduler 只回答 Who should；Capability Matching 回答 Who can；
Shared Resource Coordination 决定资源能否 Commit；Execution Group Manager 应用 committed
collaboration。

Mission Actor 的物理 placement 是独立的 Control deployment policy，不是 MissionPlan 字段。
可选 `(MissionId, ActorId) -> NodeId` constraint 在首次 Matching 时生成 singleton candidate，
但不会提前创建 ActorBinding 或 reservation；Group Bind 会再次校验 constraint，成功后才按
[`ADR-0007`](../decisions/0007-mission-actor-continuity.md) 建立 continuity authority。未注册、
不健康、无有效 lease、不可达或 capability/contract 不满足的 constrained Node 保持 Task
Ready/deferred，不会回退选择其他 Node。

v0.1 未实现 Capability × Compute × Space × Time optimization、load-aware placement、
spatial/traffic/deadline scheduling、priority/fairness/preemption、batching、bidding/auction、
RL/LLM policy 或 Scheduler persistence。演化这些能力前需要真实 Compute Load/Queue/GPU
Memory、Pose/Travel/Traffic、Time Window/Deadline/Duration 与 contention evidence。

### State & Memory Plane — Allocation State v0.1

`ControlPlane.reservations` 是 resource commitment 唯一 authority；`allocation_snapshot`
交叉验证 reservations、Execution Group assignments 和 pending recovery commitments，生成
完整 `AllocationViewSnapshot`。Phase 仅为 `Committed`、`Bound`、`RecoveryPending`，不
复制 GroupLifecycle，也不创建 Free/capacity/load/contention records。

`AllocationStateWriter::replace_allocation_view` whole-view 替换独立
`InMemoryAllocationState`，Reader 提供单资源、稳定 ResourceId 顺序和 `projected_at`。
State 拒绝旧 projection 覆盖更新 view，但不重新验证 Control authority。Authority mutation
与 projection refresh 不是共同 transaction：projection 可以滞后，State write 失败不影响
Commit，反向修改 State 也不改变 reservation。

Normal Commit 投影 Committed；Mission-level Group 将 committed plan 绑定到对应
TaskExecution 后投影 Bound；partial release 删除受影响 record；Recovery Commit 投影
RecoveryPending；Rebind 后转 Bound；Abort/Release 后 record 消失。
orphan 或 ownership 不一致的 Group reservation 会使 projection builder 返回 invariant error。
该 ownership 记录在
[`ADR-0005`](../decisions/0005-allocation-state-projection-authority.md)。Scheduler v0.1
保持不变且不读取 Allocation View；Scheduler v0.2 是后续独立工作。

### Control Plane — Reconciliation & Recovery Slice v0.1

当前只处理 Active Execution Group 中恰好一个 assigned node 不再满足 Control
eligibility policy。`assess_group` 读取 Control-owned Group desired configuration 与
Shared Node State observed facts，复用 Capability Matching 的 health、receive-time
freshness、liveness、lease、capability predicate，返回 `NoAction` 或
`RoleRecoveryNeed`；assessment 不修改 Group。

`begin_role_recovery` 只编排既有 `block_group` 与 `release_role_binding`，使 Group 保持
Blocked 并仅将失败 Role 置为 unbound。Reconciler 从不替 Scheduler 选择 replacement。

#### Recovery Reassignment Pipeline v0.2

`match_recovery_candidates` 只对失败 Role 使用共享 eligibility predicate，排除 failed
node，并允许返回空 `RecoveryCandidateSet`。带 Actor 的 Role 还必须服从既有
`ActorBinding`（首次绑定前服从 deployment placement）；v0 不隐式迁移 Actor，因此权威
Node 就是 failed node 时返回空集合并保持 Group Blocked。外部 bootstrap Scheduler 必须从
该 Set 显式选择 Node；`propose_role_recovery` 验证 candidate membership 与 resource
declaration，但不创建 reservation 或修改 Group。

Mission-level Group 在创建时从已接受的完整 MissionPlan 固化 Task/Role requirement
metadata，并随 Control checkpoint 持久化。Assess、Match、Propose、Commit 均拒绝与该
metadata 不一致的 caller-supplied `TaskRequirement`；调用方不能通过删除 Actor 或改变
contract/capability/resource requirement 绕过 Control authority。旧的无元数据 legacy
single-Task Group 仅保留兼容路径，新 Mission execution 不得使用该路径。

`commit_role_recovery` 在 commit time 重新验证 Group/TaskRef/Role、Blocked/unbound、node
eligibility、Actor authority、failed binding、resource ownership 和 conflict，完成全部检查
后才原子写入 ControlPlane 唯一的 reservation authority，返回
`CommittedRecoveryAssignment`。此时
Group 仍为 Blocked/unbound。`rebind_role` 只接受 committed value，并验证 reservation
确实属于同一 TaskRef/Role/ExecutionGroupId 后更新 assignment，进入 Adapted；随后由
`activate_group` 返回 Active。

职责保持为：Who can = Capability Matching；Who should = Scheduler boundary；Can resources
be committed = Shared Resource Coordination；Apply committed collaboration change = Execution
Group Manager/Rebind。Proposal != Commit，Commit != Rebind。

#### Recovery Commitment Lifecycle v0.3

Commit 成功后，Control 以 `(ExecutionGroupId, RoleId)` 保存唯一 authoritative pending
recovery commitment，并在同一无失败 mutation 阶段写入 replacement reservations。
`CommittedRecoveryAssignment` 是 caller handle，不是 authority；第二次 Commit 不会覆盖
同一 Group/Role 的旧 pending entry。

`rebind_role` 只接受与 Control pending authority 完全一致的 handle，并验证所有 resource
reservation 属于同一 TaskRef/Role/Group。Rebind 更新 Group、进入 Adapted 后 Consume
pending entry，但保留 reservation 作为 active binding ownership。

`abort_role_recovery_commitment` 先验证所有 replacement resource ownership，再统一删除
reservations 与 pending entry。Abort 后 Group 仍为 Blocked、Role 仍为 unbound，可重新
进行 Recovery Match/Proposal/Commit；Abort 不进入 Failed。`release_group` 负责 terminal
兜底，只有在 active assignment、pending commitment 和 reservation authority 相互一致时
才统一清理，Released 后该 Group 不得拥有 reservation 或 pending entry。

Pending recovery commitment 属于 Control/Shared Resource Coordination bootstrap，不是
Group lifecycle、不进入 `ExecutionGroup.assignments`，也不属于 Shared Node State。
持久化、crash recovery、timeout/auto-Abort、Allocation State 和 distributed transaction
仍未实现。该 ownership 决策记录在
[`ADR-0004`](../decisions/0004-recovery-commitment-lifecycle.md)。

没有 candidate/proposal，或 commit conflict 时，Group 保持 Blocked/RecoveryPending；
只有显式 `fail_group` 才表示 recovery exhausted。本切片没有 background loop、自动
Scheduler、multi-role/spatial/timeout recovery、Mission replanning、Runtime command
replay 或自动 failure escalation。

### State & Memory Plane — Distributed Spatial Memory v0.1

Spatial Memory 将地图分为 immutable manifest/catalog 与独立 blob data plane。`core/state`
只维护可从 evidence 重建的 revision/replica metadata；CAS 和 streaming transport 位于
Artifact Store/Integration；Node Service 负责声明式 staging、digest 校验和受控本地路径映射。
跨 Mission 的 Consumer 使用预分配 map/revision scalar reference，Runtime 不解析地图或驱动
Catalog。固定物理 anchor 是 v0 的 spatial authority，导入成功不等于 localization verified。
实现和非目标见 [`ADR-0016`](../decisions/0016-distributed-spatial-memory.md)。

### State & Memory Plane — Selective Memory Catalog v0.1

通用 `MemoryCatalogReader/Writer` 与 `MemoryCatalogProjection` 维护 Execution、Spatial、
Semantic、Experience、Artifact 五类 immutable revision metadata。manifest 记录 provider、
node/local-system 或 RoboGuide owner、Local/ExecutionGroup/Global scope、
Discoverable/Exchangeable visibility、schema、media type、provenance 和可选 Artifact ref。
Discoverable 允许 metadata-only；Exchangeable 必须引用已经由 filesystem CAS 重验 digest
和 size 的 bytes。replica evidence 只允许 Staged 后 Imported/Rejected 的保守转换。

Controller 的 `/v1/memory/providers` 发现声明 owner；Artifact HTTP 提供通用
publish/list/detail/replica endpoints，五类 Memory 共用一套目录语义。
typed Spatial map projection 通过 read adapter 出现在统一 discovery 结果中；map schema 的
发布仍必须走 `/v1/maps`，避免复制 anchor/localization authority。当前交换是 consumer
选择 revision 后通过现有 Artifact data plane pull，不做全量复制或 P2P。合同见
[`contracts/memory/v0.1`](../../contracts/memory/v0.1/README.md)。

当前切片仍不是完整 State & Memory Plane。以下内容延后：

- 完整 Execution/Task/Group 历史 projection；
- 可驱动 Control 的 Shared Belief policy 与 provider/fusion 实现；
- Provenance / uncertainty fusion；
- 多 Controller replication、HA、retention 与 access-control policy；
- Node 通用 Memory workflow 的自动 publish/import（地图仍是首条强验证链路）；
- State Authority conflict resolution；
- Lease ownership resolution。
- Map fusion、实时增量同步、active-map 选择、删除/GC、动态 output binding 和认证传输安全。

Spatial artifact 的本地 durability 属于 Node Service/Artifact Store 实现不变量：文件内容、rename、
hard-link 或 unlink 的父目录必须在 Journal/evidence 前同步；restart finalization 必须重验
staged target 的 size/digest。中央 CAS 与 Node 路径解析逐级拒绝 symlink/非目录并对叶子
使用 no-follow。Catalog 的 lifecycle 顺序取自 durable append 顺序，HTTP receive timestamp
在共享 writer gate 内按 replay high-water 单调分配，不把可回拨墙钟当 ordering authority。

### Durable Evidence Bootstrap

`core/state::SqliteEventLog` 提供 SQLite WAL-backed immutable event envelope。它保存
`event_id`、RoboGuide-local timestamp、correlation/causation identity、payload schema marker
和 `domain.EventPayload.json/v6` 版本化 JSON payload，供 Integration Server 的事件查询使用；
读取路径保留 v2-v5 兼容。v6 增加 source-aware State 与 generic Memory catalog/replica
evidence；v5 增加 Execution Coordination Relation evidence。该切片已验证跨进程
重开保留事件信封和 payload。当前 controller 另在同一 SQLite batch 中保存版本化
外层 `roboguide.controller-checkpoint/v10` 包含内层 v9
Control/Shared Node/State records/Runtime projection；
启动时要求 checkpoint 序号与事件末尾严格
一致。恢复会清空旧进程租约、将节点 liveness rebased 为 `Unreachable`，将非终态 execution
置为 `Unknown`，绝不自动重放物理命令。缺少 checkpoint、schema 不支持或序号不一致时
fail-closed；outer v9/inner v8 只支持一步迁移，缺少的 State record projection 恢复为空。
Memory catalog 从 event evidence replay，不进入 Runtime checkpoint。该机制是单控制器恢复切片，不等同于完整 event-sourced projection replay、
复制或 State Authority resolution。

Integration fact 与其同步触发的 Group lifecycle evidence 使用一个 SQLite event batch；
rejection 会 rollback，成功路径统一 commit。批次开放期间查询不会看到中间行。若 evidence
append 或 commit 失败，Integration Server 整体 fail-stop，因为当前内存 `ControlPlane`
不支持 transaction rollback；服务不得继续以已变更但未持久化的 authority 接受流量。

Control 当前仍持有 `NodeId -> NodeLease`、Reservation、Execution Group 及其
Blocked/Recovery/Partial Release/Failed/Release 生命周期。Lease 的 Control / Runtime /
State owner 尚未最终确认；Allocation View 与 Group observable projection 将由后续独立
State slice 处理。

## 4. 合同规则

- Proposal 和 Commit 是不同的类型和状态转换；
- Node 在线状态和 Capability 可用性是不同事实；
- Members、Roles、Resource Bindings 和 Shared Context 必须保持区分；
- Observation 携带来源、时间戳、新鲜度和不确定性；
- Event 不可变，并包含 Event、Correlation 和 Causation ID；
- 时长使用单调时钟，跨系统交换的时间戳使用 UTC；
- Adapter 消息需要版本化，序列化细节不能成为领域类型；
- Control 下发目标、角色、约束和 Binding；Local Systems 保留 Immediate How
  和最终安全权威。

## 5. 首个纵向切片门槛

完整 MVP 仍为 Draft。已批准的 Slice v0.1 记录在
[`../mvp-definition.md`](../mvp-definition.md) 中，第一条切片必须：

1. 注册节点、能力、健康状态和资源；
2. 消费经过批准的 Task Graph 和 Execution Requirements Fixture；
3. 产生 Candidate Set、Assignment Proposal、Commit 和 Execution Group；
4. 通过 Runtime 执行并记录有序事件轨迹；
5. 在物理上有效的边界注入至少一个已批准故障；
6. 保留已完成工作，只升级到必要的恢复层级；
7. 将无法恢复的物理状态报告为 Blocked/Escalated，绝不能报告为成功。

## 6. 变更门槛

Bootstrap 以及之后的每次实现变更都必须包含：

- 所实现的 V2 职责和所属模块；
- 完整记录的函数和公共类型；
- 正常、拒绝、超时和恢复路径的确定性测试；
- 跨模块行为的结构化证据，例如事件轨迹；
- 当依赖方向、权威、生命周期或公共合同改变时，新增 ADR；
- 同步更新命令和目录说明。

详细代码要求见 [`coding-standards.md`](coding-standards.md)。语言职责记录在
[`../decisions/0001-rust-core-python-edges.md`](../decisions/0001-rust-core-python-edges.md)。
DEAIOS 与本地运行时的边界记录在
[`../decisions/0002-deaios-node-contract.md`](../decisions/0002-deaios-node-contract.md)。
