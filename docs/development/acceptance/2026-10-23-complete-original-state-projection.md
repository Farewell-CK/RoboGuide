# 完整原 State 投影与实际进程安装

- 日期：2026-10-23
- 唯一开发分支：`codex/nonrelease-remediation-closure`
- 基线：`5e1d30787b31fa59ec5b7e52301e73c717da5ca6` + ADR-0159 完整 State 投影
- 决策：[ADR-0159](../../decisions/0159-original-content-quorum-and-raft-reference-lifecycle.md)

## 原缺陷

真实旧路径执行：

```bash
uv run python tools/quality/check_replica_certificate_processes.py \
  --skip-build --artifact-retained \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/current-replica-materialization-baseline
```

三个真实软件副本、实际 mTLS、0 Node。peer 重启后无法恢复当前 application。
`member-1-first.log` 明确报 `mandatory original Archive journal marker is missing or changed`。
原 v0.3 ReplicaView 只有 checkpoint/events/commands，不保存原 v49+ 必需的独立
Archive identity/journal。此失败保留；本包不声称旧 v0.3 网络恢复已通过。

## 实现

`core/state/src/replica_recovery.rs` 引入原 `application-recovery/v0.1` 与
`complete-replica-state/v0.1`。在实际同一 writer/SQL transaction 下捕获原 Archive ID、
zero-operation marker、完整 raw copy/abort intent/receipt、原 Catalog archive head/
envelopes、manifest history 和原 history/timestamp floor。原 nested JSON、预算和
kernel 不重写。完整 source 保留全部原未归档事件与命令；不能从 checkpoint/digest
猜回丢失前缀。逐表/完整传输均有原固定 bounds，缺失或不完整拒绝。

新显式 port 将完整投影与原 v2 semantic batch 一起交出；旧 port 不隐式获得新权限。
inactive import 在同一 SQL transaction 安装全部原记录、事件、命令和 checkpoint，
提交前验证完整读回。另一原 store ID、SQL 故障、missing source 都拒绝，不补造
新 Archive identity。旧 metadata-only API 对现代 checkpoint 在 SQL 写入前拒绝。
Controller wrapper v52、event codec v31 与 Node/Memory 语义保持原含义。

`replica_state_probe` 为独立进程入口，只捕获/检查原 metadata 或安装到新 SQL。
捕获持有明确原 canonical sibling 与 SQL inode lease；完整部署仍须另外声明/排空
所有 writer，不由一个文件锁宣称全局覆盖。actual original SQL 是 read-only snapshot。
probe 不启动服务、不复制 CAS、不创建 Node 或授予 quorum/use 权限。

## 实际证据

```bash
uv run python tools/quality/check_content_archive_processes.py \
  --skip-build --abort-unpublished \
  --replica-state-probe target/debug/examples/replica_state_probe \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/complete-state-materialization-native-01
```

本次通过的 `complete-replica-state/result.json` 保存 actual binary/capture hash、
进程 PID 和原 wait。真实 Controller/mTLS/files/SQL 产生 2 次上传、原 Archive 与
abort；捕获并原样安装 **14 条命令、3 条原 copy、2 条 abort、history floor 6**。
当前事件尾为 0，原 Catalog 依赖独立归档前缀。两端 complete identity 均为
`sha256:e5433a29d13731e6efc663ead9e5942dd7f78367df28bf6c1f1f88fed550f381`。

实际安装后的 Controller 正常启动，通过原认证读取原 upload/真实 CAS bytes，库存
仍为 0 Node；源 marker/receipt/budget 与恢复后完全相同。活动 writer 拒绝 capture，
缺原 ID 的输入拒绝且不创建 target SQL。原 Controller 使用明确 SIGTERM 后，记录
实际 wait **-15**；未将它改写为自然退出/设备停止。既有 Archive SQL rollback、
原 retry 不复制、接收字节缺失/重算 manifest 拒绝及完整 v0.16 冷恢复同时通过。

State 新增 6 项 actual SQL/Catalog 用例，覆盖完整 reopen、现代旧入口的写入前拒绝、
mandatory loss、foreign original ID、真实 SQL trigger rollback，以及显式 unit port
捕获后丢失响应/原 SQL 回滚。该 port 是标注的合同 fixture，不是真实 Raft/network
quorum。整个 State crate 106 项与复制 crate 54 项回归、Clippy 通过。

最后完整 Rust workspace 为 **999 passed、2 ignored**；全 targets/features Clippy、
private Rustdoc、346 文件 strict mypy、Ruff 与函数文档检查通过。

## 继续实施

本包只关闭独立 source capture/inactive materialization 的这处缺口。生产 v0.3 尚无
完整 projection transport；原实际三副本负例不改判。v0.4 的 source 原 owner/实际
store 注册、Prepared staging、完整私有 Commit、同成员内容多数、log/Snapshot/
reader/Archive、leader aliases、cold/upgrade/rollback 和 Node/Mission routing 都继续
实施。共用原 CAS 的静止验证不等于 cross-host content replication 或在线 HA。

N2、N4–N7 及原全部非发布条件继续保留。没有注册 Node、设备、模型或模拟器，
没有实验/E6，也没有设备停止、物理连续性或 SoftwareComplete 结论。
