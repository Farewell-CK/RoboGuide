# ADR-0083：Quorum Controller Replication 与保守切换

- 状态：Implemented for static v0.1 and pre-enrolled dynamic v0.2 software profiles; non-loopback transport requires ADR-0091 mTLS
- 日期：2026-10-12
- Profile：三个静态 voting replicas、loopback compatibility 或 mTLS software deployment；非正式 E6

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

Replica profile 默认为关闭，v0.1 提供固定三成员，v0.2 允许完整预注册 member universe、learner
catch-up 和 joint consensus 加入/移除；loopback compatibility 或 ADR-0091 mTLS RPC、bounded
snapshot/event history 保持不变。私有 member envelope 提供带 epoch 的 learner/membership/leader
transfer 管理命令，旧 leader 先停 child 和 heartbeat，目标实际新 term 选举后才收敛。Artifact
blobs 与 provider-local Memory 内容保留独立数据平面，不宣称复制。任意新主机 enrollment、
跨副本 archive 共识和物理 continuity release 仍是后续部署 gate；不扩大本次安全/发布/E6 授权。

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

发送 fence 本身是 Controller 软件授权点。ADR-0085 后，replicated profile 还把 issuing
epoch 随 Execute/Cancel 传给 Node，并由 Node journal 持久化最高 epoch；这是 stale-command
delivery fence，但不是密码学身份、硬实时停止或 physical continuation release。已发送工作
仍保留原停止证据规则；controller failover 不等于 physical continuation release。初次 debug
hashing startup timeout 和一次切换瞬间的只读探针 connection reset 均保留；探针之后对 GET
重新解析 leader，未给 POST 添加重试。

应用 Commit 的 transport response 若在 `client_write` 之后丢失，不再直接按拒绝处理。当前
child 会在同一 issuing epoch 下读取小型 accepted application identity
`(revision,event_sequence,checkpoint_digest)`：精确匹配目标时继续本地 SQL Commit；不匹配或
identity 无法确认时返回 ambiguity，application batch 回滚，child 由 supervisor 重建。这样
不会在 quorum 已接受而 response 丢失时继续使用旧前驱，也不会在无法确认时假造本地提交。
部署、配置升级限制和完整结果入口见 [软件部署说明](../development/controller-replication.md)。

ADR-0091 的 `--tls` 进程复核另外验证了 rustls 双向证书握手、SIGSTOP majority loss 和
恢复；F5 动态成员/transfer 的实际软件复核见
[`2026-10-16-f5-membership-transfer.md`](../development/acceptance/2026-10-16-f5-membership-transfer.md)。
它仍只证明软件进程边界，不能替代任意新主机 enrollment、PKI rotation、跨站点容量或物理
continuity 证据。
