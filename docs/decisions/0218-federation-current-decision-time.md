# ADR-0218: Federation decisions after writer admission

- Status: Accepted
- Date: 2026-10-26

## Context

实际双域 HA 在正常独立目标、在线 Archive 和两次 source-child drain transfer 后，
后续原事务的 probe 成功而两个正常 Control Prepare 均拒绝候选。原 Group 已 Released，
实际 Node 已重新注册并持续发送；不能靠新造设备、刷新旧 State 或重写原事务继续。

Federation 把 writer 排队前的 HTTP receive time 同时用于后续 Control 决策。原接收
事实和当前决策具有不同含义：资格、State、租约和策略可在排队期间过期或改变，不能用
旧时间授权新的动作。最初关于 lease issue-time 下界的假设被回归排除，不作为根因证据。
实际候选拒绝原因需要在相同决策视图中观察，不能靠错误字符串推断。

## Decision

原 receive time/kernel clock 继续固定 probe、回执、预算和审计。取得原 application writer、
克隆当前已提交候选之后，重新采样 decision time，供正常 Prepare、Commit/release 的
当前 binding 检查和 stop/abort 使用。当前 State、租约和完整 Control 路径保留；
不改 lease issue time、freshness、nonce、原预算或已失败回包。最终授权、epoch 和 budget
检查继续在同一 Commit 边界，不新增 checkpoint/Node 格式。

原 probe/prepare/commit/release 的真实 4xx 回包进入 stop-only reconciliation。
该状态不是无副作用证明，不删除原意图、Unknown、全局决定或 ownership，不重发 POST；
只有原正常 stop/revoke/release 证据可关闭。服务错误/丢包仍保留原有有界观察路径。

正常 Prepare 失败包含同一 State/decision time 的有界 first-use 候选排除计数；未获支持
的诊断保留 unavailable。计数不选择 Node、不增加库存或改动任何资格规则。

只读调度 hint 在已有 barrier/backoff 或原 cleanup 期限耗尽时跳过空 writer/SQL turn。
它读取真实已提交原记录、当前配置/epoch/授权和原预算，不发请求、不提交新事实。
有原回包需要归约、首次预算到期、epoch/授权变化或实际工作时仍进入原 writer，重复全部
权威/预算/当前状态检查。hint 不能延长原预算；它只减少无状态变化的竞争。

## Validation and limits

Control 合同回归验证：原接收时点曾可用的候选，在实际决策时资格已过期就必须拒绝，
原 lease/health receive time 均不修改。Root 回归验证：409 保留原回包，不能 seal 决定、
补造 stopped 或重放请求。完整真实双域 HA gate 仍必须通过，并保留此前原失败。

软件节点、进程退出和网络证明不建立设备停止、物理交接、跨主机连续性或长期 SLO。
