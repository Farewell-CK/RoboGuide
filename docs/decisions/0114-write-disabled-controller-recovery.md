# ADR-0114：写入禁用、最后提交视图与显式恢复

状态：Accepted。日期：2026-10-20。

## 问题与权威

共享持久化错误目前使 Controller 退出，查询和诊断同时消失。继续自动重试写入又会掩盖
不确定 Commit，并可能发送未确认命令。本决定保留唯一应用 writer、私有 candidate、
SQL/checkpoint Commit 和 post-Commit outbox；不建立第二份 Control/Runtime 权威。

## 决定

运行应用显式启用 EventLog 的进程级写入故障策略。begin/save/append/commit/audit 的持久化
失败，以及 timer/fact driver 的共享错误，在原 writer ordering 内锁存 `write-disabled`。
第一原因、进程实例和代际保持不变。后续批次、审计写入、archive、派生命令与 Node ACK
拒绝；Node 输入得到 unavailable 而不是 durable acceptance。普通输入拒绝不触发全局锁存。
timer 和协议 listener 留存，已接纳操作的原期限不续期，Local EAIOS 继续拥有本地安全权威。

Operator HTTP 提供 `GET /v1/authority-status` 与 recovery-admin 的
`POST /v1/authority/recover`。查询现有最后提交投影，不改变 State receive time 或新鲜度；
readiness 返回 503，metrics 明确当前 write-enabled 与最后提交证据，不伪称最新世界事实。
全部证书主体、当前部署/提交 grants、角色和 tenant 门禁仍生效；故障期间不通过提交失败
绕过 grants 变化。复制 epoch/quorum 丢失保持原 watchdog 退出规则，不提供分区旧 ACL 内容。
Artifact 私有内容保持原审计/权限门禁；故障时拒绝内容访问，不把缺少持久审计写成成功。

恢复请求是封闭、幂等、绑定原 process/generation/checkpoint digest 的命令。实际认证主体
必须在取得同一 writer 后再次验证。独立 SQL 读取必须与完整原 committed cache 相同，
不存在 open/pending transaction，并通过原 authority-journal 校验。写入原 checkpoint 和
专属 command journal 的零事件探针 Commit 后才解除锁存。它不改变 Mission、attempt、
资源、State 时间或 outbox，不立即发送 Execute。故障未解除、数据变化或未知 Commit 均
继续拒绝；同命令返回原 receipt，不重复探针。旧进程 receipt 不解除新进程故障。

复制批次若出现接受不确定性，当前端口不能证明远端完整 tail，禁止进程内恢复；要求由
已有 supervisor 从实际 accepted replica 重新物化后启动。Artifact 独立 catalog fence 也
要求重启重放。本阶段不把本地 SQL 探针冒充在线 HA 数据面恢复。

此策略不改变 durable checkpoint/domain codec/Node contract：现有 command journal 保存
原主体、输入 digest、authority revision 和相同 checkpoint；进程级 fence 不藏入旧字段。
启动仍先验证和恢复原权威，Unknown 不自动释放。后续任何新增 durable authority 另行升级。

## 验收

使用真实 Controller HTTPS、SQLite trigger 和已提交查询：注入 save/commit 故障后进程
仍活，writes/ACK/outbox 被阻断；原视图、期限和第一次故障代际不变。故障未解除、身份
降权、错误 digest/process 和同键异参拒绝。去除故障后显式恢复只增加一条原 command，
不增加事件/attempt；重复恢复不重做，随后正常写入。复制不确定性单独拒绝。零设备、
零模型、零 simulator；这些证据不证明机器人已停止或物理连续性。
