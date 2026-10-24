# 原 producer 退休与最终成员 CAS 回收

状态：158 项 Replica 回归、实际引用/回收/重启及完整三 leader 冷恢复通过。
设计见 [ADR-0197](../../decisions/0197-complete-original-producer-retirement.md)。开发仅在
`codex/nonrelease-remediation-closure`，原基线为 `f8ec309a`。

## 权威与实现

上传退休只释放原上传的保留责任。Log/View/Snapshot、stream 和 reader 各自保护同一
qualified key；原操作过期或上传已退休都不能代替它们的释放证明。闭合的本地维护入口
新增原 reader 退休，只委托 Root 自己检查原 kernel/process incarnation，不接纳调用者
自报停止。`content_network_probe` 显式传递恢复 binding，并调用维护中的真实 Core API。

长历史的逐条对账反复全量校验 Root。当前在原 parent writer/mutex 下冻结完整观察，在
同一 Root guard 内保留逐条 FULL 事务和前后完整校验。失败保留实际部分进度，原 intent、
publication、Unknown、进程与预算不改写；缺后续操作不会抹去先前已提交的独立退休。

更关键的故障在于 OpenRaft 核心循环会直接等待 `RaftLogStorage::purge`。原实现将后续
Root 退休也放在此等待里，造成真实 epoch 丢失。`store_prefix.rs` 现在将实际 Log 删除与
purged 指针的 FULL Commit ACK，和后续 Root 引用退休拆开。每成员最多一个合并的 owned
任务，继续持有实际 parent/Root writer；只按不可回退的真实 cut 退休原 Log 引用。
完整 parent/head/Root roster 核对后释放 parent mutex，再写 Root。其他类型及 truncation
保留原路径。高层维护等待实际清理，失败保留 pin/Unknown；不把排队标志当作持久回执。

真实 SQLite `BEGIN EXCLUSIVE` 反例证明：Root 不可读时，Raft metadata ACK 仍在一秒内
完成，Root 清理返回实际失败，原 Log pin 和原记录不变。后续真实对账才释放引用。
另有实际 purged cut 回退/换 identity 拒绝、批次部分进度与全部原生命周期回归。

## 固定输入与实际失败

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。
`complete-producer-ready-source-native-01` 对实际三 leader 冷恢复后全部已物化存量再次
执行完整备份，122 个文件、27291019 字节，固定 manifest：
`sha256:892bcabba15ac0cd45a109f3577b9aba8b9cde59a6c4765465ad3b39558454f7`。
原 Source/Archive/初始化、命令、Controller 进程历史和 TLS 均保留。

实际失败均保留：

- `complete-producer-gc-native-01` 的 Snapshot 已发布、purge 已提交，但 15 秒等待耗尽。
- `native-02` 的落后成员产生真实历史 stream Unknown，文件存在而原 object receipt
  未提交；清理时该成员实际 SIGKILL/wait -9，其余两个 wait 0。
- `complete-producer-unknown-source-native-01` 的完整备份正确拒绝 active 缺原回执，未发布
  有效 bundle。首次 CLI 参数错误另有原记录，不能算存储故障或备份通过。
- `native-03` 在原 Root/parent 的真实双 writer/inode 锁下保存 `forensic-only` 原文件，
  它不构成有效恢复包。原不确定启动暴露 pre-Raft 回放等待内容而网络未开放的循环；
  此问题仍开放。该次首个进程缺实际 wait 结果，不能写成正常退出或停止证明。
- `native-04` 在完整物化存量再次暴露 15 秒预算耗尽。新门禁首次请求显式声明 30000 ms，
  原 Core 已支持该时长；旧请求缺字段仍保持 15000 ms。
- `native-05` 的首次 30 秒维护在约 19.96 秒丢失 epoch，证明仅改等待预算不足。
  前缀 ACK 与 owned 清理拆分后，真实锁回归及相同固定 bundle 的后续维护通过。
- `native-06` 已完成三成员维护、每成员实际删除两个 CAS 对象、全部独立引用为空及
  staging 清理。其驱动遗漏 supervisor 的原 transfer 对账，后续重启被原 pending fence
  正确拒绝。新驱动在实际目标仍为 leader 时调用正常 target/source 原对账，保留精确新
  linearizable epoch；不会把触发选举直接当作完成。

## 最终进程与恢复证据

冻结实际二进制记录在 `complete-producer-frozen-binaries-05.json`：

| 二进制 | SHA-256 |
| --- | --- |
| `controller-replica-adr0197-native-04` | `860196cc02926db05d9800525bcb5c19c45aaf98e160d4897ef89ac124f45cb9` |
| `content-network-probe-adr0197-native-05` | `558073e2471544dd2b7b5413006d28b5c05549ecb1630944aad6792e43e377ee` |

`complete-producer-gc-native-07` 使用上述完整固定输入，三个实际 leader 的 Snapshot/purge
分别约 15.97、13.81、16.07 秒，均保持有效 epoch。每个成员的目标引用均为空，实际
删除两个不再需要的 qualified CAS 对象；重复原 GC ID 返回完全相同的原结果，全部
staging 项无 Unknown，当前必需字节和回收后重启通过。两轮实际 infrastructure 进程
各有三个原 wait 0；78 次实际维护调用完整保留。首次整体预算及原 history/Unknown 不改写。

回收后完整 backup/verify 的固定 manifest 为
`sha256:799d812c4f1e3474c728d423128b08bf3cb8cf72feb6badc7bb5d6dbec894952`。
`complete-producer-gc-recovery-native-01` 的首 leader 读取已通过，但复用了存量中的原
transfer ID，被实际 owner/target fence 拒绝。检查器为每次独立恢复的原工作流绑定固定
ID，保持同 ID 不重发规则；旧失败不改写。

同一固定 bundle 的 `complete-producer-gc-recovery-native-02` 完成完整 verify/restore/
重复 restore 只读核对，三个实际恢复后 leader 按 307 → 311 → 337 完成内容与原 Unknown
assessment 的六次读取，所有 supervisor 实际 wait 0。原 Source SQL/CAS 在服务运行期间
被独立持有 writer/inode 租约，实际 inspector 拒绝重新读取来源。
`independent-gc-recovery.json` 再次直接核对原 GC/tombstone、全部 staging 命令/逐项记录
和原 producer 的 intent/已存在回执/时钟；已删除 key 无 active refs、无物理文件，当前
所有必需 key 重新哈希一致。固定 bundle 的 staging 比较依据来自其原 SQL，而非旧来源
或恢复后的自我比较。

受影响包 all-targets/all-features Clippy、workspace 私有 Rustdoc、Python Ruff/格式/
函数文档/严格类型及 193 项 recovery-tool 测试通过。远程检查在推送后独立核对。

当前门禁创建零 embodied Nodes、设备、模型、模拟器；原来源保留已有软件 Node 历史。
本包只证明成员本地 CAS 生命周期，原 Artifact/Archive/备份各自保留责任仍独立；不会由
局部 unlink 推导全局擦除、物理连续性、长期 SLO 或 SoftwareComplete。原不确定启动、
滚动升级以及 B–F 必需项继续执行。
