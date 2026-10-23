# ADR-0174：已提交成员内容路由

- 状态：Accepted，本包已实现并验收
- 日期：2026-10-23
- 前置：[ADR-0169](0169-consensus-bound-content-stream-transport.md)、[ADR-0172](0172-runnable-owned-controller-content-profile.md)

## 问题

当前 owner 切换后，新的 Controller 可能需要从另一个仍保有完整 qualified Root
bytes 的副本读取内容。此前 `stream_content_to` 只允许当前 epoch leader 作为
source member，导致“接收成员有完整 bytes”与“当前调用者正是原 leader”被错误
绑定。用 bare hash、本地 `replica-content` owner 别名或新的 Artifact owner
替代原 `source_owner` 都会丢失内容来源边界。

## 决定

在一个已提交的 `ContentQuorumTarget` 内，任一当前 voter 都可以作为 **Replica
source member**，把其 member-local Root 中已经验证的 qualified content 流给
另一个 voter。source member 必须来自当前/joint membership，实际 mTLS leaf 必须
匹配该 member 的 committed address/trust，target epoch/membership/profile、
qualified `(source_owner,digest,size)`、receiver Root owner、command ID 和不可续
budget 仍逐块复核。接收端 durable stream intent 的 `StreamOrigin::Replica.member`
记录实际 source member，`store_owner` 记录其 `replica-content:` Root owner；这两个
字段都不能改写原 Artifact `source_owner`。

当前 leader 仍是 application authority 和 target epoch 的产生者；本决定只扩大
qualified Root byte 的实际读取路由，不改变谁可以 Commit、谁拥有 Artifact、谁有
semantic Memory use，也不从一个 member 的读取收据推断整组 quorum。完成内容 ACK
仍由同一 current/joint voter 的完整 metadata+bytes majority 计算。source member
缺失、不是 voter、TLS leaf 变化、target 变化、字节缺失或预算到期都 fail closed。

这条路由可以恢复接收成员缺失的本地 Root bytes，但不等于 Prepared upload takeover。
原 Prepared Artifact 的 source owner、原 upload journal、当前 Artifact writer 和
公开 CAS publication 仍需独立恢复/finalization contract；复制 Root bytes 不会生成
新的 Artifact owner 或 use grant。

## 验收

实际三成员 mTLS/Raft/content roots 先完成正常 source→receiver stream 并提交完整
target；随后隐藏当前 leader 的 qualified Root file，由另一个已提交 voter 作为
source member 路由到 leader，校验实际 bytes 和原 receipt。旧同 ID replay、隐藏
两个成员后的 metadata-only majority rejection、错误 owner target 和完整 quorum
恢复继续通过。该验证创建零 embodied Node、设备、模型和模拟器，不证明 Prepared
takeover、跨主机连续性或整体 SoftwareComplete。
