# ADR-0054: Mission 恢复分类与提交结果不确定性

- Status: Implemented — 下发前切片；执行期进度与重规划仍为 Proposed
- Date: 2026-10-01

## 背景

“机器收到之前失败”不能单独作为安全重试的依据。Mission Service 的 Controller POST
超时可能发生在接纳和 dispatch 之后。之前 `retry()` 从 `issues` 的文字前缀选择重新 POST，
而失败请求还能通过追加 Dialogue 进入新的规划；这两条路径都缺少可靠的提交边界。

通用恢复流程按阶段、可信证据和责任所有者分层，不按 Episode、机器人型号、任务数量
或日志文字决定策略。现有 MI 草案闭环仍在同一 snapshot 内使用配置有界的 regeneration、
Reviewer 和 Repairer；Provider 认证、网络和配置错误不能进入模型草案修复。

## 本次实现

`roboguide.mission-request-recovery/v0.1` 是持久化恢复决定，绑定 Request、Mission、draft
revision/digest、Grounding digest 和观测时间。阶段、原因和 action 均为 typed code。
Action 由阶段和原因推导，restore 拒绝矛盾 action、错 Request、错 draft/context。
它不获得 Node Inventory、资源 reservation、物理停止或执行恢复 authority。

原有 `roboguide.mission-request/v0.4` HTTP 投影不变。独立 observations 升级为 v0.2，新增
nullable recovery evidence。SQLite 同一事务保存 request/observations；历史 v0.1 observations
和 bare request 继续可读，缺失恢复证据时保守对账，不解析旧错误文本猜测可以再次提交。
B1 读取两版 observations；恢复元数据不替代实际 POST、task registration、verifier 和
官方 benchmark 证据。旧 failure/submission schema 保持不变。

Controller POST 前先持久化 `submission_in_flight` fence。正常异常与进程中断均保留
`reconcile_submission`；缺少 receipt 不能证明未接纳。明确的当前 Controller 400/409/422
拒绝才允许操作者通过原 Request 显式重交原稿，不重新规划，不改变参数、资源或 snapshot。
其它状态、矛盾 receipt、非法响应或传输异常不授予另一个 POST 的权限。

重启前已经原子保存完整、身份一致的真实接纳 receipt 时，启动恢复只归约这份原证据，
完成 Request 的 Accepted 转移，不重新调用模型或 POST。这与从 GET 猜测接纳不同。

同一个 Request 的命令沿用 request-scoped lock：并发 retry 不能在第一条成功接纳后继续
提交。可能已提交的计划不能用消息覆盖；本地 Request cancel 不能表示实际 Mission 已停止。
已接纳/结果不明的 Mission 使用 Controller 的观察、取消与 Runtime 对账边界。

结果不明时，现有 `retry` 入口对原 `mission_id` 做一次有界、只读 Controller GET，保存
immutable lookup observation。不发新 POST，不调用 Interpreter/Planner/Reviewer/Repairer。
每次显式 retry 最多一次 GET，没有后台轮询。查询只记录 bounded id/status，不存整份
Controller response、实时 Node facts 或任意错误文本。

## 当前实现流程

```text
用户任务 -> Interpreter -> Planner -> 确定性校验 -> Reviewer/Repairer -> Approval
                   |         |                |              |
                   |         +-- 结构错误：有界 regeneration --+
                   +-- 事实不足：Clarification；基础设施故障：配置/传输处理

原稿 -> 持久化 submission fence -> Controller POST
         |                             |
         |                             +-- 有效接纳 receipt -> Accepted -> Control/Runtime
         |                             +-- 明确拒绝 -> Blocked -> 显式 retry 原稿
         +-- 崩溃/超时/非法响应/不明状态 -> Failed/Blocked + reconcile_submission
                                               |
                                       GET 原 mission_id（只读）
                                               |
                              found / not_found / unavailable，继续保留 fence
```

## 严格边界与缺口

当前 Controller GET 只提供 Mission identity/status，没有 accepted-plan content digest。
所以 `found` 不补造 receipt、不修改原 HTTP evidence，也不将 Request 变成 Accepted。
查询 `404` 是某一时刻的缺席观察，不能证明尚未完成的旧 POST 以后不会被接纳。
完整自动接纳恢复需要另一个经过版本化设计的权威、digest-bound 接纳查询；不能直接放宽
B1 provenance 来达到自动通过。

typed state 与哈希防止矛盾和跨 snapshot 使用，不是可信 SQLite 或全部源证据遭替换时的
密码学认证。原始失败记录在对账中保持不变；lookup 只保留最近一次，只读调用不累计无界历史。
显式人工重新 deliberation 沿用既有预算语义，不存在自动创建新 Request 来绕过预算的流程。
同一 Dialogue 的显式 retry 复用已有冻结 Grounding，而不是重新 capture 后保留旧 review。
明确的用户澄清开始新 Dialogue/snapshot，当前 review history 属于新周期；旧周期的完整
review、失败与提交记录由 SQLite immutable history 保留，不重写历史 digest。
手动 retry 是操作者完成配置修复后的恢复命令，也允许认证/配置失败继续原请求；它不提供
自动认证重试。408、429、5xx 被标记为 infrastructure transient，不进入草案 regeneration。
POST 前清空当前回执；旧拒绝留在历史中，不能在新 POST 崩溃后冒充本次拒绝。
回执和最终状态同一事务保存；兼容旧版本 receipt/state 写入窗口中的明确拒绝恢复。
真实模型将来是否稳定修复任务语义，不能由 deterministic tests 宣称已证明。

## 后续通用执行恢复

下一切片先设计 operation-specific、attempt/owner/freshness-bound 的只读进度证据，区分
正常等待与停滞。之后按 Local How -> Runtime/Control -> MI 升级：局部重试保持原 canonical
目标；新 attempt 需要可信停止或真实动作 fencing；新的组织方案需要新 revision/snapshot、
完整审查与 Control 接纳。Cancel receipt、忽略迟到事实或心跳均不证明物理停止。
当前 Task terminal failure 仍按原规则处理，未实现通用停滞恢复或执行期 MI replanning。

公共 Node progress、停止证明与计划替换机制须先更新相应 ADR/版本，不能把 Habitat 日志
或 adapter JSON 直接提升为 Core 的重试协议。计划见
[`recovery-improvement-plan.md`](../development/recovery-improvement-plan.md)。

## 验证

离线回归覆盖真实 HTTP 非法 receipt、404 后迟到接纳、查询失败或错 identity、提交前
持久化 fence、进程中断、并发 retry、明确拒绝下原稿重交、Dialogue/cancel 边界、旧记录
迁移、错误文本伪装、跨 draft/context、typed Provider fault 与原 B1 fail-closed 规则。
没有调用真实模型、启动模拟器或改变官方评测与 Formal admission。
