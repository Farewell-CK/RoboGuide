# ADR-0082：多 Mission 准入、优先级与公平 dispatch

- 状态：Implemented; deterministic Control competition and production HTTP/SQL paths checked
- 日期：2026-10-12

## Decision

Orchestration 增加 logical Ready-Task dispatch policy，持久保存每个 Mission 的 priority、
last service ordinal 和每个 Ready Task 的首次排队 ordinal。ordinal 由已保存的最大 service
ordinal 推进，不使用 Node 时钟、不在 restore 后刷新等待年龄。priority 为 0..255，
默认 128；等待每 8 次 logical service 增加一级、最多增加 512。同等有效 priority
使用旧 service ordinal、稳定 Mission/plan 顺序打破平局。每轮一个 Mission 最多选择
一个 Task，轮内其他 Mission 获得机会后才开启下一轮；每个 input/timer 最多 32 次
logical Task progression，每个 Task 在该 transition 内最多尝试一次。
失败的资源尝试保留原 waiting ordinal；只有新的 Control binding 或 Runtime preparation
消耗等待优势。已 committed due intervals 可优先越过 round 次序，仍受每 transition 预算。

已 Commit 的 due scheduling intervals 先按其约束处理；hold、cancel、Group recovery、
window-missed 和既有 Actor/Context readiness 规则保留。策略只选择 logical Task 的
尝试顺序；Control 仍独占 Match/Propose/Commit/Bind、live eligibility、完整资源及
calendar validation。等待机会不能证明可部署性或保证物理完成时间。

Controller 支持显式 `POST /v1/missions/{id}/scheduling-policy` 修改该 Mission 的
priority，事件和 checkpoint 提交后生效。它不改 accepted plan、Actor、当前 attempt、
deadline、recovery budget 或 ownership。新 wrapper v24 fences 旧 binary 忽略该 ledger；
旧 checkpoint 无 policy 时保持默认 priority、初始 ordinal，恢复仍 Unknown/no-replay。

Orchestration 在 Group 创建前执行 deployment-owned admission limits，默认最多 128
nonterminal Missions、4096 declared Task/Role slots；hold 和 Cancelling 也占用名额。
已存在的完全相同 plan/Group 仍幂等接纳，terminal Mission 释放准入名额而不删除历史。
拒绝产生 typed capacity disposition，HTTP 返回 429，保留原内存/事件/checkpoint。
配置限制只约束新 admission，不撤销历史 ownership，不作为第二资源 quota。

## Validation gate

真实多 Mission 竞争下的 round fairness、优先级、等待老化、饥饿反例、资源冲突、
fixed Actor/Context reuse、future windows、hold/restore、bounded per-input work 和 durable
ledger rollback；生产 HTTP 验证 policy 更新、非法字段、SQL failure 与准入 429 的
无 mutation。Control generated ownership suite 保留。检查不运行 E6 或硬件。

当前新增 5 个 Core 和 7 个 application 检查通过：包括 2,040 次高 priority service 后
仍获得机会的老化、复原中间 history、一个 Mission 的多 roots 不抢占另一 Mission 的
首轮资源，以及实际两 Role sequential gang 完成后 B 先获得释放资源的反例。后者证明
NoCandidate 不能清零等待年龄。40 roots 验证 32-turn bound；SQL failure 保留所有
history/ownership。Current wrapper 缺少 ledger 和旧 wrapper 携带新 ledger 都拒绝。

该阶段独立于控制面复制；下一阶段复制包含整个 durable policy ledger，使切换不重置
公平历史。running physical work 的抢占继续受独立 stop/repeat authority 约束；未激活
future interval 的显式优先级让位需要下一 ADR 的 generation-bound Control 决定。
