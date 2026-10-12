# ADR-0085：Node 侧 Controller Epoch Fencing

- 状态：Implemented for the current replicated software profile
- 日期：2026-10-12
- 范围：Node Service durable command admission；不提供物理停止、mTLS 或跨主机部署

## Decision

Controller replication 的 issuing epoch 以可选字段随 `Execute` 和 `Cancel` 传递。当前
replicated Controller 使用序列化的 `(term, replica_id)`；standalone Controller 发送空值，
保留现有兼容路径。

Node Service 在本地 execution journal 中持久化最高已接受 epoch。每条带 epoch 的 Execute
或 Cancel 必须先通过 journal 的单调比较：相同 epoch 幂等，更高 epoch 原子推进，低于已持久
化 epoch 的命令在任何 Local EAIOS 调用、执行记录或取消 tombstone 之前被拒绝。拒绝仍可返回
`COMMAND_REJECTED` receipt，不能被解释为物理终止事实。

Controller 侧仍在每次实际发送前检查原 issuing epoch 和 quorum；Node 侧 fence 是第二道
delivery admission。它阻止 Node 在已经观察并持久化新 leader epoch 后继续接受旧 leader 的
命令，解决 stale Controller command 的可利用窗口。

## Boundaries

- epoch fence 不会撤销已经被 Node 接受的旧命令，也不证明 Local EAIOS 已停止；Unknown、
  stop proof、replacement 和 Actor takeover 仍遵循既有 Control/Runtime authority。
- 当前 profile 仍是 trusted-loopback 软件复制；Node Protocol 和 replica RPC 没有因此获得
  mTLS、调用者认证、跨主机 membership 或物理连续性证明。
- 若旧命令和新 epoch 首次接触在物理网络中竞态，Node 只能按到达顺序推进 durable fence；
  生产恢复仍必须先满足原有 stop-proof/recovery gate，不能仅凭 epoch 自动重派。
- 历史 Node journal 没有 epoch 行时按空值迁移；第一次 replicated command 原子建立该行。

## Validation

Journal tests verify durable reopen, equal-epoch idempotence, lower-epoch rejection and higher-
epoch advancement. Wire round-trip and Node protocol paths retain the optional field. Existing
standalone, legacy command and command-receipt tests remain valid with an empty epoch.
