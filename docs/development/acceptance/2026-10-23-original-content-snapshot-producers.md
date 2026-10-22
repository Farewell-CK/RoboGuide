# 原内容与 Snapshot：实际存储生产者接线

- 日期：2026-10-23
- 唯一开发分支：`codex/nonrelease-remediation-closure`
- 基线：`6e7f7d21cf57e7b84329ab468440aa1813fa65cc` + ADR-0159 存储接线
- 决策：[ADR-0159](../../decisions/0159-original-content-quorum-and-raft-reference-lifecycle.md)

## 实现范围

新的私有 `application-commit/v3` 将原 v2 semantic batch、原 issuing epoch、实际
application CAS writer、完整 State recovery、原 bare-event→owner/digest/size 和
完整当前 byte requirements 一起固定。Prepared 上传也保留，不等到 Completed。
历史 origin、原 pending copy/abort、原预算与 source identity 不能被新 view 改写。
新 view 保留完整原 candidate，可独立重算其 batch digest；checkpoint-only digest
不能代替它。旧 wrapper/Node/semantic v2 各自的原含义保持。

`SqliteRaftStore::open_with_content` 显式选择 storage v4，实际 member-local backend
owner/root/SQL inode 保存在 mandatory head。旧 v0.3 open/Commit 不获得新权限，
已有 v3 不隐式迁移。保留完整原未归档尾，拒绝旧独立 prefix compaction；达到容量
上限须先完成 coordinated Archive。

actual log append 先建立独立 Log pin；upstream truncate/purge 后才释放相应 log
责任。current view、published Snapshot 与每个 actual SnapshotData reader 有各自
独立引用。build/install 前检查完整实际 receiver bytes，缺失不安装；新 Snapshot
替换只释放旧 Snapshot 自身。reader 使用 actual retention-only scope，不声称其
读取了 payload。原 scope 结束或独立 original-process absence 分开记录；kill/SQL
不确定不丢 pin。stale/fork Snapshot 不回退已接受原 command/State/owner/floor。
member-local copy/reader/transfer/child authority 不进入 Raft Snapshot。

新 scopes 用 content SQL v2 / layout v0.2，原 backend identity v0.1 不改。显式升级
持有原 root/SQL inode lease、native DELETE/exclusive drain，验证原完整 heads/rows，
只增加新格式 fence；原 raw intent/receipt/ID/预算字节不改。普通 open 不自动升级，
旧二进制拒绝新 SQL。false/absent retention-only 字段保持旧 digest 编码。

## 已验证的实际边界

以下实际命令通过（第一轮证据保留于 `actual-snapshot-producer-native-01`）：

```bash
uv run python tools/quality/check_content_archive_processes.py \
  --skip-build --abort-unpublished \
  --replica-state-probe target/debug/examples/replica_state_probe \
  --replica-snapshot-probe target/debug/examples/content_snapshot_probe \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/actual-snapshot-producer-native-01
```

真实 Controller/mTLS 上传实际源码文件；probe 不生成代替生产者的 upload row。
它捕获原 SQL/CAS，再直接进入实际 OpenRaft storage/Snapshot traits。两个声明的
基础设施 member 23/31 有各自真实 store UUID，原 content owner 不替换成接收 owner。
metadata 已应用而缺 bytes 时 build/install 拒绝；真实复制/hash/fsync 后安装通过。
替换保留活动 old/current readers，stale Snapshot 在 pin/metadata 副作用前拒绝。
进程被实际 SIGKILL/wait **-9**，两个原 reader 的 Unknown/pins 留在原 SQL，分别
观察原 PID/boot/PID namespace/start ticks 后才退休，不补造 payload receipt。
读回实际 bytes、独立 View/Snapshot pins 仍在；删 mandatory complete head 拒绝。

该子流程 **10 个实际独立进程**，结果/所有 PID/wait 见
`actual-snapshot-producers/result.json`。外层原 Archive SQL rollback、原 retry 不复制、
完整 State materialization 与 v0.16 cold recovery 也通过。最终 profile/retention-only
核对收紧后，使用新目录 `actual-snapshot-producer-final` 重跑，结果以其冻结 binary/
完整 source hashes 为准。

最终 `actual-snapshot-producer-final` 与 `content-reader-layout2-final` 均通过，分别
保留 **10/42** 个原 PID/native wait。后者在结束前重查 probe、checker 和完整
复制/Artifact/State 源文件 hashes，拒绝运行中源码或二进制漂移。

实际旧内容二进制位于证据根的 `legacy-content-probe-6e7f7d2`，SHA-256
`e7bf110f41e767d3f11178420db600f2c69291981376439e9dd3c6cbc9b865f6`。
`content-reader-layout2-native-01` 以 **42 个实际进程**通过原复制、Unknown、真实
SQL final-commit 失败、独立 repair、reader kill、hardlink/live writer、实际旧 v1
创建/复制/读取/占锁/close/升级/旧二进制拒绝，原 raw history 完全不改。最终完整
source freeze 版本另用 `content-reader-layout2-final`。CI 不拿当前编译器伪装旧 binary；
没有保留旧 executable 的 runner 仅运行 fresh-layout 子范围。

复制 crate 的 **58 项**含 4 项 actual content application/Snapshot 反例，State 为
**106 项**。这些定向 storage tests 不等于实际网络 quorum。

完整 Rust workspace **1003 passed、2 ignored**，全 targets/features Clippy、348 文件
strict mypy、539 文件格式、Ruff 与函数文档通过。随后避免了 legacy 每次 apply 的
完整历史扫描，仅有实际被替换的 content view 才读取对应 head；复制 crate 整体
复测。CI 执行最终源码全仓门禁，原结果不重标为其后的同一次执行。
legacy 扫描修正后，复制 crate **58 项**全过；workspace Clippy、private Rustdoc
及站点 strict/9 项测试也通过。

## 尚未关闭的必需工作

生产 ReplicaConfig 仍只接受 v0.1–v0.3，网络 Commit/成功 ACK 仍为原 metadata 合同。
本包不启用半接线 v0.4。真实 mTLS byte/观察、同一 current/joint voters 完整 bytes
与 metadata 多数、真实原 source register/Prepared staging、leader alias/current
content readiness、完整线上 Catalog/Archive/GC、recovery v0.17 与旧部署 drain/
upgrade/rollback、Node/Mission routing 继续实施。

存储中断仍需完整原 producer operation journal 与显式对账：未知 unpublished Snapshot、
SQL append/apply 回滚后留下的 pin、目标安装结果丢失不能只从“当前没引用”推出释放。
原 issue/deadline 和 query/repair 各自独立；本包的 normal producer release 不覆盖
这些路径。receiver scope expiry 不自动释放或证明 original process end。

v0.4 尚须把实际 hash/SQL/file I/O 从网络 worker 移至受限 blocking 工作，并验证
single-worker heartbeat/epoch；新 Snapshot scope Drop 的 SQL/校验成本也在 N7
范围。Replica 原 listener 的 pre-TLS/帧内存/并发上限需要完整门禁，不能从既有
Operator listener 的容量上限推导。未完成前不能启用新的生产内容 profile。

直接 storage gate 没有启动网络共识，不能宣称实际 quorum、在线 HA、跨主机 RTO/RPO
或 Task/goal 连续性。0 Node、0 设备、0 模型、0 模拟器；无实验/E6。原 N1/N3
以及 N2、N4–N7 全部条件保持，未达到 SoftwareComplete。
