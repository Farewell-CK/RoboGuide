# ADR-0121：故障触发的持久 MI 工作与独立审查

日期：2026-10-20。状态：Accepted，实施中。

ADR-0120 的原目标、版本历史、完整停止/撤销与 Control 承诺权保持原义。v0.1 只自动消费
精确预审替代；没有候选时不能把本轮新增 MI 生成权隐藏到旧协议中。

显式 runtime-plan-revision v0.2 `authorize-generation` 由实际 recovery-admin 为现有
不可替换 policy 授权一次。原 issuer、实际 leaf/grant、tenant、时间和 policy digest
入库；没有续期、扩充次数或增加自动触发源。原 policy proposer 仍是唯一候选提交者，
新候选仍要求实际 recovery-admin 对 exact digest Review。生成权限本身不授予 Review、
Node、资源、停止证明或独立 goal producer 权限。

只有原 policy 明确列出的真实故障可消费新工作票据。先持久化原 source revision/Group、
完整 attempts/generations/session、hold 和 typed stop/Memory revoke，再对外暴露票据。
generation、Review 和 switch 共用故障发生时固定的原预算；重启、排队或新 HTTP command
不能更新该锚点。没有新候选或 Review 时保持 hold；预算耗尽、权限/版本改变、拒绝和
Unknown 不授权释放、自动重试或回到旧 Execute。普通 resume/recovery 仍受原 pending fence。

MI worker 只消费 Controller 已提交票据及原 MI Request→Mission 的真实映射。先在原 SQL
存储写入 work/call intent，再调用配置的 provider；完整原 input/output、代码、配置、
Catalog、Prompt、依赖和实际 provider response identity 随 receipt 保留。未确认调用
保持 uncertain，重启只观察原命令，不再次调用 provider 或再次生成。原 SQL 工作者锁、
原 boot/time namespace 和总预算限制并发和存储；网络等待不持有 Controller authority。

仅有本地 SQL 不能抵御空库或较早副本。v0.2 的 `claim-runtime-work` 在 Controller 固定
原 SQL instance、work/config identity 与实际 proposer；`admit-provider-call` 再固定原
phase/request digest 和 SQL 在 I/O 前生成的 nonce。每 ticket 的 generation/review phase
各只能有一个 nonce。旧副本不能取得新许可；已有 pending/uncertain SQL intent 也不重发。
Controller receipt 证明工作许可，不声称 provider 已完成。原始回包先保存后解析；独立
只读观察另存，原 HTTP 不确定结果不被改写成一个虚构的成功回包。

模型仅给候选和审查建议。生成服务不得自授 recovery-admin；独立配置的实际 reviewer
身份可按部署明确授权提交 Review，缺失时等待人工审查。Review 完成后，只有原完整 stop/
revoke、当前 State/owner/session/tenant/goal、原 deadline 和 Control 新 Commit/Bind
齐备才能 Execute。旧 scoped Memory 没有显式原 lineage 转移时继续拒绝切换。

共享 Controller wrapper v44 封住新增生成授权/等待状态；v43 无新增生成权的数据保留原
policy digest、候选和停止预算迁移。旧 writer 和伪装旧 wrapper 的新增权限拒绝。
Node Contract、journal、Bridge inner v23 和事件 codec v28 不增加新的设备或执行事实。
真实软件进程与显式 fixture provider 仅验收调用/SQL/HTTP/停止门禁，不宣称模型效果、
设备停止、物理连续性或全部非发布整改完成。
