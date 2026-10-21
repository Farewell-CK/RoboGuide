# 持久化 leader transfer drain 验收

`ReplicaNode::request_leader_transfer` 现在在暂停 source ticker 前写入 `prepared`，目标
在 election 前写入 `accepted`，source 观察目标新 epoch 后写入 `target-elected`。目标重新
成为 leader 时把相同 identity 单调推进为 `completed`；`prepared`、`accepted` 和
`unknown` 会在重启后阻止 owned Controller child 自动启动。

`StoreSnapshot` 保留该 record，安装较旧 source snapshot 时保留本地更高状态，避免已完成
的 target hold 被回放覆盖。identity、source epoch、target 和 required log index 不得更改。

Rust 1.99.1 下 `controller-replication` 的 28 项测试通过，其中包括三副本
`round-trip`、ticker 恢复、durable record 重启/单调性和 snapshot/conformance。该证据只
覆盖软件共识与 owned process 生命周期，不证明设备停止、物理 handoff 或跨故障域 HA。
