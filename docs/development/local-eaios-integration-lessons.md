# Local EAIOS 接入经验与验收清单

维护日期：2026-10-29。本文从 EMOS/Habitat 接入中提炼可复用的工程经验，
记录确认程度、现有修复和未关闭问题，避免接入其他系统时重复犯错。
本文不保存实验结果、凭据或原始运行证据，也不新增公开协议、准入规则或调度权威。
架构与合同仍以 [V2](../architecture/v2/README.md)、已接受 ADR 和
[设备接入规范](../extensions/device-extension-conformance-v0.2.md)为准。

## 使用方法与责任

每次新接入或调整本地执行系统，先逐条检查本页，再按实际支持的接口执行对应测试。
“不适用”须写明接口与依据；静态配置通过、测试桩通过、实际软件进程通过、仿真通过和
设备通过分别记录，不能互相替代。以下验收项是维护清单，不表示已经提供统一的自动验收工具。

通用层规定命令身份、执行权限和证据消费的不变量；部署侧适配器把这些要求落实到实际系统。
EMOS 的内部字段不能成为其他厂商接口的通用协议；新系统优先使用其正式命令、句柄和状态接口。
本地执行与安全责任仍留在实际 Local EAIOS/设备侧，不由接入文档或模型判断补造。

## INT-L01：新命令不能继承旧命令的完成状态

**确认的问题。** 本地导航可能锁存“这个目标已完成”。再次接纳同目标的新命令时，
旧状态会屏蔽实际执行，同时新的完成观察仍为 false，最终只消耗技能预算。
此前适配器的导航生命周期只覆盖观察采集，搬运路径遗漏了接线。

**通用要求。** 区分原命令重传与新命令。重传保留原句柄与原结果；新命令在 Guard 和
原始映射完成后，精确绑定 endpoint、execution/attempt、invocation、call 与 sequence，
由本地系统支持的方式建立本次命令状态，再根据当前世界重新观察结果。
不得清除 peer 状态、覆盖未知执行、提前写成功或重置整个环境。

**状态：搬运接线已由 ADR-0226 实现并有确定性测试，固定物理回归仍须独立验收。**
[生产绑定](../../integrations/habitat-local-eaios/habitat_local_eaios/live_bindings.py)、
[精确命令生命周期](../../integrations/habitat-local-eaios/habitat_local_eaios/navigation_dispatch.py)、
[现有测试](../../integrations/habitat-local-eaios/tests/test_navigation_dispatch.py)。
其他系统是否存在同类缺陷，需要检查其实际接口，不根据 EMOS 行为推断。

## INT-L02：接收、技能结束和任务满足是不同事实

**确认的问题。** 原始模型工具客户端可以在物理动作执行前返回 `Success`；
技能也可能因预算或高层终止而返回控制。这些回执不能独自证明执行或目标成功。

**通用要求。** 分别保存接收、实际开始、实际技能结果、operation 完成、Task satisfaction
及独立世界目标。完成结果必须绑定本次命令与真实观测；缺失证据保持 unavailable。
超时、cancel ACK 和进程退出不能直接合成为物理停止或成功。

**状态：已有分层与观测反馈实现，新接入仍须验证。**
[执行反馈](../../integrations/habitat-local-eaios/habitat_local_eaios/stage2_feedback.py)、
[本地执行与语义拒绝的边界](../decisions/0093-task-satisfaction-rejection-and-evidence-projection.md)。

## INT-L03：联合世界目标不能悄悄收紧为同机器人条件

**确认的问题。** 团队级目标与单执行者复合条件可能有不同量词和时间范围。
分别要求“存在机器人检测目标”和“存在机器人到达目标”，不自动要求同一个机器人同时做到；
某时刻检测到也不等于最终时刻仍检测到。

**通用要求。** MI 的分解、canonical operation、局部完成与最终 verifier 要明确对象、
执行者范围、时间/新鲜度和联合终态。更强的局部策略不能被当作原任务唯一合法解释。
保留原始任务与真实局部失败，不能拼接跨时刻缓存或以官方 true 覆盖本地 false。

**状态：作用域差异已确认，通用分解/能力表达方案尚待评审。**
[复合采集合同及其范围](../decisions/0223-compound-observation-acquisition.md)、
[官方与本地满足分层](../decisions/0049-authoritative-goal-satisfaction-policy.md)。
有效局部策略的失败不自动构成 Core bug。

## INT-L04：共享传感器或缓存必须绑定当前对象与执行

**确认的问题。** 原始搬运技能可能通过共享目标索引读取另一个物体的距离。
实体序号与模拟器对象 ID 也未必相同；同名目标的旧位置缓存可能已经失效。

**通用要求。** 观察通过实际实体映射绑定对象、目的地、坐标系、世界/episode、
attempt 和采样时刻。对象移动后检查缓存的有效性；不能用另一个执行者的数据替代本次事实。

**状态：精确对象放置绑定已有显式 profile；其他缓存仍按接口分别验证。**
[ADR-0073](../decisions/0073-bound-relocation-place-completion.md)、
[搬运 profile](../extensions/habitat-relocation-profile.md)、
[感知解析](../../integrations/habitat-local-eaios/habitat_local_eaios/perception.py)。

## INT-L05：能力注册必须有实际部署与 readiness 依据

**确认的问题。** 接口或技能存在不证明某台机器人支持该操作，也不证明能到达某一楼层。
抽象能力、实际执行 workflow、当前 readiness 与单个任务可行性是不同层次。

**通用要求。** 注册精确操作、真实能力属性、资源及容量，保留来源、版本与不可用值。
MI 使用允许的抽象规划证据；当前节点资格、资源和 placement 由 Control 判断。
模型不能猜 Node、楼层通行能力、State export 或物理执行者；未知状态不得伪装成空约束。

**状态：已有注册与证据合同，新增设备须独立验证。**
[部署证据解析与消费者边界](deployment-evidence-boundaries.md)、
[Node 合同版本入口](../../contracts/node/README.md)。

## INT-L06：执行环境拓扑不能变成任务语义

**确认的问题。** 共享环境启动屏障、endpoint 容量与单 Actor 顺序复用可能互相等待。
两个目标不自动意味着两个物理执行者；多个任务也不自动构成持续并发协作。

**通用要求。** 明确支持的 endpoint 数量、稀疏分配、连续 Task、并发、资源释放与等待上限。
只从实际 Control assignment 激活执行；适配器不自行运行 DAG 或补造任务。
运行时不支持的拓扑应按现有机制暴露，不通过改写目标或强加 distinct-executor 掩盖。

**状态：已有显式 live profile 与拓扑验证；未来接入不可假定同样支持。**
[ADR-0074](../decisions/0074-independent-live-endpoint-profile.md)、
[计划验证与有界草案反馈](../decisions/0109-published-plan-validation-and-bounded-draft-recovery.md)。

## INT-L07：恢复先区分动作有没有实际执行

**确认的问题。** 零物理步的有限准备失败曾直接终止会话，未进入既有模型反馈。
但一个范围内没有找到视点，不证明世界全局不可达，更不是 Provider 错误。

**通用要求。** 合同拒绝、未执行的准备拒绝、实际技能失败、基础设施错误与结果不确定分别处理。
有界反馈复用原预算；动作可能已生效时不能用新 ID 自动重放。
未知状态和原资源义务保留，模型不得替代真实停止、释放或效果核验。

**状态：ADR-0225 在当前开发分支实现，新的物理验证待完成；旧版本保持原行为。**
[设计与验收边界](../decisions/0225-bounded-preparation-feedback.md)。

## INT-L08：诊断、版本与基线等价性也是接入质量

**确认的问题。** 相同 episode、seed 或模型名不足以证明输入、初始状态、工具、
本地控制与完成条件相同；归档不完整也不能反过来冒充执行组件失败。

**通用要求。** 保留实际代码/模型/profile 身份、初始快照、工具输入输出、真实动作与终态，
显式记录证据缺失及采集故障。诊断应有界且异常隔离，不能增加物理步、改变 RNG 或官方判定。
修改本地 How 时披露版本与影响；冻结批次保留原输入和结果，新修复单独验证，重试不覆盖 primary。
凭据、Authorization header 和敏感环境变量不进入归档或经验文档。

**状态：持续维护要求，不表示任一部署已完成物理或基线等价性验收。**
[诊断实现](../../integrations/habitat-local-eaios/habitat_local_eaios/diagnostics.py)、
[有界观察与证据归档](../extensions/bounded-observation-and-evidence-closure.md)。

## INT-L09：任务实体 ID 必须贯穿场景描述与工具参数

**确认的问题。** 上游公开描述器可能先解析任务别名，再被展示名称覆盖。
模型能读到物体高度，却无法把它可靠关联到任务和工具中的目标 ID。源码来自作者公开分支，
也不证明与论文历史执行版等价；直接回退旧函数还可能破坏普通背景对象的名称兜底。

**通用要求。** 权威实体 ID、显示名、几何和能力引用保留显式对应；不能根据顺序、
名字相似或模型常识补造绑定。修复已知回归时检查原始正确路径和普通对象退化路径。
对比双方使用有记录的同一基础输入修复，声明版本、补丁和实际加载字节，保留原结果。

**状态：ADR-0227 已实现显式源码准备和确定性测试，真实请求及物理验收仍须单独记录。**
[版本化兼容准备](../decisions/0227-explicit-emos-target-identity-compatibility.md)、
[准备工具](../../integrations/habitat-local-eaios/habitat_local_eaios/vendor_compatibility.py)、
[E1 问题登记](e1-baseline-and-execution-issue-register.md)。

## 新接入的可复用验收矩阵

| 类别 | 应验证的场景 | 需要证明的边界 |
| --- | --- | --- |
| 命令身份 | 原请求重传、新命令同目标、异目标、过期/乱序回执 | 不重复效果，不借用旧成功，不跨 attempt |
| 生命周期 | 同 endpoint 连续 Task；同目标重试时仍在原位或已离开 | 新命令实际重新评估；缓存不代表错误世界 |
| 并发与拓扑 | 实际支持的单/多 endpoint、稀疏 dispatch、资源不足 | 不重置 peer、不等待不存在的任务、不越权释放 |
| 目标绑定 | 对象移动、不同对象 ID 映射、局部与团队条件 | 使用当前精确实体及声明的时间/执行者范围 |
| 失败与恢复 | 准备拒绝、技能预算退出、断线、重启、cancel/stop 未确认 | 原效果、Unknown、预算和资源义务不被重写 |
| 结果与证据 | ACK 后失败、本地完成但目标未满足、缺失最终指标 | 各层结果独立，缺证据不制造 true/false |
| 组合路径 | 生产绑定到实际 driver/技能入口；覆盖各声明支持的 operation | 辅助函数通过之外，生产接线也确实执行 |
| 诊断与部署 | 采集异常、输出上限、原 reset 与采样路径、实际解释器 | 证据有界且不改变原任务；软件与物理验收分开 |

## 持续维护规则

新增经验记录稳定 ID、触发条件、确认程度、责任边界、源码/ADR、缺失测试与验收层次。
问题只有在对应代码和测试接入、所需真实验证完成后才能标为关闭；没有物理验证就明确保留。
每次修复评估能否转成通用测试，但不把厂商内部字段升级为公共合同。
架构或协议确需调整时另走 ADR，不在经验记录中直接宣布变更。

原始报告及实验结果继续保存在独立分析目录，本页只保存可复用经验。
[E1 问题登记](e1-baseline-and-execution-issue-register.md)区分当前已确认问题与缺证据事项。
[未来研究想法](future-research-ideas.md)单独维护；当前缺陷修复不启动下一版智能模块。
