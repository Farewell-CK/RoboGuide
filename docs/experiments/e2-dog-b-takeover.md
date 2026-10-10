# E2 Dog-B 接替：实施边界与当前进度

Date: 2026-10-09
Status: Core 显式接替 authority 正在实现；应用命令、双 Dog 场景及端到端实验尚未验收。

## 已验证的 Dog-A

`dog-a-priority-2e5b3d6-retry1-20261009T120000Z` 是图环境运行。
代码 `2e5b3d6e6e295e44a84c7343ebcc3a4ab1488427`；模型与 Reviewer 均为
`gpt-6.1-sol`。原始判定成功，13 个图级 primitive、2 个规划段、4 次调用。
这证明同一机器人控制 Node 的重启恢复，不证明硬件故障恢复或替代机器人可用。
原 Node 由故障工具重启，恢复授权由工具显式发送，不宣称 Controller 自行修复机器。

原事件 HTTP 导出只保存默认前 100 条。只读数据库补导出取得 535 条；原记录保留。
真实 Controller 事件序列：285 RuntimeExecutionRecoveryRequired；289
ReconciliationRoleRecoveryRequired；292 RecoveryCandidatesMatched；293
RecoverySchedulingSelected；294 RecoveryAssignmentProposed；295
RecoveryAssignmentCommitted；296 RecoveryRebound。均属于 segment-1/t06/r06。
新增分页导出避免后续运行丢失这部分证据。

## Dog-B 不是增加一个同名能力 Node 就能完成

原任务只有 robot arm 23、robot dog 24、quadrotor 25。
实体注册与 COHERENT 图必须同时存在第二台 Dog，Node 别名不能创造实体。
现有操作 `coherent.agent-24-primitive@v1` 和参数 `agent_id=24` 均指定 Dog-A。
改 Actor 绑定后保持该指令，会继续调用 Dog-A；静默替换参数会改变已接受计划。

`tools/e2-recovery/takeover_preflight.py` 只读检查这些前提。通过仅表示结构无上述
阻碍，不授权恢复、资源释放、Actor 迁移或执行，不调用模型或修改图。

## 后续实现顺序

1. 在独立受控场景初始化两个不同的 Dog，保存场景来源、初始位置、能力与图摘要。
   添加机器人使其成为扩展场景，不能报告为原官方 env4/task17 成绩。
   故障期间不得复制 Dog-A 的位置、持有关系或已完成动作给 Dog-B。
2. 建立可替代的逻辑执行者操作合同：目标对象和动作语义保持明确；由被绑定的
   Node/Local EAIOS 执行自己的实体。Planner 生成此合同，禁止 adapter 改写旧
   `agent-24` 操作。显式指定物理实体的 Actor 不允许被该机制替换。
3. V2 与 ADR-0070 已建立；Core authorization 绑定原 Actor、Role、
   原实体和 registry revision；给出同一所有者优先窗口和替代候选范围。
   授权不能跳过原 execution 的停止证明、能力检查、Context distinctness 或资源 Commit。
4. 原 Node 永久不可达时，当前协议无法取得其 Cancelled 事实。此时必须维持 pending，
   或另行设计可验证的部署 fencing 证明；kill PID 本身不是通用物理停止证明。
5. Control 在 Match/Schedule/Proposal/Commit/Rebind 中持久化授权与 Actor 转移；
   重启、重复请求、过期授权、原节点迟到事实及失败 Commit 均不能产生双重所有者。
   所有受影响的未来 Task/ContextRole 及 checkpoint 校验需保持一致。
6. 先做零模型进程验收，再在同一双 Dog 场景做 F0、Dog-A 恢复、Dog-B 接替对照。
   后续调用默认 `gpt-6.1-sol`；新原始数据保存在独立目录。

尚未发布应用层 takeover API，也未形成 Dog-B 实验结果。本文中的 Core 进度不是已通过的
端到端实验结论。
