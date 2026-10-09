# RoboGuide AI Collaboration Review

更新日期：2026-10-09（Asia/Shanghai）

本文档是双 AI 同步变更的人工审核门禁。它记录候选包的范围、证据、风险和最终人工决定，不作为实验 verdict。

## 当前审核包

- Review ID：`SYNC-FOUNDATION-001`
- 状态：`PENDING_HUMAN_REVIEW`
- 目标分支：`codex/e2-coherent-gpt6-sol`
- 基线 commit：`61ba5b8ae11c8f7701f06ec931505949d970ef61`
- 候选分支：`codex/dual-ai-sync-foundation`
- 允许范围：`AGENTS.md` 和 `docs/codex-sync/*.md`
- 排除范围：RoboGuide Core、Recovery harness、实验配置、实验结果、原始会话、自动化脚本、hook、定时器和 Issue 轮询。

### 候选文件

- `AGENTS.md`
- `docs/codex-sync/EXPERIMENT_CONTEXT.md`
- `docs/codex-sync/TASK_QUEUE.md`
- `docs/codex-sync/REVIEW.md`
- `docs/codex-sync/SYNC_PROTOCOL.md`
- `docs/codex-sync/LIVE_STATUS.md`（保留既有快照）
- `docs/codex-sync/STATUS.md`（只追加本阶段的真实状态）

## 机器检查

提交前在 `STATUS.md` 记录以下检查的实际结果：

- `git diff --check`；
- 变更路径白名单；
- 凭据形态与私有绝对路径扫描；
- 新文档标题、相对链接和必填字段检查；
- 确认没有 Recovery/Core/配置/结果文件变更；
- 确认实验主工作树和未跟踪 TOML 未被修改。

## 人工审核清单

- [ ] 文档中的研究目标与当前 Recovery 实验一致。
- [ ] Recovery Protocol 和 Task Success 没有混为一个结果。
- [ ] 队列项不会绕过人工授权修改 Core、配置或正式实验方案。
- [ ] 未包含原始 Codex JSONL、聊天原文、token、密码、环境变量或未审核工具输出。
- [ ] 同步流程不会在实验计时区间运行。
- [ ] 同步文档与实验分支的整合方式是非强制、可审计的。

## 人工决定模板

```text
Review ID:
Reviewer:
Reviewed at:
Target commit:
Decision: APPROVED | CHANGES_REQUESTED | REJECTED
Approved scope:
Required changes:
Evidence checked:
Notes:
```

只有明确的 `APPROVED` 才能解锁 `TASK_QUEUE.md` 中依赖该审核的后续任务。对本文档的 AI 自动修改不等于人工批准。
