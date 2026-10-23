# 原 GC SQL 的显式恢复与中断验收

- 日期：2026-10-23
- 唯一分支：`codex/nonrelease-remediation-closure`
- 基线：`7c3219a9adf855c9c07fa0ab60f5feb0399ec2e5`
- 决策：[ADR-0162](../../decisions/0162-explicit-artifact-gc-sql-recovery.md)
- 证据根：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`

## 原问题与实现

`artifact-hot-journal-baseline-02` 中实际 owned SQLite 在 Root writer 下产生真实
uncommitted/cache-spill/hot journal，实际 SIGKILL/wait **-9**。Controller 的只读
启动报 `READONLY_ROLLBACK`，native wait **1**。修正备份 destination 后的独立
`backup-observation.json` 同样记录实际只读拒绝，原 SQL/journal digest 均不变。
初始备份因 destination 在源内而拒绝，明确不算 hot journal 证据；baseline-01 的
诊断进程锁路径错误也保留，不计通过。

新增显式 `recover-artifact-journal` 与只读 `observe-artifact-journal-recovery`。
完整实际 profile writers、全部已知 Controller、原 owner/digest 与 DELETE-only
输入在源读写前核对；只有真实 `READONLY_ROLLBACK` 可以进入原生恢复。
`HeldWriters` 保留原 descriptor/path/inode 与实际 flock，正常 recovery 的完整验证
保持，只有实际持有的目标可以暂缓 GC SQL 语义读。

原 intent/随机 ID、实际 caller/process/namespace/kernel/不可续 deadline 及完整
SQL/journal forensic copy 的 fsync 先于 writable SQLite。原生 exclusive/integrity
和原 GC 全记录/heads/owner 复核后才有完成 receipt；缺历史不重建。同目录重试只
观察原结果，明确新 repair 使用新 ID，旧 Unknown 独立保留。管理 forensic 文件
不授予应用、内容、GC 或恢复 authority；按原 `files` component 可显式随 cold 保留。

## 实际检查

`artifact-sql-native-verified-final/result.json` 的 **10/10** 项通过：normal、guards、
SQL contention、copy failure、receipt failure、expiry、source replacement、SIGKILL、
missing history、actual WAL。逐项保留 source/binary identity、原命令、PID/native wait、
stdout/stderr、SQL 原行、真实原内容、intent/partial/receipt；不覆盖失败目录。

所有案例先由真实 mTLS 上传、完整原 receipt 退休及普通 `gc-artifact` 生成非空原
GC command/tombstone，并保留另一份实际内容。没有插入替代 Completed GC row。
实际低层输入故障通过未提交 SQL 事务构造，并明确保留其独立诊断进程身份。

- 原 GC rows、原 Kernel/预算/receipt/tombstone 与原 SQL bytes 在正常恢复后完全
  相同；保留内容实际读取成功，原已删内容仍不可用。
- 实际 Root writer、不同 hardlink 名的 Controller SQL inode holder、缺已知 consumer、
  wrong digest 与 symlink 都在 effect 前拒绝。Actual WAL mode 的转换也保持原行，
  后续恢复在 DELETE header 门禁拒绝。
- 实际 SQLite POSIX lock、真实输出目录/receipt collision、源 SQL inode 替换、
  原进程 SIGSTOP/实际预算到期/SIGKILL 保持原 Unknown。原 directory 的 retry
  writable SQL opens 为 **0**；明确新的 repair 使用不同 ID，旧 intent/预算不改。
- receipt 写入失败后，即使当前 SQL 独立可读，原操作仍是 Unknown。缺失 committed
  `gc_commands` 表在 rollback 后也不补建，不发布完成 receipt。
- 完整 v0.17 backup/verify/restore 包含原 Controller、实际 CAS 与显式 forensic
  files；恢复后原 GC rows/证据完全相同，真实 Controller/mTLS/原内容继续可读。

完成记录也采用闭合字段/原 profile/原 kernel/实际输入与 forensic bytes 核对；实际
修改 `original_gc_semantics_verified=false` 的 receipt 不能显示 Completed。

矩阵共保存 **29 个 Controller incarnation、65 个其它实际进程**，其中 **10 次**
实际 native wait -9。Controller SQL inode holder 是独立文件诊断进程，不称为
Controller/Node 或设备停止。全部新建 Node/device/model/simulator 为 **0**。

独立先行样本 `artifact-sql-original-native-01` 和正常 full/cold 样本
`artifact-sql-native-normal-01` 同样通过。受影响的现有实际文件执行回归在
`/tmp/rg-gc-memory-20261023` 通过，其 105 个原 regular files 另保存于
`gc-recovery-memory-processes-archive`；证据复制不授予运行权威。回归使用声明的
软件进程 Node、实际文件/SQL/TLS/
独立计算；不制造设备事实。Recovery tests、strict typing、格式、Ruff、函数文档
及 Python 全量回归、354 文件 strict typing、格式/Ruff/函数文档、MkDocs strict
与站点 9 项检查通过。远端 CI 的观测因 GitHub API TLS timeout 暂不可用，不推断
其成功或失败。所有结果分别留存，不由单个矩阵代替其它阶段证据。

## 完成边界

本项关闭既有 v5 GC hot journal 的显式本机维护入口。Artifact v5、GC SQL v0.1、
Controller wrapper v52 与 Node 合同保持。外部 forensic 不是可回放的新应用权威。
在线副本内容多数、原 producer/legacy adoption、supervisor replacement/child 迁移、
剩余 listener/I/O、P2/P4–P7 与原所有非发布完成条件仍必需；尚未 SoftwareComplete。
实际文件/进程不建立设备停止、跨主机 RTO/RPO 或物理连续性。
