# 2026-10-23 原有界内容流后端验收

唯一分支 `codex/nonrelease-remediation-closure`，前置 `700d802`。
[ADR-0168](../../decisions/0168-original-bounded-replica-content-streams.md)先固定原接收
责任，再实施 explicit content SQL v4/layout v0.4。证据位于
`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

## 实际行为与范围

原 receiver/完整 target/qualified source/实际 writer/kernel/process/预算与独立 pin
在 FULL SQL 事务先提交，随后才建 staging、打开源或读取内容。唯一 worker 保留实际
root/SQL/FD、八个实际 slot 的容量及不可续预算。每块最多 64 KiB；完整 size/hash、
fsync、同 qualified namespace 的原子 hard-link publication 与目录 fsync 先于原
SQL receipt。已有文件也独立检查全部 bytes，并逐块检查原预算和 live permission。

错误使原 writer 永久拒绝继续；原记录、partial 和 pin 保持。原 worker FD 已关闭、
原 within-budget receipt 和原进程死亡分别记录/观察。同 ID retry 只返回原观察，
即使源载荷已不可打开也不会重新读取或生成新的回执。当前 stream pin 仍独立保留，
未由 scope close 或 deadline 推导全局 GC 许可。

显式 v3→v4 必须先排空原 writer/native SQL；保留 identity/inode、旧 copy/producer/
reader 行与原 heads。新 stream 空 journal/head 独立添加，旧二进制在零新操作时也拒绝。
本地 blocking API 只有实际文件 owner 许可；声明的 target 不证明真实 committed
membership。网络 mTLS/Ready、原 child 已发布/Prepared 读能力、Root7、同一 current/
joint voters 内容+元数据 ACK、readiness/routing、stream retirement/GC 和 recovery
仍须接线。普通部署内容 v0.4 尚未启用。

## 原进程与旧历史

`content-stream-native-04/result.json`、`content-stream-native-upgrade-01/result.json`
与最终 `content-stream-native-final/result.json` 通过。最终原 **30 次 actual waits**
包含两个 SIGKILL -9、20 个成功 0 和 8 个预期拒绝；每个拒绝均核对具体原错误。实际 2,097,159 bytes 文件由原 Artifact SDK 发布；真实 Linux 文件 write lease
阻断其打开，实际 SIGIO 29 与独立 readonly SQL 确认原接收意图已经落盘，随后取得
实际 SIGKILL wait -9。原 SQL 逐字节保持 Unknown，原部分文件和 pin 不被删除；同 ID
在源载荷隐藏时仍仅观察，独立新 ID 可以修复实际 bytes 而不改原 Unknown。

另一原进程经实际 SIGSTOP wait 19 停到原内核预算到期；继续后拒绝效果，原预算不续、
无新 receipt，实际 scope close 单独落盘。独立 fresh receiver 的文件 publication
完成后，真实 SQL 写锁使 receipt 与 close SQL 失败；实际 full hash 的文件存在，
但产品没有 metadata receipt，独立 query 判为不可用。之后新 ID repair 发布，原
Unknown 原样保留。已提交进程在父调用方消费最终结果前被杀，原 receipt 仍在，重复
ID 在源文件不可打开时保持完全相同原结果。wrong owner、actual live writer 和缺失
mandatory head 均拒绝。

旧 `content-snapshot-probe-adr0167` 真实读取已验收并已 reap 的原 Controller/CAS，
实际创建原 Log、View、Snapshot 和两个 managed readers。持有原 reader 的旧进程
阻止升级；其实际成功 wait 后升级，原 raw rows/heads、SQL inode `56697363` 和
原 owner 不变，新 stream history 为空，旧二进制拒绝新格式，原 bytes 可独立读取。
最终独立运行的旧 SQL inode 为 `56697453`；前述 `56697363` 属于 upgrade-01。
这不把未存在的历史 stream 或预算回填到旧记录中。

冻结新 stream probe SHA-256 为
`40ecdf7df3c5d5f901b8321781d728d5018e342839a19f64eee667655b58e423`；
旧 producer probe 为
`32c42d2bb033ecbb11734dba03546dadef051d4f2e38f8ba409f2f38ac03ba0b`，
实际原 source/old metadata probe 为
`3389d3bb9e76894d639862fa7855f2514a80fcb465da1bc79432a77c5c88f059`。
所有 actual argv、native waits、源码/二进制 identity 与 original SQL 保留在原目录。

前三次本地失败均保留：第一轮 probe 的 tagged-enum 数字 key 解析在接收前拒绝；
第二轮 readonly poll 遇原 native SQL drain 的 BUSY；第三轮缺 head 正确拒绝但诊断
只返回 Query returned no rows。现分别显式解析完整 target、仅在原 deadline 内处理
readonly BUSY/LOCKED、报告缺失 mandatory head；未重试 mutation 或放宽预算/拒绝条件。

## 代码验证

7 个实际 SQL/FD/CAS Rust 测试通过，覆盖 chunk order/错 hash/源权限撤销、真实 SQL
故障、八个 held worker 容量/原 ID 观察和显式升级/丢 head。workspace Clippy 的全部
targets/features 通过。全库 Rust **1052 passed、2 ignored**，全部私有 Rustdoc/fmt、
strict mypy **360 files**、维护 quality 的 **79 files** Ruff/format/函数文档、
网站 **9 tests** 与 strict MkDocs 中文/英文构建均通过。

本 gate 为零 embodied Nodes、零设备、零模型、零模拟器；目标配置明确不是实际共识。
它不证明网络 ACK、原 child 接管、跨主机恢复、物理连续性或完整 SoftwareComplete。
