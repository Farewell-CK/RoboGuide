# ADR-0079：Runtime Task Contract Isolation

- 状态：Implemented; production application path checked with synthetic Node facts
- 日期：2026-10-12
- 范围：声明 completion deadline 的 terminal-outcome 接纳；不提供物理停止或硬实时保证

## Decision

1. ADR-0077 的持久 hold 需要真实、可定位的生产错误来源。第一版采用已经在 accepted
   Task timing 中声明的 completion deadline：所有当前 Role 的 successful terminal facts
   的首次 RoboGuide received_at 必须不晚于 acceptance + deadline。该时刻是 Controller
   观察边界，不是 Node 时钟或物理完成时刻；排队处理时间和重复 snapshot 不重写它。
2. Runtime 保存每个物理 attempt 的首次 terminal received_at，随自己的 checkpoint
   持久化。Integration bridge 只传递原 ingress time，不建立第二 execution map。
   无 source time 的历史 terminal evidence 保持 unavailable；restart 不能补成 fresh。
3. Orchestration 对确切 accepted Task 检查上述契约，产生 typed、TaskRef-bound
   `RuntimeContractViolation`。Application 只在 TaskOutcome 边界且 Mission/Task 对应、
   baseline Control/ownership/accepted-plan 验证完整时升为 hold。candidate 和 event
   savepoint 先完整回滚，再提交 typed contract evidence 与 isolation。
4. 逾期、历史 receive-time 不可用或时间与 Mission acceptance 不一致时，仅该 Mission
   暂停派生 outcome/DAG/新 Execute；raw Completed 和 ownership 保留。未声明 deadline
   的 Task 保持既有路径，Failed/Unknown 不转换为时间违约或物理成功。
5. 显式 resume 必须重新检查同一不可变事实。未解决的 deadline violation 返回 conflict
   并保留原 hold，不刷新 deadline 或重新执行。显式 cancellation 仍遵守完整 terminal
   proof 后 release 的规则；重写已接受 Mission 的时间契约需要独立 plan-revision 协议。
6. Controller inner checkpoint 升为 v19，server wrapper 升为 v23；接受无新字段的历史
   v18/v22，旧 marker 不能携带新 Runtime timing authority。事件 codec v15 记录 typed
   runtime contract rejection，同时保留旧事件读取。恢复仍 Unknown/no-blind-replay。

## Validation

真实 Node-fact/application timer 路径验证：合法 accepted A 在 deadline 后首次收到
Completed，raw evidence 被接纳而 A 被持久隔离；B 正常完成，A resources 保留。
提前收到但排队至 deadline 后处理的 Completed 正常接纳；terminal replay/restore 不改
首次 receive time；缺少历史 timing evidence 不发明时间。错误 TaskRef、Control/shared
invariant、SQL/savepoint/checkpoint/Commit 失败仍 fail closed，没有 durable acceptance
就不能 ACK。此软件证据不证明物理 deadline、安全停止或正式 E6 行为。
