# ADR-0001：Rust 核心与 Python 边缘职责

- 状态：Proposed（提案）
- 日期：2026-08-17
- 范围：实现边界；不改变 V2 架构语义

## 背景

RoboGuide 需要确定性的生命周期和恢复行为，同时还要集成快速变化的规划器、
模型、仿真器和研究代码。单一语言要么降低实验迭代速度，要么削弱核心的正确性
边界。在合同尚未得到验证前就开始拆分分布式微服务，则会过早引入传输和部署选择。

## 提议的决策

Rust 负责长期稳定的系统核心：

- 领域类型、不变量和生命周期状态机；
- Capability Matching、Proposal、Coordination、Commit 和 Group 管理；
- Runtime 语义、Heartbeat、Lease、Invocation、Diagnostics 和 Recovery；
- 证据、共享状态视图、Allocation State 和事件持久化 Port。

Python 负责变化较快的边缘能力：

- Mission Understanding、Task Planning、LLM/VLM 和实验型策略；
- Isaac Sim 及其他仿真器 Adapter；
- 数据集、评测和研究工具。

如果该提案被接受，MVP 从 Rust 模块化单体、内存 Port 实现和确定性 Fake Nodes
开始。Python 首先针对版本化合同产生 Fixture 和 Adapter 输出。只有在这些合同
获得集成证据后，才引入真实进程边界。

MVP 阶段 Rust 不得嵌入 Python Interpreter。Python 不得重新实现 Commit、Lease、
Execution Group、Recovery 权威或最终 Local Safety 语义。本 ADR 不选择任何传输、
序列化框架、服务拓扑、数据库或 RPC 技术。

## 预期影响

- Rust 的编译期 Crate 边界可以约束核心依赖方向；
- 核心测试无需 Python、仿真器、网络或硬件即可运行；
- Python 保持快速迭代，同时可以在 Adapter Contract 后替换；
- 跨语言合同需要版本控制、兼容性测试和 Correlation ID；
- 部分类型需要在 Adapter 边界转换，SDK 类型不能泄漏到核心内部。

## 接受条件与重新评估触发条件

只有项目负责人审阅语言职责和 Bootstrap 成本后，才将本 ADR 改为 `Accepted`。
接受后，只有出现经过测量的证据，证明当前边界无法满足延迟、部署、安全或开发
效率目标时，才重新评估。改变语言职责、嵌入 Python 或将核心权威移入 Adapter，
都必须新增 ADR，并进行架构影响评审。
