# RoboGuide

RoboGuide 是一套面向异构具身智能体协作的通用分布式具身智能操作系统架构。

当前架构 source of truth 是 [`RoboGuide_Architecture_Baseline_V2.docx`](docs/architecture/v2/RoboGuide_Architecture_Baseline_V2.docx)。V2 描述系统职责、协议语义、关键闭环和架构约束，但不固化 Schema、API、中间件或具体算法。V1.1 作为历史基线保留。

## V2 总体架构

![RoboGuide V2 Overall Architecture](docs/images/roboguide-v2-overall-architecture.png)

独立原图位于 [`docs/images/roboguide-v2-overall-architecture.png`](docs/images/roboguide-v2-overall-architecture.png)，并已嵌入 V2 DOCX。仓库内的结构化摘要见 [`docs/architecture/v2/README.md`](docs/architecture/v2/README.md)。

## 系统目标

RoboGuide 统一感知系统状态、物理世界状态和具身能力状态，并联合调度 Capability、Compute、Space、Time，使机器人、感知设备、交互终端、边缘算力和基础设施节点能够持续完成复杂现实任务。

它不是单纯的多机器人 Scheduler，也不以中央控制替代节点本地自治。RoboGuide 持续管理资源抽象、状态、任务与执行生命周期、跨节点协调以及故障恢复。

### 前期 MVP

MVP 方向是以普通多机异构任务验证领域无关的最小闭环：

- 节点发现、身份、Capability 声明、状态与健康更新；
- Mission、Task Graph 和 Execution Requirements；
- Capability Matching 与 Capability × Compute × Space × Time 联合调度；
- Assignment Proposal、共享资源协调与 Commit；
- Execution Group 创建、绑定、执行和释放；
- Observation、Shared Belief、Reconciliation 与分级恢复。

导盲是后续应用场景，不是核心架构或 MVP 的前置假设。该方向已经明确，
但具体 Mission、节点拓扑、故障矩阵、指标和退出条件尚未冻结。完整阶段边界
见 [`docs/project-goals-and-mvp.md`](docs/project-goals-and-mvp.md)，当前决策状态
见 [`docs/mvp-definition.md`](docs/mvp-definition.md)。

当前已批准首个实现切片：Node A 执行运输与算力角色，Node B 作为运输替代节点，
Edge 提供共享算力；A 故障后保留 Execution Group 上下文，只重绑定失败角色并
继续执行。完整 MVP 仍未冻结。

## 开发基线与首个 Bootstrap

开发基线仍以 MVP Definition Draft 为约束，但首个可运行的 Rust core bootstrap
已经开始。它用于验证领域模型、端口、控制、运行时和确定性故障恢复的边界，
尚不代表完整 MVP 已经冻结或最终运行时已经完成：

- ADR-0001 提议由 Rust 负责 Domain、Control、Runtime 和 State 等长期核心；
- Python 承载 Mission Intelligence、模型、仿真和研究型 Adapter；
- 当前 `mission/` 已提供确定性 Fixture Planner 和可配置的 Responses LLM Planner；
- Mission 输出使用 `contracts/mission/v0.1/` 中的版本化合同；每个 Role 分别声明
  Capability/Resource requirement 与 canonical `ExecutionIntent`；
- 当前实现从模块化单体和确定性 Fake Nodes 起步；
- `core/state` 已实现 `State & Memory Plane — Slice v0.1: Shared Node State`，
  Control 通过 transport-neutral Port 读取节点注册、能力、资源和最新健康事实；
- 核心 Rust 包按职责位于 `core/`，可运行组合入口位于 `apps/controller/`；
- `core/adapters` 已提供第一份 backend-neutral HTTP reference adapter，
  `apps/real-node-smoke` 默认只 probe registration/status，显式 `--execute` 才发送 intent；
- Python 工具链由 `uv` 和项目级 `pyproject.toml` 管理；
- 目标目录、依赖方向和首个异构任务闭环见
  [`docs/development/README.md`](docs/development/README.md)；
- 每个 Rust `fn` 和 Python `def/async def` 都必须有有效文档注释，完整规则见
  [`docs/development/coding-standards.md`](docs/development/coding-standards.md)；
- Rust/Python 职责边界提案由
  [`ADR-0001`](docs/decisions/0001-rust-core-python-edges.md) 记录，当前状态为
  `Proposed`。
- DEAIOS 与本地 EAIOS/厂商运行时的语义边界由
  [`ADR-0002`](docs/decisions/0002-deaios-node-contract.md) 记录，当前状态为
  `Proposed for MVP Slice v0.1`。
- 通用异构 EAIOS 接入、canonical operation 与 Local Skill 映射由
  [`ADR-0006`](docs/decisions/0006-heterogeneous-eaios-integration-contract.md) 记录。

完整 MVP 切片仍需单独冻结。后续目录按首次真实实现按需创建，不提交空目录，
不允许绕过已接受的模块边界。

## V2 逻辑结构

### Mission / Application

外部用户、Agent 或 Application 提供 Mission / Goal，但不直接操作具体设备。

### Mission Intelligence

负责 Mission Understanding、Task Planning、Task Graph 和 Execution Requirements，回答 `What needs to be achieved?`。实现可以使用 LLM、VLM、符号规划器或混合方法，但不能直接进行跨节点资源绑定。

### Control Plane

Control Plane 负责全局决策与协调：

1. `Capability Matching` 根据任务需求和 Shared System View 输出 Candidate Set；
2. `Embodied Scheduler` 联合考虑 Capability × Compute × Space × Time，输出 Assignment Proposal；
3. `Shared Resource Coordination` 处理竞争、Reservation、Negotiation 和 Commit，输出 Committed Plan；
4. `Execution Group Manager` 管理 Create、Bind、Activate、Adapt、Complete、Release 生命周期；
5. `Reconciliation & Recovery` 检测现实与计划偏差，并选择最小必要恢复层级。

Scheduler 的 Proposal 不是已生效分配。只有协调成功并 Commit 后，资源占用才成为系统认可的有效承诺。

当前实现 **Control Plane — Embodied Scheduler v0.1: Selection Contract &
Deterministic Bootstrap Policy**。Capability Matching 先产生 Candidate Set 回答 `Who can`；
无状态 `DeterministicBootstrapScheduler` 只在该 Set 内回答 `Who should`，返回
`TaskSchedulingDecision` 或 role-scoped `RecoverySchedulingDecision`。Decision 仍须通过
Normal/Recovery Proposal validation，Scheduler 不调用 Proposal、Commit、Rebind，也不读取
reservation authority 或修改 State/Group。

Bootstrap policy 对 NodeId 稳定排序并选择第一个可形成当前 Role selection 的 candidate。
ResourceKind 为空时不建议资源；存在 ResourceKind 时，只从 selected Node declaration 中按
ResourceId 稳定排序选择一个尚未在当前 Task decision 使用的资源。如果 declaration-order
greedy selection 无法避免明显的 exclusive resource 重复，则返回
`NoFeasibleSelection(RoleId)`，不执行 backtracking、retry 或优化。

Normal 与 Recovery 共用同一个私有 role-selection primitive。Recovery Candidate Set 为空时
返回 `NoSelection`，Group 继续 Blocked/Pending，不表示 recovery exhausted。该策略仅建立
Scheduler ownership/contract，不声称 optimal，也没有实现 Capability × Compute × Space ×
Time 联合优化、load/spatial/traffic/deadline awareness、priority、fairness、preemption、
batching、auction、RL 或 LLM scheduling。

当前实现 **Control Plane — Reconciliation & Recovery Slice v0.1: Assigned Node
Unavailability**：Control 将 Active Group 的当前 assignment 作为 desired execution
configuration，并通过 Shared Node State 检查 assigned node 是否仍满足与 Capability
Matching 相同的 eligibility policy。Assessment 只产生 `NoAction` 或
`RoleRecoveryNeed`，不会立即修改 Group。

```text
Observed node unavailable
  -> Recovery Need
  -> Blocked + partial role release
  -> role-scoped Recovery Candidate Set
  -> external bootstrap scheduler choice
  -> Recovery Assignment Proposal
  -> Resource Coordinate / Commit
  -> committed replacement rebind
  -> Adapted -> Active
```

Reconciler 不选择 replacement node，也不实现 Scheduler。Controller 通过
`DeterministicBootstrapScheduler` 在 role-scoped Candidate Set 上产生 selection，再通过
Control API 创建 proposal。Proposal 不写 reservation；Commit 阶段重新验证 node
eligibility、Role capability、resource ownership/conflict、TaskRef/Group/Role identity 和
failed binding，并原子建立 existing Group 的 replacement reservation；Rebind 只接受
`CommittedRecoveryAssignment`。没有 candidate、没有 proposal 或 commit conflict 都只
表示 `RecoveryPending`，不等于 recovery exhausted，不会自动进入 `Failed`。

该实现形成 **Recovery Reassignment Pipeline v0.2**：`Who can` 属于 role-scoped
Capability Matching，`Who should` 仍属于外部 Scheduler boundary，资源能否生效属于
Shared Resource Coordination/Commit，已经 committed 的协作变化才由 Execution Group
Manager Rebind。Proposal 不等于 Commit，Commit 也不等于 Group Binding。

**Recovery Commitment Lifecycle v0.3** 显式管理 committed-but-not-rebound ownership：

```text
Recovery Commit
  -> Control-owned Pending Recovery Commitment
     -> Consume through Rebind
     -> Abort and release replacement resources
```

Control 以 `(ExecutionGroupId, RoleId)` 保证同一 Role 至多一个 pending commitment；
`CommittedRecoveryAssignment` 只是 handle，真正 authority 是 Control pending collection
与唯一的 `reservations`。Rebind Consume 后删除 pending entry，但保留已经成为 active
binding 的 reservation；Abort 只释放本次 replacement resources，Group 保持 Blocked、
Role 保持 unbound。Terminal `release_group` 会交叉验证并清理 active bindings、pending
commitments 及所有指向该 Group 的 reservations，确保 Released Group 不再拥有资源。

Pending commitment 不是 Execution Group lifecycle state，也不写入 Shared Node State。
Abort 不表示 recovery exhausted；它允许后续重新 Match/Propose/Commit。

本切片未实现 background reconciliation loop、自动 Scheduler、multi-role failure、
spatial/task-timeout recovery、Mission replanning、自动 Runtime re-execution 或 recovery
exhaustion policy。

### Heterogeneous EAIOS Integration Contract v0.1

`ExecutionIntent` 以可扩展 `OperationRef(namespace, name, version)` 和稳定 scalar parameter
map 表达 `What to execute`。它不使用 enum 固化 operation，也不携带厂商 Skill、ROS action、
SDK method 或 shell command。MissionPlan v0.1 将 intent 与每个 Role 显式关联；Matching 与
Scheduler 不解析 intent，Runtime 只路由，Adapter/Local EAIOS 负责翻译成 Local How。

`NodeGateway` 保持 transport-neutral，registration 显式声明 `roboguide.node.v0.1`，fallible
`status()` 可报告 timeout/unavailable/protocol/rejected。status transport failure 不伪造
reported `Offline`，Runtime 保留旧 reported health 并记录 liveness `Unreachable`。

`core/adapters::http::HttpNodeGateway` 是第一份同步 HTTP/JSON reference transport；wire DTO
与 serde 只存在于 adapter crate。`ConfiguredCommandBackend` 仅允许 canonical operation
查找本地预配置 fixed argv，不接受网络 executable，也不拼 shell。HTTP 不是 Node Contract；
异步 Accepted/Started/Completed lifecycle、operation catalog/discovery 与真实设备 backend
仍未实现。合同见 [`contracts/node/v0.1/`](contracts/node/v0.1/)。

```bash
cargo run -p real-node-smoke -- --endpoint http://127.0.0.1:8081
cargo run -p real-node-smoke -- --endpoint http://127.0.0.1:8081 \
  --execute --intent scenarios/real-node-smoke/noop-intent.json
```

### Embodied Execution Group

Execution Group 是 Control Plane 之外、由 Runtime 承载的任务级动态分布式执行上下文。Group Manager 位于 Control Plane，Group 本体跨多个节点存在。

- `Members`：参与执行的 Robot、Perception、Interaction、Compute 或 Infrastructure Node；
- `Roles`：成员在当前任务中的职责；
- `Resource Bindings`：已提交的 Space、Compute、Device、Time 占用；
- `Shared Context`：仅当前 Group 需要的上下文；
- `Lifecycle`：Create → Bind → Activate → Adapt → Complete → Release。

`Blocked` 是等待 Reconciliation & Recovery 的非终态，不等于 Group 已失败或应被
销毁。单个 Role/Member/Resource Binding 失效时，Control 只 partial release 该
Role 的 binding 和 reservation，保留 Group identity、TaskRef 与其他有效 binding；
恢复成功后经 `Adapted → Active` 继续执行。只有 `Completed`，或恢复明确耗尽后的
`Failed`，才能执行 whole-group `Release`。

Role 是 Task 内与具体 Node 解耦的职责槽位：Capability 是 Node 能否承担该 Role 的
依据，Assignment 指明当前承担者，Resource Binding 则记录已经提交的执行资源。
如果 Task 直接绑定 Node，节点故障通常需要重新规划整个 Task；通过 Role 间接绑定，
系统可以只替换失败 Role 的承担节点，同时保留其他已完成工作、有效 Binding 和
Execution Group 上下文。

Member 与 Resource Binding 必须区分：GPU Node 可以是 Member，GPU quota 是 Binding；走廊是 Spatial Binding，不是 Group Member。

### State & Memory Plane

State & Memory 是横向基础设施，不是 Control → State → Runtime 的线性中间层。Nodes 和 Runtime 持续写入 Observation、Evidence 与运行状态，Control Plane 读取 Shared System View，并写入 committed desired state 和 allocation state。

```text
Observe → Update → Fuse → Believe
```

Shared Belief 是带有 Source、Timestamp、Freshness、Uncertainty 和冲突信息的决策视图，不等于绝对 Ground Truth。Memory 按 Local、Execution Group、Global 三种作用域管理，不要求全部全局同步。

当前代码只实现 **State & Memory Plane — Slice v0.1: Shared Node State**：

```text
Runtime / Adapter observation -> Shared Node State -> Control decision
```

`core/state` 使用确定性内存实现保存 Node identity、Local EAIOS/runtime descriptor、
Capability/Resource declaration、Local EAIOS 最近上报的 health，以及 RoboGuide 观察到的
liveness。State 保存“观测到了什么以及何时观测”，Control 仍根据 reported health、
freshness、liveness、lease 和 requirement 决定是否可参与 matching。

当前同时实现 **State & Runtime Integration — Slice v0.1: Node Observation
Ingestion**。`NodeGateway` 继续代表 Local EAIOS / Vendor Runtime / Adapter 边界；
Runtime 从 `NodeGateway.status()` 形成 transport-neutral `NodeHealthObservation`，通过
`SharedNodeStateWriter` 写入 State。成功读取 gateway 是 `Reachable` 证据；lease expiry
只更新为 `Unreachable`，不会把 Local EAIOS 最后上报的 health 篡改成 `Offline`。

该路径只让新的事实可被后续 Control decision 读取，不会自动触发 Block、partial
release、rebind 或其他 Reconciliation 行为。

当前进一步实现 **State & Runtime Integration — Slice v0.2: Observation Time
Semantics**。`NodeStatus.observed_at` 明确表示 Local EAIOS 的 source-local observation
time；`NodeHealthObservation.received_at` 表示 RoboGuide Runtime/Control 收到该事实的
本地时间。State 同时保留二者，但以 `received_at` 作为当前 bootstrap 的 health
observation 接纳顺序，Control TTL/freshness 也只比较 RoboGuide-local receive time。
Liveness observation 与 Lease 时间继续属于 RoboGuide-local 时间域。

该策略没有解决跨节点 clock synchronization 或 global event ordering。Source time
仅作为未来 provenance、offset estimation 和冲突推理的证据保留；NTP/PTP、clock
offset 和 distributed ordering 均延后。

当前同时实现 **State & Memory Plane — Allocation State v0.1**。Control 的
`reservations` 仍是 resource commitment 唯一 authority；`allocation_snapshot()` 将
authority 投影为完整 `AllocationViewSnapshot`，由独立 `InMemoryAllocationState` whole-view
replace。View 只表达：

- `Committed`：正常资源已 Commit，尚未 Bind，`group_id=None`；
- `Bound`：资源属于当前 Execution Group assignment；
- `RecoveryPending`：replacement 已 Commit 到 existing Group，但 Role 尚未 Rebind。

Projection refresh 独立发生，可以暂时滞后；State write 失败不回滚 Control Commit，State
内容也不能授予、拒绝或释放 reservation。Projection builder 会拒绝 orphan、重复或同时
Bound/RecoveryPending 的 Group reservation。Scheduler v0.1 当前不读取 Allocation View；
未来 Scheduler v0.2 即使使用该 view，也仍须由 Commit 重新检查 authority。该边界记录在
[`ADR-0005`](docs/decisions/0005-allocation-state-projection-authority.md)。

这不是完整的 State & Memory Plane。Execution Group State Projection、Physical/Spatial
State、Shared Belief、Provenance/uncertainty fusion、
Distributed Memory、Persistence/Replication、State Authority resolution 和 Lease
ownership resolution 均未实现。

### Distributed Embodied Runtime

Runtime 提供 Discovery、Messaging、Invocation、Heartbeat、Lease、Adapter、Diagnostics 等执行语义。它让跨机器调用、成员关系、资源绑定和状态传播真正发生，但不拥有全局资源选择权。

### Local Embodied Systems & Physical World

Local System 保留 Navigation、Local Planning、Perception、Motion、Hardware Control 和即时 Safety。RoboGuide 下发目标、角色、约束和资源绑定，但不把本地系统降级为 dumb slave。

## 三条核心语义链

```text
Plan → Match → Propose → Coordinate → Commit → Bind → Execute
Observe → Update → Fuse → Believe
Detect → Reconcile → Adapt
```

系统使用四类流表达这些关系：Decision / Control、Observation / State、Binding / Lifecycle、Adaptation / Recovery。

## Recovery Escalation Ladder

| 层级 | 所有者 | 典型处理 |
| --- | --- | --- |
| L0 | Local Autonomy | 局部避障、短程重规划、motion retry、安全停机 |
| L1 | Runtime | 短暂通信错误、调用失败、重连 |
| L2 | Execution Group | 成员替换、re-bind、Group adaptation |
| L3 | Scheduler / Coordination | 重新 Propose、Coordinate、Commit |
| L4 | Mission Intelligence | Task Graph 已无法实现 Mission 时重新规划 |

恢复目标不是盲目重放旧命令，而是在当前物理世界中恢复任务进展，并且只升级到必要层级。

## 当前开放问题

V2 仍保留七类架构问题：State Authority、Spatial Authority、Control Topology、Execution Group Authority、Scheduling vs Runtime Coordination、Temporal Assurance、Resource Commitment Semantics。它们记录在 [`docs/implementation-backlog.md`](docs/implementation-backlog.md)；MVP 具体场景、拓扑和验收指标的草案记录在 [`docs/mvp-definition.md`](docs/mvp-definition.md)。

## 仓库内容

```text
.
├── AGENTS.md
├── README.md
├── Cargo.toml
├── rust-toolchain.toml
├── pyproject.toml
├── config/
│   └── mission.toml
├── contracts/
│   └── mission/v0/
├── mission/
│   ├── src/mission/
│   ├── prompts/v0/
│   └── tests/
├── scenarios/
│   └── mvp-slice-v0.1/
├── tools/
│   └── quality/
├── core/
│   ├── domain/              # facade + allocation domain module
│   ├── ports/               # facade + allocation ports
│   ├── state/               # node and allocation projections
│   ├── control/             # node/match/proposal/coordination/group/scheduler/recovery/allocation
│   ├── runtime/
│   └── testkit/
├── apps/
│   └── controller/
└── docs/
    ├── README.md
    ├── architecture/
    │   ├── README.md
    │   ├── v2/
    │   │   ├── README.md
    │   │   └── RoboGuide_Architecture_Baseline_V2.docx
    │   └── v1.1/
    │       ├── README.md
    │       └── Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx
    ├── project-goals-and-mvp.md
    ├── mvp-definition.md
    ├── implementation-backlog.md
    ├── development/
    │   ├── README.md
    │   └── coding-standards.md
    ├── decisions/
    │   ├── 0001-rust-core-python-edges.md
    │   ├── 0002-deaios-node-contract.md
    │   ├── 0003-mission-plan-contract.md
    │   ├── 0004-recovery-commitment-lifecycle.md
    │   └── 0005-allocation-state-projection-authority.md
    └── images/
        ├── README.md
        ├── roboguide-v2-overall-architecture.png
        └── distributed-embodied-ai-os-architecture-v1.1.png
```

当前 V2 架构是有效基线；开发基线正在通过多个最小工程切片验证。Shared Node
Shared Node State 与 Allocation State v0.1 已实现，但完整 State & Memory Plane 和 MVP
Definition 均未完成；
完整 MVP 的测试、适配器和仿真环境尚未完成。

## Mission Intelligence 开发

Mission 配置位于 [`config/mission.toml`](config/mission.toml)，版本化 Prompt 位于
[`mission/prompts/`](mission/prompts/)。模型名、Provider、Prompt 版本、Responses
路径、推理强度、review 模型和存储策略均由配置提供；API Key 只从
`OPENAI_API_KEY` 读取。默认配置使用 `gpt-5.6-luna`，但远程明文 HTTP 会被安全
边界拒绝，生产与持续联调应使用 HTTPS 或 localhost 隧道。

```bash
uv sync --dev
uv run mission validate \
  --input scenarios/mvp-slice-v0.1/mission-plan.json
uv run pytest -q
```
