# ADR-0173：严格无变化的 timer 事务

- 状态：Accepted，本包已实现并验收
- 日期：2026-10-23
- 前置：[ADR-0172](0172-runnable-owned-controller-content-profile.md)

## 问题和责任

真实 runnable 内容 profile 的原日志显示，零 Node 空闲 timer 仍会反复将完全相同的
完整 checkpoint 作为新命令复制。每次都触发实际 Log/View producer、原 pin、SQL
刷盘和完整内容多数观察，增加操作延迟和持久日志压力。不能仅凭没有 domain event
跳过：零事件仍可能改变完整 checkpoint、Memory/Archive SQL 或需要发送已持久 outbox。

## 决定

State 提供原实际事务内的保守无变化核对。原 writer/租约和事务前 quorum 检查保持；
`BEGIN IMMEDIATE` 后保存同一真实 SQLite connection 的 `total_changes` 基线。
上次实际完整 checkpoint/command/COMMIT 还须保留该 connection 的原 total_changes
和 writer transaction 内取样的 `PRAGMA data_version`。二者均须仍匹配，才能排除
事务前已发生的 Archive 等辅助写入，以及另一实际 connection 的提交；首次打开或
重启没有已确认的本地基线时，必须正常完整提交后才允许优化，不能从启动读数补造。
只有以下条件全部成立时，可在原 writer 内回滚本次无变化事务：原事件序号不变、
实际 `total_changes` 与两个原基线相同、data_version 不变、没有 pending checkpoint、已提交完整 checkpoint 的
schema/序号/精确 JSON bytes 与本次候选相同，并与实际 SQL 完整 checkpoint 一致。
缺基线、无原 checkpoint、任何 SQL write、savepoint 中已回滚的 write、事件、
已写命令或 checkpoint 变化都不能走此路径。错误沿原 write-disabled 规则处理。

仅内部 application timer 在创建新 journal command 之前调用此核对。外部命令和
显式零事件命令保持原审计/幂等/Commit，不通过通用 commit API 自动合并。
真正无变化的 timer 不生成新的 authority revision/复制/成功回执。完整候选仍经
实际 timer 归约；原 committed outbox 的后续发送仍走原当前权限、完整 delivery
fence 和 fresh clock，不能因本轮没有新 Commit 而跳过停止、撤权或重发已提交命令。

这是同步事务内的性能优化，不改 Domain/Node/Memory 合同或持久 schema，不刷新原
权限、来源时钟、budget、Unknown 或接收时间。不能缓存一个当前可用/ready 布尔量来
代替每次实际检查，也不放宽 quorum/字节多数或 ADR-0172 的显式观察预算。

## 验收

用实际 SQL、replicator 计数与 timer 验证无变化不新增 journal/复制，独立的零事件
checkpoint/SQL/命令变化仍持久化，回滚过的 SQL write 也保守拒绝优化。维护的零 Node
真实 Controller 内容 gate 加实际只读空闲观察；冻结旧二进制应暴露原 idle revision
增长，当前二进制同时保留正常 HTTP 上传、全部实际字节、原 wait/同 inode 重启。
完整文件/执行 gate 验证已有 outbox/停止/撤权路径。跨 owner/Prepared takeover 与
其余 N1–N7/P1–P7 继续实施，软件性能观察不证明设备或长期 SLO。
原旧二进制反例、新实际产品与文件交接结果见
[验收记录](../development/acceptance/2026-10-23-unchanged-timer-transactions.md)。
