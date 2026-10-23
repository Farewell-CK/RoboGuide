# 2026-10-23 Snapshot 原 reader 关闭

分支 `codex/nonrelease-remediation-closure`，前置 `90edd74`。
[ADR-0165](../../decisions/0165-bounded-snapshot-reader-closure.md)只调整实际本地 SQL
关闭的工作位置与生命周期；SQL/Root、Node、wrapper、原预算及接收/读取权限不变。
证据根目录 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

## 原反例与当前结果

`snapshot-release-baseline-01.log` 在旧代码上真实失败：一个 Tokio async worker、
真实 Artifact bytes/Root/原 reader、实际 SQLite exclusive connection 与独立 Python
TCP client。旧 Snapshot Drop 同步写 close receipt，阻塞 `1.001584049s`，独立客户端
超时并实际 wait 1；测试进程 exit 101。metadata buffer 是明确的本地合同 fixture，
没有伪装为已接受的 Raft Snapshot，也没有建立 Node/设备/全局 quorum 事实。

`snapshot-release-fixed-01.log` 的同一窗口中 Drop 约 `115.368µs`，独立 TCP 约
`2.314ms`，client 实际 wait 0；后续独立检查原 reader pin 在真正 SQL receipt 后退休。
后续复测约 `133.086µs` / `2.363ms`。这里只证明该真实阻塞窗口中的网络进度，不声称
长期 SLO、硬实时或所有 CPU/OS/网络争用下的时延上限。

每个原 root 只有一个 scheduled/running blocking drainer。原 scope 由唯一所有权移入
原 root 队列，原 store/SQL 租约随 queued/active work 存活；4096 个原 reader rows 的
既有上限同时限定 pending entries。空 scope 不排队；不复制、重开或续期原 reader。
正常无 Tokio 的同步 Snapshot 仍在 Drop 返回前完成原同步关闭。

原 receipt 仍为 retention-only、零文件/字节、`complete=false`，不是完整内容读取
证明。队列排空、等待返回或取消都不替代原 receipt；publication error、丢失或中断
保留 Unknown/pin。generation guard 只清理自己的内存队列，不能借旧 worker 取消新
工作；Tokio blocking task 保持独立生命周期。没有变更任何设备停止/安全合同。

## 实际检查

`snapshot-closure-replication-tests-01.log` 的 76 个副本 tests 通过，另新增同步兼容
test 在 `snapshot-closure-sync-test.log` 通过。取消等待检查确实先占住唯一 blocking
worker，让两份原 close work 排队；取消 observer 后原 receipt 仍缺失、原 root 不能
重开。释放 worker 后，实际原 receipt 完成、原 request digest 不变，才允许重开原 root。

失败隔离使用两个真实独立 roots：一个实际 SQLite exclusive lock 使它自己的发布
失败并保留原 Unknown/pin，另一个 root 的原 receipt 正常完成。最早检查曾使用单 root
的 SQL trigger；该 root 的原 schema fence 正确封住全部写，两个 scope 都拒绝，因此
它不能用于宣称单 root 的部分失败隔离。原失败保留，正式检查使用真正独立的 roots。

原 Snapshot 生命周期测试/探针现在等待实际 worker，再独立检查原 SQL 与 pins，
没有把等待方法或 JSON `closed` 当作内容/GC 权限。Ready 的 transient close 同样
先结束，仍实际持有的 old/current Snapshot readers 继续保留其各自责任。

`snapshot-closure-archive-01/result.json` 及其嵌套结果通过原实际进程矩阵：Root3 原
producer journal、独立 Snapshot readers、真实失败/Unknown/重启/原 process absence、
Snapshot install/旧版本拒绝、完整 State 接管、实际 CAS/Archive SQL rollback、未发布
copy 清理及 complete cold restore。对应 Snapshot probe SHA-256：
`a4fbcbd6245c9c5df20ada1360aaf80e054df0482f8ddf4fa7abaa729353e0d1`。
原意图、预算、receipt、owner 身份与实际 native waits 保留；旧二进制不可用项明确为空。
这些 gates 均为 Nodes/devices/models/simulators 0。

当前生产代码的全库 Rust 1033 通过、2 忽略；此后单独新增同步兼容 test 通过。Clippy/
all-targets/all-features、私有 Rustdoc、fmt 与中英文 strict MkDocs 按最终源检查。
没有为该变化运行模型、仿真、实验或设备场景。

## 剩余范围

前置 `90edd74` 的远端 CI `39901058881` 与 Documentation `39901058877` 均成功。
此前 `3906e66` CI 的真实 target-catchup setup 问题和原日志保留在上一个验收，不将
失败改写为成功。当前新增关闭实现仍须核对自己的远端 CI。

这里没有启用 production replica v0.4，未完成同一 current/joint voters 的 bytes+
metadata ACK、在线内容复制/catch-up/readiness、原 child/完整升级或 Node/Mission
leader routing。它们及 N2–N7、固定 deployment matrix 仍是必需项；当前不是完整 HA
或 SoftwareComplete，更不是物理连续性/设备停止证明。
