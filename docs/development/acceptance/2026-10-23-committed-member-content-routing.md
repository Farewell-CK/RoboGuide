# 2026-10-23 已提交成员内容路由验收

唯一分支 `codex/nonrelease-remediation-closure`，前置 `a214c6c`。
[ADR-0174](../../decisions/0174-committed-member-content-routing.md)先定义 source
member 与原 Artifact source owner 的边界，再修改 Replica stream。

`ReplicaNode::stream_content_to` 现在接受当前 target membership 中任一实际 voter
作为 source member。transport 仍要求真实 mTLS、当前 committed target/profile/epoch、
qualified input、receiver owner、原 command/budget；接收 SQL journal 保存实际
`StreamOrigin::Replica { member, store_owner, peer_leaf }`。旧 target leader 作为
source 的路径仍兼容。source member 只是承载已复制 Root bytes 的软件副本，不是
Artifact owner、semantic Memory provider 或设备 Node。

## 实际验证

执行：

```bash
cargo test --locked -p controller-replication network_content --lib
```

通过 **1/1** 实际三成员 mTLS/Raft/root 测试。测试先从原 leader 完成 source→两个
receiver 的 qualified bytes 和完整 target，再隐藏原 leader Root 的 qualified 文件，
由另一个已提交 voter 作为 source member 执行 `actual-cross-owner-root-route`，
原 leader 恢复完整 bytes 和 receipt。测试随后隐藏两个 receiver 的实际 bytes，
metadata-only majority 继续被拒绝，恢复后完整三成员 quorum 再通过；错误 owner
target 仍拒绝。测试创建零 embodied Nodes、设备、模型和模拟器。

这闭合了 Replica Root 的跨成员读取路由；它不闭合原 Prepared upload takeover、
原 Artifact owner 转移、公开 CAS finalization、完整 readiness、在线 HA/升级/
recovery/GC 或其它 N1–N7/P1–P7 项。
