# 精确 reader SQL 校验与冷恢复验收

状态：本切片的软件专项、实际入口、SQL 故障和三 leader 冷恢复通过；完整 A2–F 继续。
设计见 [ADR-0196](../../decisions/0196-exact-managed-reader-validation.md)。同一
`codex/nonrelease-remediation-closure` 分支的原基线为 `20b69b2f`。

## 实现与边界

`content_store/validation.rs` 只在原连接完整 witness、实际 SQL headers、精确写入数
和 Commit 后完整 reader/pin/head 行一致时延续进程内校验。admission 为四次写入，
closure 为两次；额外 trigger/cascade、外部或自身其他写入、DDL、失败、rollback 和
reopen 均不能借用。实际内容哈希、原 inode/restore binding、权限、预算和 GC 仍逐次
检查；持久历史、原 Unknown 和存储/Node/恢复代际保持。

六项实际 SQLite 回归包括旧历史删除、真实额外 trigger 写入、query-only 导致 close SQL
失败，以及外部/自身/header/事务/reopen 拒绝。真实 FD 关闭但 SQL 失败仍保留原 Unknown
和 active pin，重复原 ID 不会再打开文件。全部 155 项 Replica 回归通过；受影响三个
package 的 all-targets/all-features Clippy 和 workspace 私有 Rustdoc 通过。

## 实际进程与完整恢复

证据根：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

| 冻结二进制 | SHA-256 |
| --- | --- |
| `controller-replica-adr0196-native-01` | `7f61695c6ffb5cb6f5dae11aa472a410e3834446a68b384f69e19f5f2ac01525` |
| `integration-server-adr0196-native-01` | `a24716da1d0ec12360c69dc7813ee4c3e59f839beadb77b4bf7c048aa4dc5797` |

`reader-witness-runnable-native-01` 保留原 archived floor 82/checkpoint 102/77 个命令，
真实 TLS/身份、普通上传与三成员实际字节、原 child SIGKILL/wait/同 epoch restart、跨
owner 读取、Prepared 接管、原 SQL completion 失败、Unknown 只读观察、assessment、
真实 restart、精确原 release 和 Completed retirement 均通过。三 supervisor 实际 wait 0。

首次接管实际 96.102 秒，SQL 故障路径 115.013 秒；上一轮相同维护场景分别为 140.975
和 158.110 秒。它们是独立运行、不同实际 Root/进程的单次观察，不能解释为受控因果
benchmark、长期吞吐或 SLO 达标。原失败记录和首次预算保持。

`reader-witness-recovery-native-01` 对完整新存量实际 backup/verify/restore，固定 manifest：
`sha256:b86f921a28637039797532eaf1e89d2456a550a6da27674b825991c542e3ca18`。
原 source SQL/CAS writer 租约在服务运行时被独立持有，真实 inspector 拒绝重新读取；
恢复后三个 leader 按 337 → 307 → 311 完成内容/原 Unknown assessment 六次读取，
所有 supervisor 实际 wait 0。独立核对 `independent-verification.json` 再次直接读取 SQL，
保留全部原 initialization raw rows、policy/head 和 anchor，并重新哈希各 Root 当前所有
必需字节。缺 mandatory initialization head、错误旧 profile、私有 staging payload 等
恢复反例仍拒绝。

当前门禁新建零 embodied Nodes、设备、模型和模拟器；原归档源仍含既有软件 Node 历史。
这不关闭完整 producer/全局 Archive 生命周期、滚动升级、其它 A2–F 或物理连续性。

## 远程检查修正

原 `20b69b2f` 的 [CI 40106759428](https://github.com/Farewell-CK/RoboGuide/actions/runs/40106759428)
在编译固定 parent8/Root8 旧探针时失败，Python 与 Documentation 已通过。失败使用了
之前 pre-Root8 构建的同一 Cargo target 目录，编译器报告的依赖接口缺少旧来源本身已
存在的 `open_with_staging_gc`。本包为两个历史来源使用独立 target 目录，避免历史构建
缓存混用；该修正的远程结果另行核对，原失败不改写。

后续 `f8ec309a` 的 [CI 40110479120](https://github.com/Farewell-CK/RoboGuide/actions/runs/40110479120)
及 [Documentation 40110479005](https://github.com/Farewell-CK/RoboGuide/actions/runs/40110479005)
均已实际成功，包含固定旧 parent8/Root8 writer 的独立构建与原完整进程门禁。
