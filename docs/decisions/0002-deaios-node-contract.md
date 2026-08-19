# ADR-0002：DEAIOS Node Contract v0

- 状态：Proposed for MVP Slice v0.1（面向 MVP Slice v0.1 的提案）
- 日期：2026-08-19
- 范围：DEAIOS 与本地 EAIOS 或厂商 Runtime 之间的语义边界

## 背景

DEAIOS 在全局范围内协调异构节点，而每个节点可能运行不同的 EAIOS、厂商 SDK、
本地规划器或安全控制器。当前 Rust Bootstrap 中已有进程内 `NodeGateway`，但这
不能变成所有节点必须采用同一种实现的假设。该边界需要足够稳定，以支持 Fake Nodes
和 Adapter，同时保留传输和部署选型的自由度。

## 决策

Node Contract 是语义合同，不是通信协议。它包含五组职责：

1. **Registration（注册）**：`NodeId`、本地 Runtime 身份/版本、Capability、
   Resource 以及最新 Health Snapshot；
2. **Scheduling Evidence（调度证据）**：Capability 可用性、Resource 所有权、
   Freshness，以及节点可以被纳入调度的条件；
3. **Execution Command（执行命令）**：Mission、Task、Execution Group、Role、
   目标 Node、Resource Binding 和 Correlation Identity。DEAIOS 下发目标、角色、
   约束和 Binding，不下发原始执行器轨迹；
4. **Observation（观测）**：完成、失败、安全停机、健康变化、时间戳、来源身份以及
   Correlation/Causation 信息；
5. **Lifecycle（生命周期）**：注册/刷新、Lease 和 Heartbeat 语义、命令接收、
   执行观测、释放和恢复升级。

全局与本地的权威边界必须明确。DEAIOS 负责 Matching、Proposal、Coordination、
Commit、Group 成员关系、Rebinding 和 Escalation。本地 EAIOS 负责 Immediate How、
Local Planning、Hardware Control 和最终 Safety。Adapter 将具体 EAIOS 或厂商 API
转换为该合同，并将 SDK 类型隔离在 Rust 核心之外。

## MVP Slice 范围

Slice v0.1 只要求类型化的进程内 Port 和确定性 Fake Nodes。它必须验证 Registration、
Matching、Proposal 与 Commit 的区分、Group Binding、Execution Observation、可恢复
的节点故障、Role Rebinding，以及没有替代节点时的 Blocked 升级。Lease/Heartbeat
行为属于合同语义，在接受真实多进程 Adapter 前必须覆盖测试。

本 ADR 不选择 gRPC、ROS 2、NATS、序列化格式、数据库、服务拓扑、仿真器或硬件 API。
当这些选择影响职责所有权或公共接口时，必须先从合同测试中取得证据，再通过单独的
决策确定。

## 影响

- 不同 EAIOS 实现可以通过各自 Adapter 接入；
- 核心测试保持离线，并独立于仿真器和硬件；
- 合同对象需要在 Adapter 边界进行显式版本控制和兼容性测试；
- 本地命令执行成功不等于全局 Commit 成功，也不等于任务成功。

## 接受证据

接受本 ADR 需要为正常路径、拒绝、资源冲突、超时/Lease 失败、替代恢复和不可恢复
Blocked 路径提供有序事件轨迹。只有负责人审阅这些轨迹和对应的 MVP Slice 证据后，
才能将本 ADR 改为 `Accepted`。
