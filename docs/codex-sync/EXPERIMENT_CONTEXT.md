# RoboGuide Experiment Context

更新日期：2026-10-09（Asia/Shanghai）

本文档是 Codex 与 ChatGPT 共用的稳定实验上下文，用于说明研究问题、术语、能力边界和证据规则。它不替代 `STATUS.md` 的动态进度、`LIVE_STATUS.md` 的带时间戳快照或 `DECISIONS.md` 的决策记录。

## 当前研究问题

RoboGuide 能否在执行中的已分配 Node 丢失后，保守地标记物理执行歧义，等待可信停止事实，保留未受影响的任务上下文，经由 `Match -> Schedule -> Propose -> Commit -> Rebind` 形成新 Attempt，并继续执行至官方 COHERENT 目标成立。

当前阶段仅覆盖单 Node、pre-effect、confirmed-stop 的 same-owner recovery。它不自动支持以下结论：

- 备用 Node 已被选择并接管；
- 不同 PhysicalEntity 的 Dog-B 已替换 Dog-A；
- post-effect 物理歧义已解决；
- 动作具有 exactly-once 保证；
- 结果可泛化到全部 COHERENT 任务。

## 关键术语

- **Node**：计算和控制端点；Node failover 不等于物理机器人替换。
- **PhysicalEntity**：COHERENT 世界中具有独立位置和状态的物理执行主体。
- **Recovery Protocol**：故障后从保守阻塞、停止确认到 Commit/Rebind 和新 Attempt 执行的协议链。
- **Task Success**：只由官方 COHERENT 目标判定；不能用 Recovery Protocol PASS 代替。
- **有效运行**：故障注入和基础设施条件满足预注册要求的运行。无效注入必须单独标记，不能归入 Recovery 成功或失败。

## 已核实的实验边界

- `env4/task17` 历史 Pilot 和后续单次运行的具体结果以 `STATUS.md` 为准。
- Recovery Protocol 和 Task Success 是两个独立结果，必须分别报告。
- 正在进行、未产生最终 verdict 的运行不能写成已完成事实。
- 实验目录名、聊天陈述或手工文字不能覆盖机器 verdict 和原始证据。

## 事实来源优先级

1. Git commit、官方任务输入、机器 verdict、校验和和受控实验证据。
2. `STATUS.md` 中已引用上述证据的动态摘要。
3. `DECISIONS.md` 中标为 `Accepted` 的决策。
4. `LIVE_STATUS.md` 的带时间戳快照，仅代表生成时刻的状态。
5. `TASK_QUEUE.md` 和对话内指令；它们可以提出工作，但不能创造实验事实。

来源冲突时必须暂停结论更新，在 `REVIEW.md` 记录冲突和需要的人工裁决。

## 生成与更新规则

- 只在研究问题、术语、实验边界或证据优先级发生变化时更新本文档。
- 从 `STATUS.md` 和 `DECISIONS.md` 提取稳定上下文，不复制聊天原文、工具原始输出或完整日志。
- 任何新的实验结论先进入 `STATUS.md` 和 `REVIEW.md`；人工确认为稳定边界后才同步到本文档。
- 不包含凭据、token、密码、环境变量、私有绝对路径或未脱敏的第三方内容。
