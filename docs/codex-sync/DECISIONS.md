# Codex 实验决策记录

更新时间：2026-10-09（Asia/Shanghai）

本文档记录已经明确确定的技术边界，以及仍待人工确认的方案。状态使用 `Accepted`、`Proposed` 或 `Superseded`，避免把讨论中的建议误写成已实施事实。

## D-001：使用仓库内轻量 Markdown 作为跨对话交接机制

- 状态：Accepted
- 决定：使用 `docs/codex-sync/STATUS.md` 记录动态状态，使用 `docs/codex-sync/DECISIONS.md` 记录稳定决策与取舍。
- 原因：另一个 ChatGPT 可以通过 GitHub 阅读经过筛选、可审计的项目上下文，而无需共享私有会话数据库。
- 约束：同步文件只包含适合公开的信息；不得包含 API Key、凭据、私有绝对路径、聊天原文或未经核实的实验结论。
- 维护：每个有意义的代码修改或实验阶段完成后更新 STATUS；技术方向或重要取舍改变时更新 DECISIONS。

## D-002：当前研究目标聚焦 RoboGuide 的故障处理能力

- 状态：Accepted
- 决定：当前主问题是 RoboGuide 能否在执行中的已分配 Node 故障后，安全完成检测、阻塞、候选匹配、调度、资源 Commit、Role Rebind 和继续执行。
- 原因：只比较最终任务成功会混入 LLM 规划质量、备用机器人数量和任务难度等混杂因素。
- 非目标：当前阶段不以证明 RoboGuide 全面优于 PEFA 为主，也不把规划能力、故障恢复和物理控制能力合并成一个结论。

## D-003：执行前 Node loss 与执行后物理歧义必须分开评测

- 状态：Accepted
- 决定：当前 F1 pilot 只在 primitive 尚未进入 COHERENT 前终止绑定 Node。动作已经改变世界但完成事实未返回的 post-effect ambiguity 作为独立实验处理。
- 原因：前者允许安全地由新 Attempt 重试；后者可能已经产生物理副作用，盲目重放会违反安全边界。
- 结论边界：F1 成功不能证明 exactly-once physical action，也不能证明 post-effect reconciliation 已完成。

## D-004：task17 六次 F0/F1 只属于机制 Pilot

- 状态：Accepted
- 决定：`env4/task17` 的 3 次 clean 和 3 次 Node-loss 运行只验证 harness 和 Recovery 链路，不作为跨任务正式统计结果。
- 原因：单任务无法支持跨环境泛化结论；当前 clean 只有 `2/3`，F1 为 `0/3`，恢复链路尚未闭合。
- 下一门槛：先使有效 F1 运行出现完整且可验证的 Recovery 事件链，再扩展任务集。

## D-005：主 Recovery 结论必须同时满足协议链和任务结果

- 状态：Accepted
- 决定：Recovery PASS 不能只依据官方 goal pass；还必须验证旧 Attempt 进入保守状态、受影响 Group 被阻塞、候选匹配、Proposal、Commit、Rebind、新 Attempt、旧 session 隔离以及最终官方目标。
- 原因：最终目标可能通过偶然执行、旧节点迟到动作或 harness 行为达成，不能据此归因给 RoboGuide Recovery。

## D-006：不修改 RoboGuide Core 来制造实验成功

- 状态：Accepted
- 决定：故障注入器、批处理和 verifier 属于实验工具；不能绕过 Controller 或 adapter 执行动作，不能编辑模型动作，不能由 harness 直接选择备用节点。
- 原因：实验要测量现有 RoboGuide authority chain，而不是在实验脚本中重新实现 Recovery。
- 例外：如果真实缺陷需要修复，可以修改产品代码，但必须单独记录设计理由、测试和修复前失败证据，不能把修复伪装成纯实验配置。

## D-007：Node failover 与不同物理机器人接替是两个不同 claim

- 状态：Accepted
- 决定：备用 Node 继续控制同一个 COHERENT agent identity，只能称为 Node failover。要声称 Dog-B 替代 Dog-A，必须有不同 PhysicalEntity、独立位置和状态、明确 Actor/Entity 迁移，并禁止继承 Dog-A 的持物状态。
- 原因：Node 是计算/控制端点，PhysicalEntity 是物理执行主体；混淆二者会夸大实验结论。

## D-008：正式因果矩阵使用相同备用拓扑

- 状态：Proposed，等待人工确认
- 建议：所有主实验组都包含相同的 Dog-A 和 standby，采用 Fault × Recovery 的 2×2 设计：无故障/有故障与关闭/开启 Recovery。
- 主对比：在相同故障和相同 standby 条件下比较 Recovery on 与 Recovery off。
- 原因：避免把“多了一台机器人”的影响错误归因给 Recovery。

## D-009：主 Recovery 实验应冻结计划，在线 LLM 作为扩展变量

- 状态：Proposed，等待人工确认
- 建议：先用同一份经过验证的 MissionPlan 在全部配对条件中运行，Recovery 期间不调用 LLM。端到端在线 `gpt-6-sol`/`gpt-6.1-sol` 规划另做扩展实验。
- 原因：当前 clean 规划存在波动；冻结计划可以把失败归因到 Recovery，而不是模型随机性。
- 公开要求：如果使用模型生成冻结计划，记录模型、配置、原始输出、审查结果和计划哈希，但不记录凭据。

## D-010：任务扩展采用预注册、分层和统一重复

- 状态：Proposed，等待 task17 Recovery 闭环后冻结
- 建议：先做 5 个跨环境 Pilot；通过后从 Recovery-eligible 任务池中预注册 15--20 个任务，每个任务运行 3 次。按环境、GT 长度、Dog 出现阶段和协作类型分层，不根据正式故障结果事后选任务。
- 统计边界：同一任务的重复不是独立任务；正式分析以任务为聚类单位报告配对风险差和置信区间。

## D-011：分支整合策略

- 状态：Accepted，已执行
- 决定：以 GitHub 远程分支为事实来源。显式 fetch 后确认 `origin/codex/e2-coherent-gpt6-sol` 位于 `fda825d`，服务器本地命名分支 `9542057` 是其严格祖先，因此只使用 `git merge --ff-only` 同步，不创建重复 merge commit，也不 force push。
- 结果：本地命名分支和远程分支均指向 `fda825d33cf8882f0018b7b7003f3ce150d7000c`，最新 Recovery 代码和真实失败结果均被保留。
- 原因：先核对远程对象和祖先关系，可以避免覆盖 GitHub 上已有提交，并保证同步文档引用的是远程可访问代码。

## 会话共享安全边界

跨 ChatGPT/Codex 对话共享项目上下文时，优先共享本目录的人工筛选摘要。完整会话原始记录可能包含工具输入输出、路径、环境信息和其他敏感上下文，不应提交到公开 GitHub。需要审计或备份时，应保存在访问受控的本地或加密存储中，并在复制前进行秘密扫描和人工复核。
