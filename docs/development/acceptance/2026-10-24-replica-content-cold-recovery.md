# parent8 / Root 完整静止数据恢复与实际重启

状态：静止数据链、二次恢复及实际三 leader 重启组合验收通过。
对应 [ADR-0184](../../decisions/0184-explicit-replica-content-cold-restore.md)、
[ADR-0185](../../decisions/0185-live-producer-reconciliation.md)、
[ADR-0186](../../decisions/0186-owned-apply-content-readiness.md)。尚不标记完整 N3 或 SoftwareComplete。

## 存储边界

显式 recovery v0.19 同时覆盖原 parent8、每个原 Root4/Root5、实际已有的 Controller SQL、
Artifact 内容和部署输入。未曾启动 Controller 的成员不虚构 SQL/CAS。源 profile 必须完整
覆盖原 committed member/store map；supervisor、parent SQL inode、Root writer 和 Root SQL
inode 全部冻结后才读数据。必要表/head、原操作与独立 pin、当前输入的同 Root 精确
`(source_owner,digest,size)` receipt/字节和 recovery 原 reader close 均独立检查。

恢复保持原逻辑 Root 身份及原操作数据不变，Root5 binding 单独记录来源 manifest/SQL、
原 parent 和实际新 Root/SQL/parent inode。原文件 inode 仍是历史来源；新 stream worker
固定实际恢复 backing。显式 process v0.3 和配置中的 binding digest 才能打开 Root5。
原 v0.18 仍拒绝 parent8/Root4 和新增恢复事件；旧 Root 入口拒绝新 backing。

实际 bundle 包含 3 个 parent、3 个 Root、2 个实际 Controller SQL、2 个实际 Artifact 根
及 TLS/config 输入，共 8 个 SQL。它保留离线成员的真实落后状态，不把不同成员的内容
或进度合并成已经同步。恢复不启动服务、不续原预算，也不填充原 Unknown。

## 实际证据

证据根：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

| 记录 | 实际结果和边界 |
| --- | --- |
| `root-recovery-source-native-01` | 新的实际 3 supervisor / Controller / TLS / 文件 / SQL 源链通过，实际 SIGKILL/reap、换主、正常新上传和完成记录 SQL 故障；原 Prepared 与 Unknown 保留，原进程全部 wait 为 0 |
| `root-recovery-cold-native-final-01` | 实际 Root SQL inode 被持有时拒绝；完整 backup / verify / 新 inode restore / readonly repeat 通过；原操作和预算独立比较 |
| `root-recovery-second-cold-native-01` | 以上实际恢复后的 Root5 再 backup / verify / restore / readonly repeat 通过，3 个新 binding ordinal 均为 2，旧 binding 原文及原操作保持 |
| `root-recovery-full-native-01` | 新 stream 最初误用原 receiver 的旧物理 inode，实际拒绝；已改为固定当前显式 backing，并用真实文件/stream 回归验证 |
| `root-recovery-full-native-02` | 检查器误用 `/health`，不是正确的 `/healthz`；失败记录保留，不计产品故障或通过 |
| `root-recovery-full-native-03` | 当前 leader 实际读字节和原 Unknown 通过；过早请求未追上成员的 transfer 被拒绝。后续脚本先独立只读冻结并观察 source 日志位置，再只发一次 transfer |
| `root-recovery-full-native-04` / `05` | 实际落后成员在 apply 时遇到仍存活 producer，原清理错误造成 apply channel 关闭；ADR-0185 保留原 Unknown / pin，不伪造退出和释放 |
| `root-recovery-full-native-06` | 已越过前一失败，实际暴露元数据先于并发字节发布的 race；ADR-0186 在原 worker / 原预算内等待真实就绪，且不占用 parent 写锁 |
| `root-recovery-full-native-07` | 实际 337 → 307 → 311 追赶/transfer 均通过，三个 leader 读取原字节、原 Unknown 不变；三个 supervisor wait 均为 0；独立 SQL/CAS 核验通过 |

成功的 readonly repeat 只验证原恢复结果；实际 runtime 修改了已恢复存储后，原 restore
不能覆盖、修复或重新复制它。接入 runnable 时只移动原 Controller SQL / Artifact 根，
Root 和绑定的 parent SQL inode 均保留；实际配置单独留存。实际旧 supervisor 的拒绝和
新 binary 的原 Root 入口拒绝分开记录；没有实际旧 binary 时旧 binary 证明为 unavailable。

## 保留的语义与限制

实际 held-permit 回归验证：3 次对账保留原 Unknown / 整行 / budget / pin，不重复 log
副作用；只有实际 permit Drop 才允许后续原 parent 观察退休。实际迟到文件/SQL 发布与
原时钟到期、已知 receipt 字节损坏回归验证 parent 在等待期间仍可访问且不产生 authority。

这些均为本机软件进程、SQLite、TLS 和实际文件事实。所有新增路径创建 0 embodied Nodes、
0 devices、0 models、0 simulators。它们不证明硬件停止、物理连续性、跨主机全局租约、
长期 SLO 或总体整改完成。尚缺的历史内容来源/路由、完整 online Archive/Snapshot/GC、
joint/fault/升级/回退和原 N1–N7 / F0–F10 / R01–R20 矩阵仍须独立完成。

当前冻结 supervisor 为 `controller-replica-adr0186-01`，SHA-256 `34099e42f818d28f02647865ae7c709203da4408a36901a96e60a764919c022d`；应用保持原 `integration-server-adr0183-01`，SHA-256 `0119c8104b29e09af79875e7381c34b6c0017eb84d2ac669a5f80015d845f942`。实际旧 supervisor SHA-256 为 `2f15921d88ce5edb5ba08ae4b8b0ab436130647bc8ec977ff68389be3ad4e660`。独立检查见同一 case 的 `independent-verification.json`，核对原 intent/receipt、实际新 inode、原 Unknown 恰好一条且 completion 为空，以及 0 Node 记录。

最终相关检查：Replica Rust 118 项、recovery Python 152 项、recovery 严格类型 38 个文件、
全工作区 Clippy、受影响 Rust 私有文档、Python 函数文档、站点 9 项及 strict build 均通过。
最终收紧的 SQL DELETE-journal/header/主键检查再次验证实际成功 bundle，101 个文件的原
manifest digest 不变；没有重跑无关的实验或把数据复验当成新的运行连续性证明。
