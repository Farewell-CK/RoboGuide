# 在线协调归档与原内容恢复验收（2026-10-26）

[ADR-0212](../../decisions/0212-coordinated-content-archive.md)与[实现合同](../coordinated-content-archive-v0.1.md)
接通复制 writer 的在线归档。State 保留原操作和精确 prefix，Replica 复用实际同成员完整内容
quorum，Artifact CAS 保留私有包；元数据维护不改变 Mission、Control 资源或 Node 事实。

本次创建 0 embodied Node、设备、模型、模拟器。实际 SQL、CAS、mTLS、原子事务、OS 子进程
和原 native wait 是软件证据；设备停止、跨主机、物理连续性与长期 SLO 分别保持待验证。
开发始终使用 `codex/nonrelease-remediation-closure`，完整最终运维/跨域组合另行验收。

| 实际入口 | 结果与独立证据 |
| --- | --- |
| `/tmp/rga4-native-4/result.json` | 正常归档、精确重复只读、实际丢弃一次 POST 回复后 GET 对账、继续写入、三份同 owner 包实际 hash、Artifact admin 私有读取拒绝通过 |
| 同一原 runnable 存量 | 实际 SQL 完成故障、原 child wait、显式 supervisor 重启、跨 owner 原上传读取、Prepared Unknown 原操作/只读恢复/释放和 Completed 原上传退休通过 |
| 原 Archive 最终 SQL 故障 | pending quorum 已提交、实际 CAS 已发布时拒绝最终本地 SQL；原 Unknown、checkpoint 与 floor 保留。真实原 child 排空/换主后，同 ID 只读；显式放弃保留原 intent 与 `completed_ms=null`，新独立操作完成 |
| `/tmp/rga4-cold-2/result.json` | 同一固定完整 v0.30 bundle 的备份/验证/新 inode 恢复/重复恢复及三个恢复后 leader 读取通过；原 qualified bytes、Unknown、clocks 与 SQL history 保留 |
| `/tmp/rga4-negative-5/result.json` | 独立复制包分别删除实际 Archive bytes、删除必需 journal 后重新 hash；verify/restore 均拒绝且无恢复目录发布。genuine 同 wrapper v60 旧 writer 实际拒绝，原 SQL digest 不变 |
| `/tmp/rga4-gc-1/result.json` | 原活跃 Log 引用阻止回收；实际 Snapshot/purge、原 stream/reader 退休后，各成员删除两个不再需要的真实 CAS 对象，Unknown=0；同 ID 只读、staging 回收及重启通过 |
| `/tmp/rga4-membership-10/result.json` | 原成员实际 SIGKILL/wait=-9、离线成员恢复 purged Snapshot；真实损坏内容拒绝 voter 准入且原 membership/Log 不变；原单次 joint、移除成员实际 wait=0、恢复完整三成员与原 v4 内容通过 |
| `/tmp/rga4-final-cold-1/result.json` | 成员组合后的固定完整 v0.30 bundle 独立验证/新 inode 恢复/只读重复，三个实际 leader 337→307→311 均读到原 196655 字节内容；三个 supervisor 实际 wait=0 |
| 同目录 `independent-original-gc.json` | 原 GC、producer、staging、Unknown 与全部当前 qualified CAS 独立比较通过，未修改原 bundle/receipt |

原 runnable Controller SHA-256 为
`5ade087eac0d41f7e7e6f20f3983c56b4e28f650cd064e78a68902bce2120b6b`，
Supervisor 为 `f70343b1b03044c1ad78b8b17ab300ba73fb1c690026a906ca92fca562703080`。
原 network-parent11 probe 为独立维护入口，其字节与原 binary identity 均保留。
genuine prior v60 Controller 为
`98bb684c7ec95156d19270c227240b9e277e215df67ebeb3890e134a0f8aa29d`；
拒绝原因为 `mandatory original Archive journal marker is missing or changed`，不是 wrapper 不匹配。

| 固定原数据 | manifest digest |
| --- | --- |
| 原归档 / SQL 故障 / 换主后完整存量 | `sha256:be9a638eb2acbe4e48437a3a009f723ab1c5a25ea4790606dbbf764c3c62ad13` |
| 原 producer 退休 / 实际 GC 后 | `sha256:606a36d7c4fea956c8f05b99e743b970a2791fe55a84a5061445fb444ea7f33c` |
| 实际 Snapshot / joint / 恢复三成员后 | `sha256:44c2c6f88ab8ba87b2c75eb03d10f125ae23983c2b3a494ab5cc0a6adc0f37dd` |

整批 Rust 1200 项通过、0 失败、2 忽略；恢复工具 297 项通过。严格类型检查 422 个源文件、
Ruff、函数文档、Clippy 与私有 Rustdoc 均纳入整包检查。后续 Snapshot 修复的专项结果和
最终成员/冷恢复证据在下面单独记录，不能用修复前的全量结果替代。

早期真实失败保留：实际应用端口未转发新 replication 能力、删除旧 SQL readonly view、
checker 期待未经授权的自动 child 重启，以及旧格式恢复库无 metadata 表。
后续成员组合另复现同一实际 Snapshot 在 membership/epoch 变化后永久命中原 header 拒绝。
当前 parent11 修复为每次独立真实 SnapshotBuild 保留原 member/Root/producer，并在实际
payload 中绑定该已持久化出版意图；同 cut 可有不同真实出版，但不补造 Log、应用修改或设备事实。
旧接收 ID、Unknown、pins 和首次预算不变；同一显式 build ID 保持只读。该组合已通过上述
实际成员与最终冷恢复。返回 Root 已有全部内容时，仍在原接收 issue 内核验真实字节/receipt，
省去重复 stream；缺失仍走原传输，损坏拒绝。普通空 Append 心跳不再等待同步存储校验。

成员门禁保留 `learner catch-up timed out` 原回复：不重发 AddLearner，而用真实只读 quorum、
实际 Raft applied prefix 观察后续结果；只有独立实际追赶完成才进行下一步。不能把超时
回复改写成功，也不能只凭相同 application revision 提升 voter。

本轮成员/最终冷恢复 Supervisor SHA-256 为
`0de787dc9452d89f22b4a3556841e21c78fc7bef17db1f797c57fa9eb94f3343`，
network probe 为 `8fe7396fea2e295b6c38d9e14b5f05dec2b1dd2091706e3b6dbaa24e69514927`；
原 `/tmp/rg-archive-listener-bin-8/identity.json` 与各冻结输入独立保留。

验收工具也保留自身失败：旧表名、任意 follower 投影、历史 CAS receipt 被当作当前输入，
以及两套原端口恢复检查同时运行。冲突运行不计通过；实际网络恢复检查改为顺序执行。
独立 bundle 均通过复制产生，未修改原 pinned bundle 或把原 Unknown 改成成功。

维护入口 `tools/quality/check_coordinated_archive_processes.py` 顺序执行整个模块：online →
完整冷恢复 → 原包/表/旧 writer 拒绝 → producer GC → actual member/Snapshot/joint →
最终完整冷恢复和独立原 GC/producer/CAS 检查。CI 调用同一入口；保留原失败与成功目录。
