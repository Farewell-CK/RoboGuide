# 模块地图：Domain 值与端口（基础层）

对应 crate：[`core/domain`](https://github.com/Farewell-CK/RoboGuide/tree/main/core/domain)
（约 10,000 行）、[`core/ports`](https://github.com/Farewell-CK/RoboGuide/tree/main/core/ports)
（约 600 行）、[`core/testkit`](https://github.com/Farewell-CK/RoboGuide/tree/main/core/testkit)
（约 300 行）。模块地图首页 · 下一篇：[Control Plane](control.md)

## core/domain —— 跨权威共享的领域值

**定位**：Control、Runtime 与节点适配器共享的全部领域值的唯一定义处。
刻意零传输、零序列化 SDK、零仿真器依赖——它是核心依赖图的叶子节点，
所有其他 crate 依赖它，它不依赖任何兄弟 crate。

### 模块地图

| 分组 | 模块 | 职责（取自各文件模块文档） |
| --- | --- | --- |
| 基础 | `identity.rs` `time.rs` `error.rs` `event.rs` | 强类型身份、任务相对时间约束、不变量错误、不可变事件证据 |
| 任务/规划 | `mission.rs` `mission_plan.rs` `task_requirement.rs` `task_execution.rs` `task_satisfaction.rs` `duration_estimate.rs` `context.rs` `actor.rs` | Mission 目标、Task Graph、Group 内任务单元、语义满足证据、源感知时长估计、逻辑 Actor |
| 执行 | `execution/`（`command.rs` `intent.rs` `node_event.rs` `session.rs` `value.rs`）`execution_relation.rs` | 传输中立执行意图/命令/节点事件、共享世界执行会话（ADR-0045）、协同关系规格 |
| 节点群 | `node_registration.rs` `node_state.rs` `node_health.rs` `lease.rs` `capability.rs` `physical_entity.rs` | 注册与共享状态快照、reported health 与 observed liveness 分离、租约、能力、物理实体 |
| 分配/资源 | `allocation.rs` `role_assignment.rs` `resource.rs` | 可观察分配投影类型（非权威） |
| State/Memory | `memory.rs` `spatial_memory.rs` `spatial_replica.rs` `localization_evidence.rs` `state_model.rs` | 不可变 Memory manifest、地图 artifact 值、副本证据、强定位证据、源感知 State 记录 |

### 关键类型

`MissionPlan` / `TaskGraph` / `PlannedTask`、`NodeStatus` / `NodeRegistration`、
`EventRecord` / `EventPayload`、`ExecutionCommand` / `ExecutionIntent`、
`AllocationViewSnapshot`、`ExecutionRelationSpec`、`MapArtifactManifest`、
`StateRecord` / `StateSource`、`TaskSatisfactionEvidence`、`NodeLease`。

### 值得注意的设计

- 所有 schema 都有显式版本常量（`MISSION_PLAN_SCHEMA_V0_4`、`STATE_RECORD_SCHEMA_V0_1` 等），
  兼容解码路径集中可见。
- `state_model.rs` 的自我约束写进了模块文档：一条记录是"一次带来源的观察或声明"，
  **从不宣称全局真相**。
- 测试（`domain_tests.rs` 等 4 个文件）覆盖 Task Graph 环拒绝、计划身份不匹配、
  Memory 不变量等纯值语义。

## core/ports —— 传输中立端口目录

**定位**：核心拥有的全部端口 trait，全部只依赖 `domain`。实现方（`state`、
`artifact-store`、gRPC 层、测试替身）实现这些端口，核心逻辑只面向它们编程。

完整端口清单（每个一行）：

| 端口 | 语义 |
| --- | --- |
| `Clock` | 可注入单调时钟（测试用虚拟时间的基础） |
| `EventSink` | 追加不可变事件证据 |
| `SharedNodeStateReader/Writer` | Shared Node State 契约（含过期观察拒绝） |
| `AllocationStateReader/Writer` | 非权威 Allocation View 的替换与读取 |
| `MemoryCatalogReader/Writer` | 可发现通用 Memory 目录 |
| `NodeGateway` | 面向本地 EAIOS/厂商运行时的传输中立集成边界 |
| `ArtifactBlobWriter/Reader/Store` | 有界分块不可变 artifact 字节（刻意不暴露整块字节接口） |
| `MapCatalogReader/Writer` | 地图目录证据 |
| `StateRecordReader/Writer` | 独立带来源 State 记录 |
| `TaskSatisfactionEvidenceReader/Writer` | Task 满足证据 |

## core/testkit —— 确定性测试基础设施

**定位**：`VirtualClock`（无真实睡眠的虚拟时钟）、`InMemoryEventLog` /
`SharedEventLog`、实现 `NodeGateway` 的 `FakeNode`（可注入
`FailureMode::FailNext / FailNextAndReportStatus / SafeStopNext` 故障模式）。
只依赖 `domain + ports`，因此所有上层测试都是"针对端口 + 替身"的纯离线测试。

## 实现状态

- 三个 crate 均无 TODO/FIXME/占位标记；`deny(missing_docs)` + `forbid(unsafe_code)`
  全量生效，函数级文档是完备的。
- 兼容常量显式存在（如 `LEGACY_MEMORY_CONSUMER_PROVIDER_ID` 仅用于解码 pre-v7
  副本证据）——旧数据路径可审计而非隐式。

## 相关 ADR

[0002 Node Contract](../docs/decisions/0002-deaios-node-contract.md) ·
[0003 MissionPlan 合同](../docs/decisions/0003-mission-plan-contract.md) ·
[0011 事件证据编解码](../docs/decisions/0011-event-evidence-codec.md) ·
[0016 分布式空间记忆](../docs/decisions/0016-distributed-spatial-memory.md) ·
[0017 能力身份规则](../docs/decisions/0017-canonical-capability-contract-identity.md) ·
[0020 执行协同关系](../docs/decisions/0020-execution-coordination-relations.md) ·
[0024 联邦 State 与选择性 Memory](../docs/decisions/0024-federated-state-and-selective-memory.md) ·
[0033 Task 满足边界](../docs/decisions/0033-task-satisfaction-boundary.md)

---

模块地图：[Domain 与端口](domain-ports.md) · [Control](control.md) ·
[Orchestration](orchestration.md) · [Runtime](runtime.md) ·
[State & Memory](state.md) · [Integration 与 Node](integration-node-service.md) ·
[Mission Intelligence](mission.md) · [Eval 与集成](evaluation.md)
