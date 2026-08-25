# ADR-0010：单一 Node Service 与声明式 Local Integration Engine

## 状态

已接受（2026-08-25），取代 ADR-0009 中“按 EAIOS 编译 Adapter”的节点侧实现方式。

## 决策

每台节点机器只部署一个 RoboGuide 进程 `roboguide-node`。它内部包含通用 Local
Integration Engine，通过用户维护的本地配置连接一个或多个 Local EAIOS/runtime。
Adapter 不再是独立服务、动态库或 Rust 泛型实现；新增 EAIOS 不得修改或重新编译
RoboGuide。

引擎首版提供 HTTP、dynamic gRPC 和 MCP 通用驱动。配置声明固定 endpoint/method、
protobuf descriptor、能力与资源、顺序 workflow、状态轮询、终态映射，以及基于 JSON
Pointer 和白名单模板函数的字段转换。网络 invocation 不能改变 endpoint、method、tool、
descriptor 或可执行命令。配置在启动时整体校验并冻结，修改后重启生效。

每个 local system 必须配置独立 health workflow；Heartbeat 聚合这些真实本地事实，
不以 `roboguide-node` 进程存活伪造 Online。

一个 Node 可包含多个 `local_system`，但每个 canonical capability contract 必须有唯一
owner；重复 owner 导致启动失败，不自动故障切换。配置由节点部署者本地维护，RoboGuide
Server 不下发 Local How。

Node Protocol v0.2 的 Execute 显式携带 Control 已 Commit 的 resource IDs。Node 的
execution-scoped local lock 只用于防止本机误用，不授予或撤销 Control reservation
authority。

`roboguide-node` 使用 SQLite WAL journal 持久化 execution identity、canonical invocation、
workflow digest、committed resources、local handle、sequence 和状态。调用 Local EAIOS
前先记录 `Dispatching`；重启后无法确认是否已开始的 execution 进入
ReconciliationRequired，绝不自动重放危险物理动作。

## 后果

旧 `RobonixAdapter`、Robonix helper 和 `adapter_type` 分支从 RoboGuide 删除。Robonix、
ROS 2 或其他 EAIOS 仅作为本地配置示例；若本地接口无法由受支持驱动与受限映射表达，
部署方需在 Local EAIOS 一侧提供 HTTP/gRPC/MCP facade，该 facade 不属于 RoboGuide。
