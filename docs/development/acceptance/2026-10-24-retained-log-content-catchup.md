# 历史 Log 内容追赶软件验收

状态：相同已应用成员配置下的 Log 路由专项通过。对应 [ADR-0187](../../decisions/0187-retained-log-content-catchup.md)。
完整 A1、N1/N3 及 SoftwareComplete 尚未关闭。

## 实现与权威

新的历史 describe/request/push 与普通 committed route 独立。来源实际 SQL 的原 Log、
提交位置和原 producer，接收方自己的原 Log/producer，分别先于 Ready 和字节读取。
原 candidate 固定 qualified owner/hash/size；实际 membership/epoch/Root/TLS 和原预算
在逐块传输中复核。原 stream 意图/pin/回执复用，不新增持久权限、Node 合同或 wrapper。
ID 不包含 retry/epoch，旧 Unknown 不能通过换 epoch 取得新原 ID。

apply 在原 worker/原时钟内先持久化共识-only 前缀，再等待后续内容命令，避免成员配置
被它所授权的内容反向阻塞；每个原应用命令仍保持原子性。SQL/hash worker 的实际容量
随真实生命周期持有，取消 async 等待不会提前释放。逐块检查不重入自身 Root 锁。

## 实际证据

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

| 证据 | 结果与边界 |
| --- | --- |
| `historical-log-native-test-04.log` | 实际单 worker/mTLS 服务中，离线副本漏掉 bytes 后经原 Log 追赶；检查器字段名修正后通过 |
| `historical-log-native-test-05.log` | 更严格的两版本源暴露 bootstrap membership 与后续内容同批等待的循环依赖；原失败保留 |
| `historical-log-native-test-06.log` | 共识前缀修复后，两版本来源、历史目标与普通当前路由区分、真实字节/apply 通过 |
| `historical-log-tests-final-05.log` | Replica 123 项通过，包括来源未提交/错误 Log/原 producer、前缀到期不产生应用 authority、旧路由和现有 stream/Unknown 回归 |
| `historical-log-processes-native-01` | 已归档压缩源首次捕获缺少原 event/store binding，实际拒绝，未算通过；原记录和进程 wait 保留，归入 A2 归档接线缺口 |
| `historical-log-processes-native-02` | 原未压缩 Controller SQL/CAS、3 个实际独立 mTLS/SQL/Root 进程；实际停止成员、漏掉初次发布、原文件重启追赶、完整 quorum、普通路由/缺字节多数拒绝、实际 SIGKILL/wait、同 inode 重开均通过 |
| 同 case 的 `independent-verification.json` | 独立 readonly SQL 比较双方完整同一 Log、来源/接收方提交位置、原 producer/pin、确定性原 ID、源/接收 Root 身份和实际完整 CAS hash；5 个实际 incarnation 的 wait 为 `-9,0,0,0,0`，其中 `-9` 是明确的原 SIGKILL |

测试 fixture 的实际端口预留→bind 存在两个 cluster 间竞争，已在该模块启动范围互斥，
未改变生产端口/成员配置。最终全工作区 Clippy、受影响 Rust 私有文档、Python 类型/
format/lint/函数文档、站点 9 项及 strict build 通过。中间编译和断言失败记录保留。

冻结 probe SHA-256：`content-network-probe-adr0187-01` 为
`2cf7496153979685bbb00b6b90b940fbe84ca0f0ffc22881e1696e3bd6b2ef02`；
`content-stream-probe-adr0187-01` 为
`2d287570562a1664eb7f09f51a18d816b631aa75237476bccd1292ffc7ec39f0`。
配置、源码 hash、原请求/响应、各进程日志与实际退出保存在同 case，原输入/二进制均未
在运行途中替换。维护门禁为 `check_network_content_processes.py --historical-log-catchup`，
CI 直接扩展既有实际 network gate，未重复构造另一套应用环境。

## 剩余项

完整网络 Snapshot、learner/joint 成员差异、原 Unknown 的更多 SQL/响应丢失网络组合、
历史源失联后的显式修复、已归档源首次接纳，以及全 producer GC/升级矩阵继续实施。
新增路径创建 0 embodied Nodes/devices/models/simulators；输入文件是保留的软件记录，
不证明当前设备、跨主机独立故障域、物理停止/连续性或长期 SLO。
