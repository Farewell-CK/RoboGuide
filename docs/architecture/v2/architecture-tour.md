# 架构导览

代码核对：2026-11-26 · `main@3c7136d6`。图文描述代码职责与协议边界；实际设备结果须另查原验收证据。

<!-- diagram:overview:zh -->
![RoboGuide 总体框架](../../images/architecture/2026-11-26/overview.zh.png)

RoboGuide 将异构执行体、计算节点和交互设备组织为共享 Mission 的任务、调度、执行、状态与恢复闭环。它联合考虑 Capability、Compute、Space、Time，同时保留 Local EAIOS 的本地执行方式与即时安全权威。Controller 当前是模块化单体：各职责在同一应用事务中协作，不代表每个框都是独立微服务。

[Handbook](architecture-handbook.md) · [Eight figures](current-architecture.md) · [Flows](system-dataflows.md)
