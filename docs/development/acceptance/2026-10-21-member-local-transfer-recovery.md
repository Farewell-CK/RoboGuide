# 成员本地 transfer 与 v0.6 恢复验收

ADR-0136 修正 transfer 运维记录与 Raft authority 的边界。最多 512 个原 ID 保留在成员
本地 SQL 中，终态不可改写，Unknown 不能变成 rejected 或自动重发。原八秒 effect budget
从提交 intent 前开始；重启/观察不发起 election。目标完成要求实际新 epoch 的
linearizable quorum，源经实际认证只读请求取得 exact original proof 才能解除 hold。
源的应用 fence/commit 在 intent 后拒绝。source-child 实际 stop/wait 仍是下一项独立门禁。

`TransferLeadershipV2` 显式拒绝旧 target 命令。storage v2 保留原 v1 物理表与只读旧名，
原 v1 marker/legacy transfer 原值保留；弱完成迁移为 Unknown。升级前排空 SQLite，
marker 提交到主数据库后才开启 WAL。Raft snapshot 不导入或抹除任一成员的本地 hold。

本轮通过 35 项 Replica 回归，含真实 TCP 在目标 quorum 后丢失原回包、重复原 ID 不再
产生目标 mutation frame、原完成只读对账、不同成员 snapshot、完整历史/restart 与 v1
弱证据迁移。整仓 Rust 933 项通过、2 项保持原 ignored；fmt、完整 Clippy 和私有 Rustdoc
均以 warnings-as-errors 通过。Recovery 30 项通过，包括 repinned 假完成证明仍被拒绝。

实际软件进程结果：

| 证据 | 原结果摘要 | 已验证范围 |
|---|---|---|
| `/tmp/rg-replica-transfer-storage-v2-20261021-c/RESULT.json` | `641ac2dd8b361775240fc5053d888d5f5522d3b5d94fd13efb920bb107d0a994` | 十个实际进程；旧 v0/v1 活跃拒绝、排空后升级、hardlink writer fence、旧 binary 拒绝、原 identity 保留；probe 首尾摘要一致 |
| `/tmp/rg-replica-transfer-certificates-v2-20261021-a/result.json` | `2ea0fbc2b7b665b17ea5fe2065e212b9693f4a96856b853c9bbee9564829ce14` | 三成员 mTLS、换 leaf/CA、真实 leader 切换与整体重启、原十条 trust receipt |
| `/tmp/rg-replica-transfer-complete-recovery-v2-20261021-a/result.json` | `cc958c9b01375049dfe377659d89ff6745aa36bd5b33c0f9e44763da27191383` | 六个 SQL、42 张表逐行不变，完整 transfer 历史、CAS 原 bytes/finalize receipt；恢复后三个实际 leader 均独立读取内容；v0.5 旧工具拒绝 v0.6 |

所有原进程已回收，Node、设备、模型、模拟器均为零。实际 Replica executable 为
`874a586a1f75fe4398460407ce03e9397b3898dc121b868959e52dc63abcd492`，Controller 保持原
v45 兼容 binary `87e51b689be71f5cd7f70caa78179f8ddf9930fc57801dddf647999f25c7abe0`。
这些完整 declared-store 结果不关闭 source-child drain、在线 Archive/Memory/CAS、跨故障域
恢复或物理连续性，也不能代替其余 F4–F10 验收。
