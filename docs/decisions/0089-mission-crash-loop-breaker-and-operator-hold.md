# ADR-0089：Mission-local typed failure与 crash-loop breaker

- 状态：Implemented for the accepted-plan local contract slice
- 日期：2026-10-13

## 决策

Orchestration 对已接受 Mission 的 `InvalidContract` 与 exact Task
`RuntimeContractViolation` 记录 typed failure class、稳定 fingerprint、时间窗口和重复次数。
同一 fingerprint 在十分钟内连续三次触发 Mission-local quarantine。Hold 保留原 Group、
binding、resources 和 raw evidence；`dispatchable_tasks` 对 quarantine 直接返回空，因此
一个 Mission 的故障不会停止其他 Mission，也不会自动释放资源、替换 Node 或重复 Execute。

`MissionFailureState` 与 Hold 一起进入 orchestration checkpoint。普通 generation-bound resume
在 quarantine 时拒绝；`POST /v1/missions/{id}/review` 要求 `recovery-admin`，携带
`isolation_generation` 和 bounded reason，并在当前 Control/State/registry/attempt 重新核验后
清除 breaker、提交 `MissionExecutionQuarantineCleared` audit event，再继续原有 resume 流程。
`GET /v1/missions/{id}/recovery` 暴露 review deadline、failure evidence、ownership 保留和
自动动作禁用状态。

Operator HTTP 可配置 mTLS client certificate 到角色的 digest map；没有该配置时只允许
loopback 开发模式。mTLS 证书只建立连接身份，角色映射仍是独立的 deployment policy。

## 边界

该 breaker 不把未知 Runtime、Control、SQLite 或共享状态错误降级为 Mission-local；这些
错误继续 fail closed。它也不证明物理停止、资源安全释放、语义满足或硬实时恢复。
