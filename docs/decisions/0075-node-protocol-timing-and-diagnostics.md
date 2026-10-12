# ADR-0075：Node Protocol Timing and Read-only Diagnostics

- 状态：Accepted
- 日期：2026-10-12
- 范围：启动冻结配置、transport/application 只读诊断；不改变事实接受与 Control 权威

## Context

O1 切片已统一 heartbeat/lease；ADR-0023 的 30 秒 application acceptance 仍为独立常量。
现有 reader 已与 application acceptance 分开，排队、clone、checkpoint 和 SQLite 的成本
尚无当前版本分阶段证据。必须先观测，再决定时间权威或批处理变更。

## Decision

1. `ports::NodeProtocolPolicy` 组合已有 `NodeLeasePolicy` 与正数、可表示的 acceptance timeout。
   部署入口一次解析并冻结，旧构造函数保留 5s/15s/30s 默认。acceptance timeout 不自动
   调整 lease、health/State/peer TTL，也不是物理停止时限。无效或非 UTF-8 配置拒绝启动。
2. Integration 在现有 reader 取得事实时记录同一 process Clock 的原始 receive metadata；
   本 ADR 下只用于观测，application 仍保持其现有权威时间使用。O4 单独决定时间语义。
3. `ports` 提供传输中立、best-effort 的固定维度诊断端口。两级队列报告条数、字节、最老
   年龄及是否可完整观察；bounded bookkeeping 不完整时报告 unknown，不能制造年龄。
4. 应用实现默认关闭的本地 JSONL sink：有界 try-send、固定直方图、有限 session 维度及
   有限文件轮转。控制路径不等待日志 I/O；诊断失败、缓冲满或丢记录不改变事实、事件、
   reservation、调度、completion 或 outbox。诊断不得进入 authority checkpoint 或事件流。
5. 观测 ingress/queue、写门/Controller 锁、clone、reducer、checkpoint bytes/serialization、
   SQLite save/commit、completion/Ack、timer lag、outbox，分别观察 lease/health/transport age。
   sink 不记录完整 payload、模型参数或凭据。指标不证明物理安全或实验成功。
6. `Registered` 和 `Ack` 仍需现有完整 application transition 与 durable checkpoint commit。
   application timeout 的失败类别继续为 DeadlineExceeded，其他 ADR-0023 拒绝语义不变。

## Validation and compatibility

自定义 timing 验证 Welcome/route、注册、renewal 与 restore 新会话；paused-clock 检查
completion timeout；sink enabled/disabled 和故意失败的轨迹比较证明观测不改变 authority。
旧调用入口、Node Protocol v0.4 wire shape 和现有 checkpoint schemas 保持兼容。
测量仅用固定离线/loopback synthetic fixtures，与正式 E6 无关。
