# ADR-0083：Quorum Controller Replication 与保守切换

- 状态：Implemented for static trusted-loopback software profile
- 日期：2026-10-12
- Profile：三个静态 voting replicas、trusted loopback 软件部署；非正式 E6

## Decision

使用 [OpenRaft 0.9.25](https://github.com/databendlabs/openraft/releases/tag/v0.9.25)
负责选举、日志顺序、quorum 与 snapshot 安装。RoboGuide 提供 FULL-synchronous SQLite
log/state-machine storage 及 bounded private replica transport，不实现第二套共识。
ReplicaId 是控制基础设施身份，与 PhysicalEntityId/NodeId/Mission Actor 分离。

独立 controller-replica composition root 承载共识服务并管理一个自己启动的
integration-server 子进程。只有当前 leader 且 quorum 可确认时启动/保留 child。
child 每个持久 transaction 在 local Commit 前提交 exact base identity、完整 authority
checkpoint 和连续 event rows；只有 quorum 持久接受且同 epoch 可确认才提交本地投影、
安装 live candidate、ACK。新 Execute/Cancel 各自再次检查当前 epoch/quorum；失去
多数或 term 改变会 fence delivery，supervisor 关闭自己拥有的 Controller child/路由，
Local EAIOS 与物理工作遵守其原本规则。

Raft state machine 只保留已接受的 opaque authority snapshot、完整 event metadata history
和 source identities，作为一个 cluster 的唯一 durable accepted history。它不匹配 Node、
决定资源、修改 Task lifecycle 或路由命令。followers 保留该历史并参与共识；只有 leader
child 使用既有 Control/Runtime/Orchestration 权威。每个 application batch 校验前一
checkpoint digest 和 event tail；拒绝不产生第二 commitment。

升为新 epoch 前，supervisor 从已接受 replica view 原子重建 child 的 materialized SQLite，
然后走现有真实 startup restore：Control lease/route/session anchors 清空，nonterminal
Runtime 变 Unknown，reservation/Actor/registry watermark、原 receive time、停止证据、
immutable budgets、Mission hold 与公平 ledger 保留。旧 pending Execute 不自动重放。
quorum 已接受但 local Commit/response 丢失的 batch 也由该过程收敛；重复 physical
操作仍需要既有 stop/repeat/Commit/Rebind authority。

Node Service 可配置有限 controller endpoint 列表，session 丢失后按顺序重连并重新登记，
保持同一 Node、durable command journal 和 Local EAIOS handle。新 registration 不证明
旧 attempt 停止；它不授权重放或 Actor takeover。默认仍一个 endpoint/standalone server。

Replica profile 默认为关闭，当前提供固定三成员、loopback RPC、bounded snapshot/event
history。Artifact blobs 与 provider-local Memory 内容保留独立数据平面，不宣称复制。
跨主机认证、动态 membership、unbounded historical archival 和物理 continuity release
是后续部署 gate；不扩大本次 1/2/4 之外的安全/发布/E6 授权。

## Validation gate

OpenRaft storage conformance；durable reopen、snapshot/old-log compaction、拒绝错误 base、
schema/sequence/source、SQL failure、quorum loss 与 leader crash/partition/rejoin。
三个真实 loopback replica processes + production Controller/Node Protocol 验证：
quorum 前无 ACK/Execute，follower 不执行，旧 epoch 不发送，丢失 response 恢复已接受
batch，切换保留 ownership/Unknown/hold/fairness，Node journal 不重复 Execute。
所有测试仅合成软件工作；不运行模型、Habitat、真实设备或 E6。

## Implemented evidence and remaining deployment gates

生产 root、State acceptance port、发送 fence 和 Node fallback 已实现。upstream 完整 storage
conformance、实际三 engine election/loss/rejoin、snapshot/reopen/base 拒绝、原始 epoch、
quorum-before-SQL、response loss/local Commit failure 回归通过。真正三 replica/Controller/
two Node process 检查验证 minority 无 child、leader crash、Unknown/ownership/priority/journal
保留和 metadata rejoin；新 local Execute 数保持 2。补充 old route closure、standalone
downgrade、live quorum loss 与恢复检查也已通过。

发送 fence 是软件授权点，不是 Node 端分布式 fencing token，不证明任意进程暂停场景下
的硬实时停止。已发送工作保留原停止证据规则；controller failover 不等于 physical
continuation release。初次 debug hashing startup timeout 和一次切换瞬间的只读探针
connection reset 均保留；探针之后对 GET 重新解析 leader，未给 POST 添加重试。
部署、配置升级限制和完整结果入口见 [软件部署说明](../development/controller-replication.md)。
