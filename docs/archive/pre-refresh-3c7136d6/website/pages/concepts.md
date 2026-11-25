# 核心概念与术语

这些对象决定长期连续性、资源所有权和恢复范围。把它们区分清楚，再读[执行链](lifecycle.md)。

| 术语 | 含义 | 不能混同 |
| --- | --- | --- |
| Mission Request | 对话、grounding、草案与提交过程 | 已被 Controller 接纳的 Mission |
| MissionPlan | 目标、Actors、Contexts、Task DAG、约束与满足依据 | 单次模型输出 |
| Actor | 跨 Task 连续的逻辑参与者 | Node、机器人、Host |
| ContextRole | Context 中引用 Actor 的角色 | 单次执行尝试 |
| TaskRole | 引用 ContextRole 并声明 requirements/intent 的执行槽 | Node assignment |
| Execution Group | 长期成员、binding、reservation 与恢复上下文 | Task 或一个进程 |
| Execution slot | `(Group, Task, Role)` 的逻辑位置 | 某个 attempt |
| Attempt / generation | 绑定到具体 Node/session 的一次执行 | Task 的逻辑身份 |
| Node | 执行、能力与存活的接入边界 | PhysicalEntity 或 Host |
| PhysicalEntity | 部署拥有的执行实体 | Mission Actor |
| Capability Requirement | 承担角色需要证明的能力和条件 | ExecutionIntent |
| Operation Support | 支持的 exact canonical operation | Node 私有 workflow |
| ExecutionIntent | 选中后应完成的语义目标 | 厂商 API 参数 |

## 一个跨 Task 的例子

同一个 Actor 在 Task A 生成文件，Task B 处理文件。两个 TaskRole 引用同一 ContextRole。Control 首次 Bind 后保留 Actor 的连续性；普通恢复不能通过挑选另一 Node 偷换物理实体。

Group 持有跨 Task 上下文，各 Task 有自己的尝试与临时资源。A 满足并通过释放门禁后，B 才可按 DAG 前进。B 的 Memory 使用许可不能仅凭 A 曾经 Imported 推导。

## 计划关系与运行关系

DAG 表达完成前置；relation 表达运行期间持续成立的约束。当前 reducer 支持 `requires-active` 和 `shared-spatial-reference`。其他类型即使可以写入合同语法，也会被 implementation profile 拒绝。

耦合模式声明所需机制，不把具体运动算法移入 Controller。原理与代码见[架构手册](docs/architecture/v2/architecture-handbook.md)和[Runtime](modules/runtime.md)。
