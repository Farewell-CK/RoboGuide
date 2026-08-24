# ADR-0003：MissionPlan v0 跨语言合同

- 状态：Proposed for MVP Slice v0.1（面向 MVP Slice v0.1 的提案）
- 日期：2026-08-20
- 范围：Mission Intelligence 到 DEAIOS Rust 应用边界

## 背景

V2 要求 Mission Intelligence 将用户目标转换为 Task Graph 和 Execution
Requirements，但不能拥有节点选择、资源 Commit 或本地设备控制权。ADR-0001
又要求 Python 模型能力与 Rust 核心保持进程和 SDK 隔离，因此需要一份可版本化、
可离线验证的交付合同。

## 决策

首个合同标识为 `roboguide.mission-plan/v0`，Schema 位于
`contracts/mission/v0/mission-plan.schema.json`。合同只包含：

- Mission ID 和原始 Objective；
- 有向无环 Task Graph；
- 每个 Task 的描述、依赖和 Role Requirements；
- Role 所需的 Capability 与可选 Resource Kind。

Integration Contract v0.1 新增并存的 `roboguide.mission-plan/v0.1`，Schema 位于
`contracts/mission/v0.1/mission-plan.schema.json`。它为每个 Role 增加 canonical
`ExecutionIntent`（OperationRef + scalar parameters），用于描述该 Role 执行时的 What。
旧 `v0` 文件继续保留，不静默改变已版本化字段语义。Intent 不得包含具体 Node、Local
Skill、ROS action、vendor SDK method 或 shell command；具体 Local How 仍由 Adapter/EAIOS
翻译和执行。

合同不得包含 Node Assignment、Reservation、Commit、Execution Group、设备轨迹
或厂商 SDK 类型。Python `mission/` 可以使用确定性 Planner、LLM、VLM 或混合
规划器，但输出必须先经过 Schema 和图不变量校验。Rust `core/domain` 再将
Artifact 转换为 `MissionGoal`、`PlannedTask`、`TaskGraph` 和 `MissionPlan`；JSON
与 serde 类型只存在于应用 Adapter 边界。

模型 Provider、模型名、Review 模型、推理强度、网络和存储策略属于外部配置。
Planner 与 Reviewer Prompt 作为 `mission/prompts/` 下的版本化资产管理，不得硬编码
在 Adapter 中。动态 Mission 输入与 Prompt 分离传输。凭据只通过环境变量注入，
不得进入配置、Prompt、Fixture、日志或事件。

## 影响与接受证据

- Controller 不再手工构造 `TaskRequirement`，而是消费已校验的 MissionPlan；
- 核心测试保持离线，外部模型调用属于显式 Adapter/System Check；
- 新合同版本必须并存迁移，不能静默改变 v0 字段语义；
- 接受本 ADR 需要正常计划、未知字段、未知依赖、环依赖、模型拒绝和 Controller
  消费路径的自动化证据，并由项目负责人审阅。
