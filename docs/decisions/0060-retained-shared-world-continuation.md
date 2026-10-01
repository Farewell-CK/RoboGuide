# ADR-0060: 联合停止后的本地原世界续跑

- Status: Implemented — 本地 opt-in 与离线验证；配套 Controller 恢复见 ADR-0061
- Date: 2026-10-01

## 已确认的边界

原始 EMOS `MultiLLMPolicy.act` 推进所有配置的策略。现有 shared-world 取消在下一次
联合 act/step 之前退出整个循环，并为尚未完成的端生成真实 Cancelled；已完成的结果
保留。当前实现没有独立 Role 停止协议，不能声明 `stop_scope=execution`。

本决策实现部署侧的 retained-world continuation，保持 ADR-0059 的权威边界。它不是
Controller Group recovery，也不赋予适配器重分配、资源释放、自动重试或操作重复授权。
Controller 原有单 Role `/recover` 继续拒绝联合停止部署；独立的 Group command 见
[ADR-0061](0061-confirmed-stop-group-continuation.md)。

## 本地履约

新增默认关闭的 `--retain-stopped-session`。开启后，仅在取消真正停止联合循环、episode
未结束、原始 simulator step budget 尚有余量时，保留当前世界、reset observations 的
后继观察、已有物理效果和完整 accepted-plan session。下一次执行只能来自外部正常
Execute 路径的新 attempt，不会由适配器自行创建。

- 同一 Mission/Group/session digest、同一逻辑 Task/Role、同一物理 endpoint；
- exact operation、objective、parameters 不变；资源选择仍来自外部 Commit；
- 新 attempt identity 非空、未使用，每个世界最多 16 次续跑，不因正常下一 Task 续预算；
- retained profile 最多 32 个 accepted-plan slots，segment history 最多 48 条；
- 每个被取消的端都需新 assignment 才能继续；缺一端有界等待后关闭 session；
- 已 Completed 端保留历史结果，使用原始 wait skill 的 model-free passive policy，不
  重新执行完成的 invocation；仍需执行的端使用原始 CrabAgent、策略与技能；
- 只有正常新 Task 或新 attempt 才重建 Stage2 子任务策略状态，不 reset Habitat、不
  重置 seed/步数或官方评测；Failed、异常、episode done、预算耗尽不可续跑；
- 初始化前建立 run-local 持久化 fencing marker；重启或子进程丢失不能重建 reset
  世界并冒充续跑，必须使用新运行目录开展新的运行。

单 Actor 正常顺序执行保持原路径。开启本功能时，中断的 Task 只允许原 Task 的新
attempt 续跑，不能跳到下一 Task。正常 Completed 之后的 Task 释放和后续 dispatch
仍由 Control 决定。

## 证据与声明

每段保存真实 outcomes、attempt identity、实际 Stage2 assignment、累计步数与停止/结束原因。
已完成端的 Stage2 输入明确记录 passive / Nothing to do，不能误读旧目标文本为再次执行。
暂停快照与最终 terminal snapshot 分离；暂停只 flush，不关闭连续视频。
旧 Cancelled/Completed 不改写；
未结束的停止快照不发布成最终 benchmark summary 或终态 verifier verdict。
最终 summary 使用当前物理终态与官方指标，保留 segment history。
segment/state 归档失败记录日志和有界 failure counts；可用的最终 summary 标记归档不完整，
不会将归档故障改为物理执行失败。官方 benchmark 与 local Completed 始终独立。

默认声明仍为 `execution-group` / `unsupported`；显式开启且配套注册一致时，部署可声明
`execution-group` / `repeat-after-stop`。任何一个版本都不能通过单 Role 恢复检查。
这项本地技术支持不证明 Group 重复操作已获授权。Controller 的显式授权与原资源承诺
复核属于 ADR-0061；本地及 Controller 的离线验证都不能证明真实物理续跑已验证。

## 验证与后续

离线验证必须覆盖真实 policy loop、父子进程消息、HTTP accept/status/cancel、身份与预算
拒绝、Completed 端 passive wait、取消后一次 reset、累计步数、提前结束、I/O 异常、restart
fence。不得用测试 double 宣称真实 Habitat/Provider 已验证。

配套 Controller Group recovery 已由 ADR-0061 单独设计：冻结全部 current attempts、
显式授权其重复、取得全部真实 stop evidence 后由 Control 复核保留的原资源承诺。
不能把本 primitive 接到单 Role `/recover`，也不能因 progress stall 自动 Cancel。
