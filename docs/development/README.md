# 开发基线

> 状态：Bootstrap 进行中；MVP 定义仍为 Draft。起草日期：2026-08-17。
> V2 架构仍是权威基线。本文档将 V2 的职责转换为工程边界，但不冻结传输、
> 数据库、Schema 或算法。

## 1. 开发原则

1. 架构先于目录和框架。只有在具备单一职责、明确依赖方向、测试和负责人时，
   才创建模块。
2. 从模块化单体开始。代码中必须能够约束逻辑边界，但 MVP 不先拆成微服务集合。
3. 核心必须与仿真器和硬件无关。Isaac Sim、ROS 2、厂商 SDK 和真实机器人都通过
   Adapter 接入。
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
  control/                 匹配、提案、协调、提交和 Group Manager
  runtime/                 发现、调用、Heartbeat、Lease 和诊断
  state/                   已实现 Shared Node State Slice v0.1；其他 State/Memory 延后
  adapters/                Rust 传输、持久化、ROS 和厂商适配器
  testkit/                 Fake Nodes、虚拟时钟、Fixture 和故障注入
apps/
  controller/              组合根和进程生命周期
mission/
  src/mission/             Mission 规划、合同校验和模型适配器
  prompts/v0/              可版本化、可评审的 Planner 与 Reviewer Prompt
  tests/                   Mission 合同与 Adapter 的离线测试
simulation/                未来的仿真器集成适配器，首次实现时再创建
contracts/mission/         版本化的跨语言 Mission Plan 合同
config/                    不含凭据的运行配置
scenarios/                 版本化场景输入和预期事件轨迹
tests/system/              仅用于黑盒跨进程测试
tools/quality/             标准 Linter 未覆盖的仓库检查
```

当前 Bootstrap 已创建 `core/domain`、`core/ports`、`core/state`、`core/control`、
`core/runtime`、`core/testkit`、`apps/controller` 和 `mission`。Mission 通过
`contracts/mission/` 下的版本化 artifact 向 Rust 应用边界提供 Task Graph，
不在 Rust 进程中嵌入 Python。`core/state` 当前只有 Shared Node State 的真实实现；
没有维护实现前，不得创建未来的 `adapters`、`simulation` 或系统测试路径。禁止提交
空目录。

## 3. 模块边界

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| Domain | ID、值对象、不变量和生命周期状态机 | I/O、SDK、存储和调度基础设施 |
| Ports | Clock、Event Log、Node Registry 等核心接口 | 厂商或传输类型 |
| Control | Match、Propose、Coordinate、Commit、Group 生命周期和恢复决策 | 硬件命令或本地运动 |
| Runtime | Discovery、消息语义、Invocation、Heartbeat 和 Lease | 全局资源选择 |
| State | 当前切片维护 Node registration、runtime descriptor、Capability/Resource declaration 和最新 health observation | 调度决策、Lease authority、Reservation、Group lifecycle、Belief 或 Memory |
| Adapters | 协议、仿真器、存储、模型、ROS 和厂商转换 | 核心策略决策 |
| Apps | 依赖组装、配置、启动和关闭 | 领域规则 |
| Quality Tools | 标准 Linter 未覆盖的静态仓库检查 | 运行时行为和生产依赖 |

允许的 Rust 依赖方向：

```text
apps -> control/runtime/state/adapters -> ports -> domain
```

`domain` 不依赖其他内部项目。禁止循环依赖。MVP 阶段禁止在 Rust 核心中嵌入
Python；Python 通过 Adapter 边界与核心通信。

### State & Memory Plane — Slice v0.1: Shared Node State

当前已实现的 State Port 为 `SharedNodeStateReader` 和
`SharedNodeStateWriter`。领域对象 `NodeStateSnapshot` 组合
`NodeRegistration` 与带 observation timestamp 的 `NodeStatus`；`core/state` 使用
`BTreeMap` 保存最新已接受事实，并拒绝覆盖更新事实的旧 health observation。

Control 不再私有保存 `NodeRegistration` 或 `NodeStatus`。Registration 和 Heartbeat
通过 Writer 更新 Shared State；Matching、Proposal validation 和 Rebind validation
通过 Reader 读取当前事实。State 不输出最终 `schedulable` 结论：health、freshness
TTL、lease validity 和 requirement eligibility 仍由 Control 判定。

本切片明确不是完整 State & Memory Plane。以下内容延后：

- Allocation State；
- Execution Group State Projection；
- Physical / Spatial State；
- Shared Belief；
- Provenance / uncertainty fusion；
- Distributed Memory；
- Persistence / Replication；
- State Authority resolution；
- Lease ownership resolution。

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
