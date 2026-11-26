# 架构阅读手册

代码核对：2026-11-26 · `main@3c7136d6`。图文描述代码职责与协议边界；实际设备结果须另查原验收证据。

RoboGuide 将异构执行体、计算节点和交互设备组织为共享 Mission 的任务、调度、执行、状态与恢复闭环。它联合考虑 Capability、Compute、Space、Time，同时保留 Local EAIOS 的本地执行方式与即时安全权威。Controller 当前是模块化单体：各职责在同一应用事务中协作，不代表每个框都是独立微服务。

<!-- diagram:overview:zh -->
![RoboGuide 总体框架](../../images/architecture/2026-11-26/overview.zh.png)


## 从一项工作理解系统

1. 应用先持久化权威状态、checkpoint 和 outbox，事务成功后才发送 Execute。
2. Node journal 绑定 invocation、workflow digest、资源与代次，重复命令只回放原记录。
3. 本地锁与容量门禁先于工作流；Local EAIOS 负责实际操作。
4. Node 的 CommandPersisted 回执证明命令已入日志；执行事实仍独立回传。
5. Controller 持久接纳事实后才 ACK；旧会话及过期依据不得授权新动作。

## 按问题查阅

| 问题 | 主题 |
| --- | --- |
| RoboGuide 总体框架 | [Atlas](current-architecture.md) · [Flows](system-dataflows.md) |
| 从对话到经过审查的计划 | [Atlas](current-architecture.md) · [Flows](system-dataflows.md) |
| 从候选到可靠执行 | [Atlas](current-architecture.md) · [Flows](system-dataflows.md) |
| 完成判定与资源释放 | [Atlas](current-architecture.md) · [Flows](system-dataflows.md) |
| State、Memory 与空间内容 | [Atlas](current-architecture.md) · [Flows](system-dataflows.md) |
| 未知、停止与分级恢复 | [Atlas](current-architecture.md) · [Flows](system-dataflows.md) |
| 四种协作模式 | [Atlas](current-architecture.md) · [Flows](system-dataflows.md) |
| 进程、存储与故障域 | [Atlas](current-architecture.md) · [Flows](system-dataflows.md) |

## 必须分开的证据

Runtime 归约当前各 Role 的执行事实，但不拥有 TaskSatisfied 权威。本地 Completed 进入 AwaitingSatisfaction；Orchestration 只能消费接受计划声明的 basis。execution-report 是可选声明，不等同于独立目标验证。verifier-evidence 则需要匹配原计划、谓词、当前 attempt、来源与时效。

Unknown 表示原执行是否产生副作用仍不明确。原所有权、停止围栏与预算继续保留，不能换一个 ID 重新执行。Cancel 回执、本地终态、独立安全状态和 Memory 撤权是不同的证明；进程退出只能证明原进程退出，不能自动证明设备停止。

[V2 semantics](README.md) · [Concepts](concepts.md)
