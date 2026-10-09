# RoboGuide AI Collaboration Task Queue

更新日期：2026-10-09（Asia/Shanghai）

本文档是 Codex、ChatGPT 和人工负责人共用的可审计任务队列。队列项只表示工作请求及其状态，不代表实验事实，也不自动授权修改 RoboGuide Core、正式实验配置或实验方案。

## 当前队列

| ID | 来源 | 创建时间 | 目标 | 目标基线 | 状态 | 人工门禁 | 完成证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `SYNC-001` | 当前人工请求 | `2026-10-09T20:12:11+08:00` | 审核双 AI 同步文档、模板和 `AGENTS.md` 规范 | `61ba5b8ae11c8f7701f06ec931505949d970ef61` | `READY_FOR_REVIEW` | 需人工确认后才合入实验分支 | 本文件所在候选提交及 `REVIEW.md` |
| `SYNC-002` | 当前人工请求 | `2026-10-09T20:12:11+08:00` | 实现自动生成、会话导出或 Issue 轮询脚本 | `61ba5b8ae11c8f7701f06ec931505949d970ef61` | `BLOCKED_HUMAN_CONFIRMATION` | 必须在 `SYNC-001` 审核后获得新的明确授权 | 尚无 |
| `SYNC-003` | 已讨论方案 | `2026-10-09T20:12:11+08:00` | 配置私有 GitHub Issue 双向通道 | `61ba5b8ae11c8f7701f06ec931505949d970ef61` | `BLOCKED_EXTERNAL_AUTHORIZATION` | 不得默认使用公开 RoboGuide Issue | 尚无 |

正在进行的 Recovery 实验由现有实验流程管理，不由本队列接管、暂停或重启。

## 状态机

`PROPOSED -> READY_FOR_REVIEW -> APPROVED -> IN_PROGRESS -> COMPLETED`

可从任何未完成状态进入：

- `BLOCKED_HUMAN_CONFIRMATION`：需要新的人工决定或授权；
- `BLOCKED_EXTERNAL_AUTHORIZATION`：需要外部仓库、连接器或权限；
- `STALE`：目标 commit 已变化，原指令不能直接执行；
- `REJECTED`：人工明确拒绝；
- `SUPERSEDED`：被更新的任务或决策替代。

## 新任务模板

```text
ID: SYNC-YYYYMMDD-NNN
Source: human | Codex | ChatGPT | GitHub Issue
Created at: ISO-8601 with timezone
Target commit: exact SHA
Objective: one bounded outcome
Allowed scope: files and systems that may change
Forbidden scope: files and systems that must remain unchanged
Required confirmation: none | human | external authorization
Status: PROPOSED
Evidence required: commands, tests, commit, verdict, or review record
Supersedes: task ID or none
```

## 生成与更新规则

- 每个任务必须有唯一 ID、来源、时间、精确目标 commit 和所需人工门禁。
- 只有人工直接指令或已批准的受控通道可将任务转为 `APPROVED`。AI 的自动回复不构成新授权。
- 执行前重新 fetch 目标分支。如目标 commit 不匹配，标记 `STALE` 并停止，不自动重写指令。
- 完成时必须附上实际 commit、测试结果和必要的实验 verdict。失败或无效运行也必须保留。
- 不从原始会话 JSONL、凭据文件、环境变量或未审核日志自动生成公开队列内容。
