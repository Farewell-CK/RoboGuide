# 模块地图与依赖边界

Domain 保存 ID、不可变值与不变量，Ports 提供传输中立接口。它们让策略、存储和 Local How 可以替换，而不改写核心语义。

## 职责与关键对象

ID、MissionPlan、ExecutionIntent、EventPayload；Clock、EventSink、State/Memory/Artifact ports。

## 主流程

新合同先明确身份、生命周期与谁有权写，再通过 port 注入实现。不得让 vendor SDK、HTTP DTO 或数据库行成为 domain authority。

## 代码入口

- [主入口](https://github.com/Farewell-CK/RoboGuide/blob/main/core/domain/src/lib.rs)：`core/domain/src/lib.rs`。
- [协作入口](https://github.com/Farewell-CK/RoboGuide/blob/main/core/ports/src/lib.rs)：`core/ports/src/lib.rs`。

## 实现状态

当前实际调用链与剩余工作统一记录在[实现状态](../status.md)，版本由[参考页](../reference.md)生成。本模块的存在不代表完整阶段或设备验收完成。

## 验证与排查

先检查原 identity/plan/attempt、当前 policy 与期限，再查上下游事务和 receipt。拒绝、超时、重放与重启必须保留原证据。测试使用显式软件配置，不把 FakeNode 或协议 fixture 当作设备。

## 相关边界

[架构与权威](../architecture-tour.md) · [概念](../concepts.md) · [生命周期](../lifecycle.md) · [接入指南](../integration-guide.md) · [State 与 Memory](../state-memory.md)

## 调用链详解

### 依赖方向

```text
apps → orchestration → control/runtime/state/integration → ports → domain
apps → node-service → integration → ports → domain
apps → artifact-store → ports → domain
apps/controller-replica → controller-replication → state/ports → domain
```

这是允许的职责依赖方向，不表示每个 crate 都直接依赖整行所有模块。Domain 不包含 I/O 或 SDK。

| 端口族 | 消费或实现 |
| --- | --- |
| Clock / EventSink | 注入时间和不可变 evidence |
| SharedNodeState / StateRecord | 来源记录与当前 Node evidence |
| AllocationState | 从 Control 单向投影，不产生 commitment |
| MemoryCatalog / MapCatalog | 元数据与 provider-qualified replica evidence |
| ArtifactBlobStore | 有界 opaque bytes |
| TaskSatisfactionEvidence | 独立满足证据，不替代 Runtime 完成 |

新增 port 先定义谁拥有状态、如何保持原身份与版本、失败如何表达 unknown，再选择具体存储或传输。
