# 原 staging 回收与 Root8 恢复验收

对应 [ADR-0194](../../decisions/0194-original-staging-file-reclamation.md)。本包只证明真实
软件进程、原 SQL/文件和恢复边界，创建零 embodied Node、设备、模型和模拟器。

## 问题与实现

原 copy 发布 CAS 后仍保留 staging 硬链接，因此删除最终 CAS 名字不等于释放该链接占用。
原 stream 中断也保留私有部分文件。现在成功 copy 在原预算内验证并同步 CAS 后，删除
并同步自己的 staging 名字，再提交原完成记录。失败继续保留原 Unknown。

显式 Root8 的回收命令只接受最多 64 个原 copy/stream ID。原完成或独立原进程结束、
原引用释放或完整 stream retirement 先于准入；命令和全部 Unknown item 在 payload
open/unlink 前原子提交。实际 FD/inode/长度、部分字节摘要、unlink 和目录同步先于逐项
结果。每个命令沿用自己的原时钟，旧 ID 只观察，后续独立缺失也不填旧 Unknown。
回收没有删除 CAS 名字，也没有给予全局删除、当前 use 或物理停止权限。

process v0.6、维护 CLI、recovery v0.22 显式选择新边界，原集群策略不变。旧入口拒绝新
格式。完整恢复校验全部源、退休、item 和清单，新增 backing v0.4，保留原行和旧绑定；
私有 staging payload 不传播，恢复后的缺失不改写历史。

## 证据

根目录 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

| 证据 | 结果与边界 |
| --- | --- |
| `staging-gc-tests-01.log` | 三项实际本地文件/SQL 合同测试通过，覆盖活引用、独立 CAS reader、原请求重试、整批拒绝和缺失清单 |
| `staging-gc-tests-02.log` | 非空部分 stream 清理后原 Unknown/无 CAS 可用性保持；三个 Root8 筛选测试通过 |
| `staging-gc-replica-tests-01.log` | 初版 148 项通过，不包括随后发现的已完成 stream 升级遗漏 |
| `staging-gc-supervisor-tests-01.log` | 11 项通过，Root7/8 显式选择保持原策略，缺失内容合同拒绝 |
| `staging-gc-recovery-tests-02.log` | 恢复工具 187 项通过，合成 DTO 负例只证明合同校验，不冒充实际进程 |
| `staging-gc-recovery-tests-03.log` | 45 项相关测试通过，增加 Root8 两次 backing 迁移、原 Unknown/旧绑定不变 |
| `staging-gc-replica-tests-final-02.log`、`staging-gc-recovery-tests-final-04.log` | 最新 Replica 149 项、恢复工具 188 项全部通过 |
| `staging-gc-clippy-final-04.log`、`staging-gc-rustdoc-final-02.log` | 全工作区所有 target/feature Clippy、受影响私有 Rust 文档通过；恢复工具和两个门禁 strict mypy/Ruff/函数文档检查通过 |
| `staging-runnable-native-final-01/` | frozen final-01 的真实三副本 Controller、HTTP、CAS、原 child SIGKILL/wait/restart、跨 owner 读取、Prepared recovery/原 Unknown/放弃与 Completed retirement 组合通过；三个 supervisor wait=0 |
| `staging-gc-native-final-01/` | 实际部分文件、SIGKILL、SQL 结果失败已通过，随后旧数据升级因遗漏 Root8 stream-receipt 版本检查失败；保留全部原进程和已提交状态，不算整门禁通过 |
| `staging-root8-stream-counter-01.log` | 新增完成 stream 的原数据回归重现上述开发中遗漏；原失败保留 |
| `staging-gc-tests-final-04.log` | 修正后四项 Root8 筛选测试通过，包含完成 stream 和活引用原样跨升级 |
| `staging-gc-native-final-02/` | frozen final-02 完整门禁通过；原旧 copy 的真实硬链接、当前独立 CAS owner、删除前 SIGKILL、删除后 SQL 结果失败及原 Unknown/只读重试；实际 copy/stream 均受 SIGXFSZ 终止，原 wait=-25，分别保留 1048576 字节部分文件后经独立原进程结束/退休回收 |
| `staging-recovery-native-final-01/` | 首轮 backup/verify/restore 均返回 0，随后独立检查发现私有 staging 被旧通用目录复制带入；该完整门禁失败，原包和日志保留 |
| `staging-private-scope-counter-02/` | 收紧后的新 v0.22 校验拒绝上述原失败包，不把历史文件缺失解释为原 cleanup 完成 |
| `staging-recovery-native-final-02/` | 完整新备份/校验/恢复、原 SQL 行与 Unknown 比较、私有目录为空、原入口拒绝和三个 leader 读取通过；实际 leader 顺序 337 → 307 → 311，三个 supervisor wait=0；正确声明/摘要的私有文件 verify/restore 均拒绝且无恢复目录发布 |

最后一项还保留同一已排空 Controller 来源的实际升级：307 的前次升级已提交 Root8，
本次不回退其格式，使用原已释放 copy 的真实 staging 继续；311/337 从原 Root4 经
Root6/7 升级。三个 Root/SQL inode、逻辑身份、原业务和预算保留。CI 已配置使用新建来源和
pinned `e0b1ac05` 的真实旧写入器执行正常排空升级；实际旧写入器拒绝 Root8。

新包 manifest 为 `sha256:13b6cb9e2acf6db3401a4f84d6a84239338b29955fc25501f022460f6aca6951`。
原成员 307 的一个 staging item 仍 Unknown：源保留一个真实私有文件，恢复后私有文件
为零且原行不变。311/337 的原 Deleted 也不改写。旧格式保留原目录解释；本次新恢复
边界不为历史旧包重写格式，也不把文件数量当全局删除证明。本页不关闭整个 A2。

当前 frozen final-02 来自 exit=0 的 locked build：

| 二进制 | SHA-256 |
| --- | --- |
| content-stream-probe | `6bcb0cc6f26dac438f2e9db90eb5955a178d8cd8caf921252356c15d3f442036` |
| content-network-probe | `a4790ec7449f86b7692a1a55d4807f9a2cbe8d2197d45bb845eb68b97232c6f7` |
| controller-replica | `963c8904c7f591336422afb8942c8ebd07e4503eabcc56416013babec37beb15` |

原 source probe 为 `content-store-probe-adr0191-final-02`，原旧 copy writer 为
`content-stream-probe-adr0192-0193-final-08`；各 case 固定完整源/二进制摘要，
不把较早构建冒充 final-02。原 Controller 仍是未改动的 `integration-server-adr0190-final-01`。

维护入口为 `check_replica_staging_gc_processes.py` 和
`check_replica_content_recovery_processes.py`。完整 producer 退休/最终 CAS 回收、历史
归档源、滚动升级/回退及其余 A1–F 项按唯一整改队列继续；本包不宣告 SoftwareComplete。
