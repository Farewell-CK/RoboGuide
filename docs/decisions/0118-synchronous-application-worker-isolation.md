# ADR-0118：同步权威工作与异步网络线程隔离

日期：2026-10-20。状态：Accepted；证据见
[单 worker 与重复 transfer 验收](../development/acceptance/2026-10-20-worker-and-transfer-liveness.md)。

实际三副本恢复的早期轮次出现 Artifact HTTP 中断，完整 C 路径通过，但这不关闭可靠性
调查。代码另明确存在同步 timer、Node fact 和 Artifact authority/SQL 在 Tokio async
worker 内直接等待 writer/SQLite/共识 fence 的路径。原只包围文件 fsync 的 handoff 不足以
保证等待 writer 时还有网络/lease/readiness worker。不能以增加线程或重试造出稳定性。

复核另发现 leader transfer 的临时 heartbeat/election suppression 在成功变为 follower
后不恢复，且取消或部分 timeout 也可能遗留。原 source 再次成为 leader 时可能不能维持
idle heartbeat。每个 source 增加串行操作 guard，临时 suppression 用 Drop 在成功、错误、
deadline 和 future cancellation 全部恢复；只有原 Raft 规则管理新 epoch/quorum，不创造
执行或资源权威。重复转回及等待目标 writer 时取消有实际 SQL/socket/election 回归。

复用原 `run_synchronous_transaction`，在 multi-thread Tokio 进入完整同步事务/有锁查询
前用 `block_in_place` 归还网络 worker；普通同步/current-thread 测试保持明确同步行为。
原 timer/fact 输入顺序、实际 writer、TLS witness、私有 candidate、SQL/共识 Commit、ACK
与 outbox 顺序不变。只读查询仍按当前 epoch/授权校验，不授予恢复或绕过原 writer。

Artifact 的 route admission/audit、完整同步 upload/publication/observation 以及每个实际
stream chunk 的授权+写入作为完整同步区；不只在已经占锁后包文件调用。异步 body 和
socket read/write 留在网络层。Node batch 完成 ACK 仍在原 apply 成功返回之后，timer
不并发积累新批次，不因 worker handoff 改来源 receive time 或续期 deadline。

Replica process 允许显式、1–64 范围内的 `TOKIO_WORKER_THREADS`，该环境与实际 binary
一起进入既有 application policy digest。限制线程只供部署资源配置与实际验收；不能以
此声称 SLO、设备硬实时或未知 workload 容量。旧二进制拒绝未声明环境项，不隐式改变
旧 profile。协议、wrapper、事件和 Node 合同不因执行线程边界升级。

验收须用单个实际异步 worker、实际 held writer 与 TCP/readiness 证明网络还能运行，
并用实际 Controller/Node/file/CAS/revoke/restart 路径证明正常权威链未变。早期 HTTP
中断的具体因果仍须独立证据；通过 worker 隔离测试不自动宣称那两个历史故障已解释。
