# Replica 原 stream 退休、CAS 回收与 Root7 恢复

状态：已完成本包软件实现和实际进程验收。对应
[ADR-0190](../../decisions/0190-replica-content-gc-intent.md)及
[ADR-0191](../../decisions/0191-bounded-log-catchup-outside-append-ack.md)。
完整 producer/joint/partial staging、滚动升级/回退及原非发布矩阵继续必需。

## 权威及存储边界

Root7 的 mandatory `gc_ids`、`tombstone_keys`、`stream_retirement_ids` 与原 SQL 行完整匹配。
首次 GC 冻结原 qualified key、原 byte receipt、全部已退休引用摘要、当前实际文件 inode
和原预算；intent、首个不可逆 tombstone 与 heads 同事务提交后才可删除。逐项实际
hash/inode/unlink/parent-fsync 和原时钟检查先于独立 SQL outcome。删除回执、实际
NotFound 观察与 Unknown 独立保留；同 ID 重试只读，独立后续命令不填充原 Unknown。

stream 退休冻结原整行和独立退休预算，仅实际 worker close 或独立原进程结束观察才能
原子释放该 stream 的 pin。原 receipt/Unknown、source/target、I/O budget 不变；其它
Log/View/Snapshot/reader 仍独立保护字节。活跃 worker 拒绝退休。tombstone 阻止新 copy、
stream、producer、reader、Snapshot receiving、reference 和当前 availability。

本地 OS-admin 入口为 `controller-replica --content-maintenance`，显式操作包括
`upgrade-reclamation-root`、`retire-content-stream`、`collect-content`、`observe-content-gc`。
普通 Root4/5/6 入口不能打开 Root7。显式 process v0.5 与 recovery v0.21 保留新边界；
恢复 binding v0.3 记录实际新 Root/SQL/parent backing，原逻辑身份、原行和旧 binding 原文不变。

## 实际证据

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。所有下面的新门禁均
创建 0 embodied Nodes、0 devices、0 models、0 simulators。

| 记录 | 结果及边界 |
| --- | --- |
| `gc-native-final-01` / `02` | 实际源 CAS、CLI、Root6 → 7、旧 binary 拒绝；活跃 copy 阻止 GC；删除前 SIGKILL、删除后 SQL outcome 失败、原 Unknown/同 ID 只读、独立实际 absence/unlink、tombstone 新获取拒绝；实际被杀 stream 的原进程结束退休及独立 repair/GC 通过 |
| `gc-native-final-03` | 上述门禁加实际 SQL writer 持锁：首次 intent 未提交时 GC 拒绝，原 GC 行不存在，原字节不变；已提交 intent 后的回执失败继续保持 Unknown |
| `gc-runnable-source-final-01` | 原真实三副本运行链、Controller SIGKILL/reap/restart、实际 HTTP 上传/跨 owner 读取、Prepared recovery 的 SQL completion 故障与原 Unknown、源 Prepared/Completed 引用释放通过；三个 supervisor 实际 wait 均为 0 |
| `gc-drained-replicas-final-01` | 上述真实已退出 Root4 分别升级到 6/7，原 SQL inode 不变；实际退休 307/311/337 的 6/39/16 条原 stream，整行不变；独立 unused 内容的原 GC Unknown、独立新删除和三份历史保留 |
| `gc-root7-cold-recovery-final-01` | 完整 backup、verify、新 inode restore、只读 repeat 通过；运行恢复因重叠历史追赶耗尽 Root work capacity，首个 Artifact HTTP 连接中断而失败；307 被迫清理的真实 SIGKILL 保留，绝不计通过 |
| `gc-root7-cold-recovery-final-02` | 对同一个原 pinned bundle 重新验证并恢复到新根；修复后 337 → 307 → 311 依次成为实际 leader、读取原 196655 字节并保持原业务 Unknown，三个 supervisor wait 均为 0 |
| `independent-gc-history-verification.json` | 在成功运行结束后独立只读比较三个原/恢复 SQL：4 条 GC commands、3 条 tombstones、61 条 stream retirements 原文一致，61 条原 stream 原文保留，307 原 GC outcome 仍为 null |

standalone stream 的 target 是明确标注的保留上下文 fixture，不创建共识或设备事实。
三副本包使用原真实 Raft/mTLS/Controller/SQL/File 链的 stream 和实际成员声明。

两次冷运行使用完全相同的 manifest：
`sha256:7ece55f5c52360ef19b2f784bd6d5f3e86b9acb4e476448275dd52cf0cc036b1`。
第二次不重造源 Snapshot producer，不修改失败记录，也不把重复读取记成新执行。

## 实际发现与修复

原 Append handler 等待历史内容追赶后才回复已持久化的元数据 ACK。重试重叠占满 8 个
Root work slot，连原 Controller fence/ingress 也被拒绝，child 进入 write-disabled 并退出。
现在每个 Root 只有一个独立持有的有界追赶任务；元数据 ACK 及时返回。原 sender、原
Log prefix、原队列预算和每个实际 blocking job 的 admission guard 均保留，异步取消不能
放开仍在运行的工作。已应用 prefix 的 heartbeat 不再排入无用任务。

独立实际 blocking-job 回归确认：取消等待者后，原 admission 与实际 Root writer 仍被持有，
只有实际工作结束后才能重新打开 Root。完整 byte/apply、同 voter 多数派、应用 Commit、
child/readiness 和 reader 校验继续执行。原失败 child 还留下 runtime shutdown 时的
Tokio timer panic；固定输入复验的正常退出不能替代完整 F10 fatal-shutdown 组合验收。

## 冻结身份与验证

当前基础设施 supervisor 为 `controller-replica-adr0191-final-02`，SHA-256
`f1fbd572caed118c9702efc501f56f597883f4f2ccabc6950a1eb081fc87cc90`。
原应用 binary 保持 `integration-server-adr0190-final-01`，SHA-256
`b52ff24226969eeac1f453b0e99792ffc5b3e1f3c77b2d9f1b124dc133a81039`，
故原 application policy/cluster identity 不变。完整 binary/source 身份留在各 case 的
`source-identity.json` 与 `gc-frozen-binaries-final-02.json`。

Replica 138 项完整回归、supervisor 11 项、recovery Python 178 项、严格类型、全工作区
all-targets/all-features Clippy、受影响私有 Rust 文档、Python 函数文档、站点 9 项和
strict build 通过。另一个多 item GC 回归验证首项删除回执与次项 Unknown 独立保留，
同 ID 不重做，独立 followup 分别记录一次 missing/一次实际删除；日志为
`gc-multi-item-test-final-01.log`。Native admission SQL 拒绝见 `gc-native-final-03`。

这里只覆盖已终结 qualified CAS 字节。Partial staging 清理、完整 producer/joint/fault、
rolling/rollback、跨主机及原 N1–N7/F0–F10/R01–R20 继续实施。软件存储及进程事实不提供
硬件停止、物理连续性、长期 SLO 或总体 SoftwareComplete 证明。
