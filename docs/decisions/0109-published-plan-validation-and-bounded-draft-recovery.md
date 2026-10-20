# ADR-0109：发布合同前检与有界草案恢复

原独立分支编号为 ADR-0106；统一编号与合同接线见 [ADR-0111](0111-branch-integration-and-authority-version-fences.md)。

- 状态：已实现；新部署的真实物理验收仍需独立证据
- 日期：2026-10-15

## 问题

计划通过 MI 的结构、能力和满意条件校验，仍可能不满足部署的执行生命周期。
在 `after-all-session-slots` 发布 verdict、`after-task-satisfaction` 释放资源的部署中，
提前等待联合终态的 Task 可能阻塞产生该终态所必需的后续工作。仅增加 DAG 边不能解决。
此前恢复只接收第一条校验异常，也丢失了能够独立确定的其他问题。

## 决策

1. Controller 提供只读 `POST /v1/missions/validate-plan`，返回
   `roboguide.plan-validation/v0.1`。结果绑定实际请求体摘要、Mission、verifier source
   和部署摘要。发布合同检查与真实提交复用相同应用校验。它不创建 Mission/Group、
   不写事件/checkpoint、不执行 Matching/Commit/Bind，不证明当前资源可用或物理可达。
2. 显式配置 execution-planning evidence 的 MI 组合根，在 Planner、regenerate、
   Repairer 和部署 reconsideration 的新草案归一化及校验中调用这一端口。缺失接口、
   HTTP/身份/来源不匹配是系统或配置失败，不能变成模型草案错误并消耗恢复预算。
   冻结 planning evidence 与 grounding world 的交叉绑定在调用模型前检查；来源不匹配
   不产生新的 Planner/Repairer 请求。
   已知合同冲突保留原始输出和结构化错误，使用既有有界草案恢复；不增加调用预算。
3. 反馈只包含逻辑字段路径、稳定错误 code、消息及冻结来源；不包含 Node/Resource
   候选列表。Actor 引用等可安全独立检查的错误有数量和大小边界，不能将未能解析的
   草案用作合法 MissionPlan。历史 rejected-draft v0.1–v0.3 保持可读。
   v0.4 观察专门保留 Controller 校验不可用时已经返回的草案，标记系统失败并禁止自动
   regenerate；它不宣称模型草案已被证明无效，不改变公共 Mission Request schema。
4. 普通 Task 可在 Control 实际释放后复用 endpoint。终态 barrier 限制只适用于声明该
   生命周期的源。通用按 Task 发布的 verifier、合法单 Actor 顺序工作、多 Actor 并发、
   真正需要 shared_view/relation 的任务保持各自合同。禁止自动改变 Actor 拓扑、
   削弱 satisfaction、编造物理实体、状态 export 或执行关系。
5. 前检通过只是已检查范围内没有发现冲突。提交仍重跑原有权威门禁，身份、tenant、
   当前 State、资源和恢复检查不受前检授权。已尝试提交后的不确定结果仍只读对账，
   不通过 regenerate 发起第二次提交。
6. final-feed 的 Actor 复用检查依据实际 Task/Role 引用，不能由声明一个未使用的 Actor
   绕过。已有的不可执行复用可能因此更早在 source gate 返回 422，而不再等待部署 gate
   的 409；两者都保留确定拒绝、零承诺的含义。通用独立 verifier 不使用此终态规则。

## v0.2：显式拓扑与具体恢复反馈

2026-10-20 扩展发布合同为 `execution-planning-evidence/v0.2` 与
`plan-validation/v0.2`：在同一冻结来源下增加 `execution_topology`，区分支持的 effective
coupling modes、DAG、Context relations 与 Actor binding lifetime。旧 v0.1 保留可读，
缺失字段不被推断为新能力，旧回复不能代替配置要求的 v0.2 拓扑证明。

Controller 将模式错误指向实际 Context/Task 的 `coupling_mode`，与资源存活期、意图和
能力错误分开；不再用 endpoint 不足替代已知的模式原因。候选检查仍在 application，
真实 Matching/Commit/Bind/release 权威不变。错误信息不包含 Node/Resource 候选。

通用规划/审查/恢复指导区分终态事实、Task/Actor 边界、本地前置工作与完整终局验证。
ordinary DAG 不自动要求 handoff；检查 P 不等于使 P 成立；不同时间的观测不能合成
同时成立的目标。实际需要合作或状态共享时仍保留需求并暴露部署缺口。

字段、兼容性与共享的合成回归见
[执行规划合同 v0.2](../../contracts/mission/execution-planning-v0.2/README.md)。

## 边界与验收

不改变 MissionPlan v0.8、Node Protocol、Core 的承诺/释放权威、正式样本准入、
官方 benchmark 判定、原始 Stage2、任何 episode 或模型超时/恢复预算。
零 Provider 的 Rust/Python 回归检查前检与提交一致、原始输出和多个错误完整保留、
失败不产生权威副作用、旧导航及合法拓扑不回退。测试不能证明真实模型稳定遵循规则。
真实实验使用新目录、冻结代码和配置，实验结果留在本机，不提交历史运行证据。
