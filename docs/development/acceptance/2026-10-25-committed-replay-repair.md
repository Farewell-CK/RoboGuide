# Committed replay input repair acceptance

状态：通过（2026-10-25）。本记录只覆盖 RoboGuide 软件存储、启动和副本进程，不覆盖设备、
物理停止、硬实时、模型效果或跨主机 HA。

## 实施范围

ADR-0198 在 Raft 启动前检查 committed-but-unapplied Log 的真实 qualified input。原回执或
字节不可用时，启动快速拒绝并保留 Log、applied/committed 指针、stream Unknown、producer、
预算和时钟。`content-maintenance` 提供明确的本地 OS-admin inspection、exact repair 和
completed-copy release；只打开已有 parent9/Root8，不能创建缺失 SQL、writer 或 Root。

修复绑定原 Log index、完整 entry digest、qualified Artifact owner、原 producer 引用和一次
不可续期预算。重复 ID 只读观察；改变 source、digest 或预算拒绝。复制只产生已有 Root copy
journal 的实际 placement receipt，旧 Unknown 不被改写。

## 实际证据

真实中断存量的原始 parent/Root 在双 writer/inode 锁下保存为 forensic-only。原生门禁
`replay-repair-native-05/result.json` 通过：

- 1 个真实 unavailable committed input，启动拒绝保留原 Log 14 entry digest；
- 缺失 parent、错误 entry digest、错误 Artifact owner 和缺失 source 均按原因拒绝；
- 1 个真实修复副本完成；源目录移走后同 ID 返回原结果，改变 budget/source 拒绝；
- 只释放修复副本自己的 Receive pin，原 Log 引用仍保留；
- 3 个实际 mTLS 成员重新入群并重启，6 个进程 wait 均为 0；
- post-repair bundle manifest：`sha256:3687f3bf5758fb21a671f861dc3e15d0d999b53c12a3efe2fdb6223d7674df4a`。

独立冷恢复门禁 `replay-repair-cold-native-04/result.json` 使用该固定 bundle 通过：验证/恢复
后 3 个成员重新入群并重启，6 个 wait 均为 0；307 保留 repair command，所有原 stream 原始
行（包括 Unknown、receipt 和时钟）逐行相等。

首次重跑中发现的真实 Artifact Snapshot 重复写入被保留为失败证据；门禁随后改用已固定的
post-repair bundle 做只读验证/冷恢复，没有放宽重复快照拒绝。

所有运行结果均声明 `embodied_nodes=0`、`devices=0`、`models=0`、`simulators=0`。
