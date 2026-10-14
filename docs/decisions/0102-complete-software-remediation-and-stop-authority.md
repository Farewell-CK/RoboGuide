# ADR-0102：全部非发布软件整改与停止权威接入

状态：Accepted。日期：2026-10-15。实现状态：进行中。

用户批准执行全部非发布整改，包含原 R20 后置软件能力。实现与验收以
[完整实施计划](../development/roboguide-software-completion-plan-2026-10-15.md)为准。
没有设备时交付通用软件，设备事实保持 unknown；不增加固定机器人或预写成功场景。

## 停止合同

Application 将 typed `SafetyStopRecord` 与 exact Group/Task/Role/attempt/generation、原始
controller epoch、operation-owned State export/source/session 绑定。受影响 Group 的完整
current attempt 集合建立 Orchestration hold；该 hold 不建立新的资源/执行权威，也不计作
重复 contract failure。Cancel 的 durable outbox 可越过 hold，新的 Execute/派生工作不可越过。

Node durable command receipt 只推进接纳阶段。安全状态必须来自冻结的 operation-owned export，
核对 record 自身 source epoch、来源、schema、TTL、停止请求后的 receive time 和 conflict policy。
Runtime 原始 terminal receive time 与新鲜 safe-state 分别保存。缺失/冲突/Unknown 均保留 ownership。
deadline 由 application timer 驱动，只升级未确认的记录，不回退已确认状态。

Mission 取消、普通失败、satisfaction、resume、recovery、repeat 和 takeover 均消费同一门禁。
自然 Completed/Failed 保留原事实，不替代停止确认。无法确认的 stop 形成 durable manual escalation，
不能因路由/session 缺失直接抛出共享错误停掉其他 Mission。

## 迁移

Controller/supervisor 共享当前 wrapper schema。旧 stop purpose 缺少 frozen descriptor/receipt/
完整证据时不得继续使用旧的字符串 `stop_confirmed` 授权 release，迁移为待人工核对。
checkpoint、codec 和 Node extension 每次演进都有显式迁移与旧版本 fence。

## 验收

必须有真实 application 路径的 stop、timeout、旧 session、stale/request-before evidence、
completion/cancel/failure、resume/restore、全释放门禁和多 Mission 隔离测试。协议测试证据只
证明软件边界，不证明设备安全。现有 HA 版本接线还必须通过实际 replica/Controller 进程检查。
