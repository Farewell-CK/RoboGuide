# ADR-0093：Task 语义拒绝与 verifier evidence projection

- 状态：Implemented for the configured final-verdict software profile
- 日期：2026-10-13
- 修订：ADR-0033、ADR-0034、ADR-0048

## 问题

ADR-0033 建立了 `TaskExecutionCompleted` 与 `TaskSatisfied` 的边界。后续 final verifier
ingress 已能提供正、负 verdict，但负 verdict 复用了 `TaskExecutionFailed`，使“本地执行
失败”和“本地执行完成但语义效果未被证明”无法从生命周期事件中区分。Verifier evidence
还只有独立的 State projection 单元测试，Controller 生产路径没有保存该 projection。

## 决策

1. 增加 `TaskExecutionLifecycle::SatisfactionRejected`，表示本地执行已经完成，但 Mission
   声明的 semantic satisfaction 被最终证据拒绝。该终态不替代 `Failed`；`Failed` 继续表示
   执行或恢复策略的不可恢复失败。
2. 增加 `EventPayload::TaskSatisfactionRejected`。负 final verifier 路径先记录
   `TaskVerifierVerdictObserved(satisfied=false)`，再记录该语义拒绝事件；它不得调用
   `TaskExecutionFailed`。Mission 仍可按 final-verdict policy 进入 `Failed` 并释放 Group，
   但此前的 `TaskExecutionCompleted` 事实保留。
3. Controller Bridge 持有 `InMemoryTaskSatisfactionState`。通过 exact
   `(TaskRef, verifier, predicate, source)` key 保存最新证据，并按 RoboGuide receive time
   执行 stale/conflict fence。该 projection 和事件/Orchestration checkpoint 在同一应用
   transaction 中提交；restore 通过同一排序规则重建。
4. `TaskVerifierVerdictObserved` 是 receipt：它保存 Task、source、source revision、artifact
   digest 和 verdict。完整 producer JSON 仍由 deployment-owned artifact 按 digest 保存；
   Controller 事件不伪装成完整的外部 verifier 文档。
5. 新事件使用 EventPayload codec v20，Bridge checkpoint 使用
   `roboguide.controller-checkpoint/v20`。旧 v19 checkpoint 没有 evidence projection 时按
   空 projection 迁移；旧事件 codec 不得声明 v20 semantic rejection。

## 生命周期

```text
Role execution facts all Completed
  -> AwaitingSatisfaction / TaskExecutionCompleted
  -> normalized evidence is recorded in State
  -> positive: TaskSatisfied / Completed
  -> negative: TaskSatisfactionRejected / SatisfactionRejected
  -> Mission policy may fail and release the Group
```

该决策不提供多源 verdict 融合、争议裁决、streaming predicate、远程 verifier authentication
或物理世界真值。Verifier source 仍受 ADR-0048 的 deployment-owned final profile、exact current
attempt、source digest 和 receive-time policy 约束。

## 验证

回归覆盖 positive evidence 的 State checkpoint restore、negative evidence 的独立拒绝事件与
终态、没有 `TaskExecutionFailed` 的反例，以及 v19 event marker 对 v20 rejection 的 codec fence。
现有 stale-attempt、late-verdict、source identity 和 Orchestration lifecycle 检查继续有效。
