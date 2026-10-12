# ADR-0077：Mission Execution Isolation

- 状态：Accepted
- 日期：2026-10-12
- 范围：可证明局部的派生 transition、事务回滚和显式恢复；保留全局 fail-stop

## Decision

1. `MissionExecution` 保存正交的 typed execution isolation：generation、source stage、
   isolated_at，分类固定为契约拒绝。原 Mission lifecycle/Group/Actor/reservation/Runtime
   authority 保留，不把隔离当 Failed、Cancelled 或物理停止。
2. Ready dispatch 和 terminal-outcome 的每个精确 Mission transition 在独立 candidate 与
   现有 SQLite batch 的内部 event savepoint 中运行。保存事件 sequence counter；失败时
   同时回滚 SQL/sequence 和丢弃内存 candidate。不能提交部分 mutation 后补 quarantine。
3. 第一版仅允许**已经定位到单 Mission、可信 baseline 通过 ownership/accepted-plan
   authority 校验**的 typed `OrchestrationError::InvalidContract` 升为 isolation。
   这些回调处理该 Mission 的完整 accepted plan，不处理跨 Mission 的 NodeUnavailable。
   `Mission(String)`、`Control`（含 InvalidProposal/InvalidLifecycle）、未知 error 和
   EventSink/SQL 错误保持 global fail closed。不会用诊断文本判定，也不扩大 input rejection。
4. 隔离可暂停后续 dispatch/outcome/recovery 推进，但 raw Runtime、Node health、State、
   receipt、迟到事实和显式取消继续保留。该 Mission 的 Control ownership 不因隔离释放。
   NodeUnavailable 仍可影响多个 Mission 并建立其原 physical ambiguity fences。
   整组 continuation 在 preparation 前也必须检查 hold；先前授权及完整 stop facts
   不能绕过隔离创建 replacement。显式 resume 不刷新原 Group recovery deadline。
5. 未完成 work 从 accepted plan 的 Ready state 或 durable Runtime terminal attempts
   重建，isolation source stage 持久保留；不把 ephemeral queue 作为恢复 authority。
   其他未分类 lifecycle failure 不拆成第二事务，也不改变 ADR-0023 的失败语义。
6. GET Mission status 附带 isolation。POST `/v1/missions/{id}/resume` 必须提交当前
   `isolation_generation`，在统一 writer gate 内重验 accepted plan、完整 ownership、
   Actor/registry、当前 bound Node 注册/lease/health/operation 与 current-attempt identity。
   同一 candidate 再推进；仍被隔离或任何拒绝，完整回滚并返回 409。成功才 checkpoint/
   commit、清除 hold、fresh-clock flush outbox。不会 regeneration/replay/renew budget。
7. 显式取消保持原 draining/terminal-stop 语义。隔离不是 continuation/retry authorization；
   任意 Unknown、Cancel receipt、lease 超时都不授权 release 或 new physical attempt。
8. server wrapper 升为 v22 防止旧 binary 忽略 isolation 后继续执行；旧 v16–v21 无 hold
   输入仍兼容，旧 schema 携带新 isolation data 拒绝。event codec v14 加入 isolate/resume。

## Validation

测试精确 A 隔离/B 继续、candidate 部分 mutation 回滚、event sequence 回滚、Context/
pending ownership 保留、raw terminal evidence 保留、checkpoint restart、generation 重放、
过期 State/operation/registry 重验、同一 Node 多 Mission ambiguity 及取消。失败 scope
不可证明、Control invariant、SQLite/savepoint/commit 或 poisoned lock 仍返回全局失败，
没有 durable checkpoint 就不能 ACK。第一版的局部分类刻意狭窄，不宣称所有 fatal 已消除。

第一版验收时运行期自动分类仍未验收：生产 InvalidContract 来源是 decode/implementation
preflight，两个 accepted-plan dispatch/outcome 回调没有已验证的该类型错误来源。
局部错误的 savepoint 测试人工注入 typed error，只证明回滚/hold 基础机制。
生产 HTTP 测试确认非法 JSON/不支持的契约分别在 admission 返回 400/409，内存、
事件、checkpoint 均不变，已有 Mission 仍由 timer 正常 dispatch，不制造 isolation。
新增运行期分类必须先证明实际单 Mission 错误来源及共享权威完整性，不能扩大捕获
全部 Control/Mission/SQL 错误来满足一个测试。

2026-10-12 后续软件闭环见 ADR-0079：声明 completion deadline 的 accepted Task
现在有真实生产 typed violation 来源。Runtime 保留首次 terminal receive time，
Orchestration 检查 exact Task，生产 timer 回归验证 A hold/B 完成、ownership 保留、
排队与重放不误判、历史缺失不补时间、resume 409 与实际 SQL 故障全事务回滚。
上段描述保留第一版验收限制；当前已覆盖这一明确的运行期子集，其他错误仍不推定局部。

整组恢复回归验证授权后隔离、迟到 Cancelled、重复 timer、generation 拒绝、durable
restore 后重新登记、显式 resume 和原预算到期后的禁止 continuation；资源和 stop
facts 始终保留。测试使用合成 Node facts，不表示物理停止已被硬件验证。
