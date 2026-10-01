# ADR-0057: 确认停止之后的有界 Role Recovery

- Status: Implemented — 显式授权的停止与 Role 重试；自动策略与 MI 重规划未实现
- Date: 2026-10-01

Runtime Unknown 是物理歧义，不证明旧 execution 停止。Controller 原有 ambiguity driver
直接调用 Control partial release，可能在旧动作仍运行时授权冲突替代。因此恢复先记录
明确的停止请求，沿用 durable Cancel command，等待原 Node 当前 physical attempt 的真实
Cancelled terminal fact。Cancel receipt、HTTP 202、心跳消失、忽略迟到消息都不证明停止。

新增明确 opt-in 的 Controller recovery command。调用者必须绑定 expected owner、声明
允许重复相同 canonical operation/parameters（包括非幂等动作的副作用风险），并提供
停止/恢复时间预算与 Role replacement 上限。没有该授权的 Unknown 保留现有绑定/资源，
仅对账。默认不根据进度观测启动恢复，不调用模型，不改 accepted plan。

Runtime 持久化 cancellation purpose、期限和每个 logical Role 的累计预算。重复命令不续
期限、不增加 attempt。真实停态与当前 Control binding 一致，且预算有效时，才允许
Control partial release -> Match -> Schedule -> Propose -> Commit -> Rebind；没有替代
candidate 保持 pending。新 execution 使用现有新的 physical attempt identity。

原 Matching 是 unavailable-node replacement policy，一律排除原 Node。规范计划的 Actor
同时绑定原 Node，因此直接复用会得到空 Candidate Set。新增显式
`match_stopped_recovery_candidates_for_operation`，只在应用已验证停止/重复授权后使用，
允许原 Node 重新参加现有 eligibility 检查。原 Actor/placement/operation/resource 约束
全部保持，正常 Matching 的“排除旧 Node”默认行为不变。Candidate -> Proposal -> Commit
传递明确的 stopped-owner policy，checkpoint 保留，Rebind 不能根据“Node 相同”猜测授权。
同 Node 重试仍有新 physical attempt identity；释放证明只消费一次。其他 Node 的选择
仍须满足既有 Actor authority，不能通过 recovery 隐式迁移 Actor。

完成与取消竞态中，Completed 继续进入原 satisfaction 路径，不重新做已完成动作。
只有明确 recovery-stop 的 Cancelled 被保留为恢复等待，不被提前归为 Task/Mission 失败。
普通取消、终态 Failed、Mission cancellation 继续各自原流程。过期或缺失停止确认时不
释放旧物理所有权。未受影响 Role、资源、Actor 约束、其他 Mission 与失败历史保持原权威。
恢复预算过期时，Control Abort 该 Role 已 Commit 但未 Rebind 的 replacement，避免无界
占用新资源；Task 仍为可观察的 pending，而不是虚构终态错误。恢复授权之后的原始
terminal Failed 继续按既有失败路径处理；本切片不声称重新打开已经失败的 Mission。

## HTTP 与证据

`POST /v1/executions/{execution_id}/recover` 使用封闭的
`roboguide.execution-recovery-command/v0.1`：`expected_node_id` 是原 owner fence，不是
replacement selector；`repeat_authorized=true` 明确允许重复原 operation/parameters；
`timeout_ms` 为 1..3,600,000，`max_replacements` 为 1..16。恢复授权、durable Cancel intent
和 checkpoint 同一事务保存，提交后才发送 Cancel。HTTP 202 只证明授权持久化。

`GET /v1/executions/{execution_id}/recovery` 返回 v0.1 只读视图，包括原请求/期限、
真实停止 receive time、是否 partial release、abort 与恢复阶段：AwaitingStop、StopConfirmed、
ReplacementPending、BudgetExpired、Aborted、Superseded、OriginalTerminal。普通 `/cancel`
取消替代授权但保留其历史。时间/count 不因重复命令、恢复 checkpoint 或新 attempt 重置。
停止证明来自可信 Node 对本地执行真正结束的报告；恶意/错误 Node 或实际未停止却报告
Cancelled 不由该 schema 自动解决，必须由 Local EAIOS 正确履约。

该切片提供有界、显式授权的执行恢复；没有自动模型重规划。新的 MI 计划仍需独立
deliberation identity、snapshot/history、审查与重新接纳契约，不能覆盖原 plan。

离线验收要求：Cancel admission 不授权 replacement，真实终态才解除 fence；错 owner、
旧 attempt、重复命令、预算耗尽、停止超时、重启、无替代、完成竞态和多 Role 局部恢复。
已通过 Runtime、原始 State/Node reducer 和 production HTTP handler 的确定性检查；
没有调用真实 Provider 或物理环境，真实系统的停止与 observer 准确性仍待受控验证。
