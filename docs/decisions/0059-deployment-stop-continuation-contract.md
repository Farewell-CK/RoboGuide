# ADR-0059: 部署停止范围与原上下文重执行契约

- Status: Implemented — 声明与恢复安全检查；shared-world 独立停止/续跑仍不支持
- Date: 2026-10-01

## 问题与事实

ADR-0057 的显式 repeat authorization、真实 Cancelled 和恢复预算是必要条件，但不足以
证明一个 Role 可以独立恢复。部署还必须保证取消该 invocation 不会终止其他 execution，
并能在保留所需上下文和既有物理效果的前提下执行原 operation/parameters。

当前 Habitat shared-world 的 `_execute_pair` 聚合任一 endpoint 的取消请求，原始联合
`_pair_loop` 因此结束；之前已观测的 Completed 保留，其余 endpoint 返回 Cancelled。
coordinator 已消费该 episode，不支持重新派发被中断的 session。单 Actor 顺序执行则
允许正常完成后由 Control 释放 Task、派发下一 Task；这不证明取消后的重试受支持。
单独 Habitat backend 的下一 invocation 会 reset，也不能声明保留原世界的恢复能力。

## 声明与权威

复用 Node Protocol v0.4 / Node Contract v0.6 的 LocalSystem metadata，增加封闭版本化
`roboguide.execution-recovery`，值为
[`roboguide.local-execution-recovery/v0.1`](../../contracts/node/execution-recovery-v0.1/README.md)。
每条声明绑定其 LocalSystem 实际拥有的精确 canonical operation：

| 字段 | 含义 |
| --- | --- |
| `stop_scope=execution` | Cancel 只终止被指定的物理 execution |
| `stop_scope=execution-group` | Cancel 可终止同 Group 其他 execution |
| `stop_scope=unsupported` | 无可信停止履约声明 |
| `continuation=repeat-after-stop` | 停止后可重执行原 invocation，保留所需上下文及既有效果 |
| `continuation=unsupported` | 不提供上述恢复能力；正常下一 Task 能力独立 |

最多 8192 bytes、1..32 个不重复 operation，拒绝未知版本、字段、枚举、非法 identity 和
错 operation owner。Node v0.7 配置在打开连接之前校验；Controller registration 也独立
校验。缺失不等于支持；旧配置继续可用于正常执行，但不能据此授权单 Role 恢复。

这是一项可信部署事实声明，不是资源承诺、物理停止证明或重复操作授权。任意 vendor
是否真实履约仍须独立验证。MI、MissionPlan、State progress 和官方 benchmark 均不读取
该声明以修改任务语义，Core 没有 Habitat 或 episode 特例。

## 安全检查链路

Runtime 在 dispatch 时冻结 exact operation/LocalSystem 声明。单 Role `/recover` 同时要求：

1. 原 attempt 声明 `execution` 与 `repeat-after-stop`，当前注册仍与其完全相同；
2. 调用者显式允许重复原 operation/parameters，提供原 owner 及有界次数/时间预算；
3. 原 Node 当前 attempt 的真实 Cancelled，在预算内到达；
4. Control 部分释放、Matching、Schedule、Proposal、Commit、stateful Rebind。

注册变化时，恢复 Cancel delivery、部分释放及后续恢复不继续执行。replacement candidates
必须独立声明支持，Commit 和 Rebind 重新检查；state-free Rebind 不允许这种恢复。
已经 prepare 的 replacement Execute 在 outbox 真正发送前也检查其冻结声明是否仍有效。
无法发送时保留 outbox 和现有所有权，不把这种状态归为物理成功或本地执行失败。
已 Commit 但未 Rebind 的失效 replacement 由 Control Abort，保留 pending 与历史。

legacy attempt 不因节点后来发布支持而追溯获得许可。旧 checkpoint 中已有 recovery stop
保留原预算和证据，但没有原声明就不发送恢复 Cancel、不释放、不自动重试。显式普通
Cancel 仍可取消 Mission/执行，且保留实际部署的联合取消效果；它不是独立恢复命令。

## 接口、迁移与限制

恢复命令仍为 v0.1，只读 recovery view 升至 v0.2，记录 original/current 声明及
`NotDeclared`、`InvalidDeclaration`、`StopNotIsolated`、`ContinuationUnsupported`、
`RegistrationChanged`、`Supported`。Support 与恢复 phase 独立，`Supported` 不授权动作。
Controller checkpoint 内层 v17 / 外层 v21 防止旧二进制忽略新增安全检查；兼容历史输入
时不补造支持、不续预算、不重写原失败。

Habitat Node 模板与只读 `GET /v1/executions/recovery-support` 一致声明
`execution-group` / `unsupported`。读取不调用健康检查、模型、仿真、RNG 或执行控制。
双 Actor 并发与单 Actor 正常顺序路径保留。当前 shared-world `/recover` 在发出 Cancel
之前被拒绝；不声称已实现独立 endpoint 停止、Group 重启或中断后的原世界续跑。

下一阶段若要恢复该部署，应先明确原 Stage2/local skill 的停止协议、所有受影响 execution
的真实终态，以及 retained world/session 的 continuation admission，再设计对应 Group
恢复；不能假报 `execution`、重建 reset 世界或把新的 Task 当作旧 attempt 续跑。

离线验收覆盖：缺失/耦合声明、错 owner/operation、旧 checkpoint、注册变化、Cancel与
partial release 隔离、Matching/Commit/Rebind/outbox 再检查、既有正常顺序执行、真实
联合 policy loop 的取消和 Completed 保留。真实物理停止与自动恢复策略尚未验证/启用。
