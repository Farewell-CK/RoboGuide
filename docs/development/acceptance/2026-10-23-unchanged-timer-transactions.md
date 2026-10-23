# 2026-10-23 严格无变化 timer 事务验收

唯一分支 `codex/nonrelease-remediation-closure`，前置 `4a7be84`；该前置的
[CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39966527259)和
[文档构建](https://github.com/Farewell-CK/RoboGuide/actions/runs/39966527227)已成功。
先接受 [ADR-0173](../../decisions/0173-unchanged-timer-transaction-elision.md)并同步 V2，
再修改 State/application timer。原证据位于
`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

## 事实与合同

真实 runnable 内容日志显示空闲 timer 会复制精确相同的完整 checkpoint。
`unchanged-timer-old-counterexample-01` 使用原冻结 ADR-0172 产品二进制独立复现：
真实 **5.0018 秒**观察中，事件序号保持 **0**，原完整 checkpoint SHA-256 保持
`a204ff3a71ca9343e2cf1ebe285a776204afc335be56003146e4c50d4bedada3`，但实际 local
journal 和 parent revision 都从 **1 → 2**。检查因此按预期失败；原 `result.json`
仍为 `passed=false`，单独 `counterexample-verification.json` 只确认这个反例。
三个原 supervisor 均实际 wait **0**。它没有启动 embodied Nodes/设备/模型/模拟器。

State 现在只给内部 timer 提供保守 rollback：原 `BEGIN IMMEDIATE` writer/quorum
检查先行，真实 connection 的 total_changes 必须与本轮 BEGIN 和上次完整 checkpoint
Commit 的原取样同时匹配；`PRAGMA data_version` 也必须匹配。后者在原 writer
transaction 内取样，不能把提交后别的 connection 写入纳入旧 witness。
重启时没有已确认基线，先走正常完整 Commit。事务前的 Archive/辅助写入、外部
connection 提交及已回滚 write 均不能由相同 checkpoint 文本掩盖。

原事件序号、pending checkpoint/command 和完整 checkpoint 的 schema/序号/精确
JSON bytes 还须全部一致，最后独立核对真实 SQL checkpoint 与原 cache 及 journal。
只有全部不变才 rollback，不创建新 command/revision/复制。不同 raw number 表示也
不合并；无 domain event 不代表无变化。错误保留原 write-disabled 处理。

外部与显式零事件命令保持原 journal/幂等/Commit。原 committed outbox、Memory
prepare/use/import flush 和 fresh clock/full delivery fence 仍在原位置执行；本轮
没有新 Commit 不能取消已有投递或跳过 stop/revoke。源码归属
`core/state/src/timer_transaction.rs` 与 `apps/integration-server/src/application/timer.rs`。
Domain/Node/Memory/storage schema、原期限、Unknown、receive time 均不改变。

## 软件检查

`unchanged-timer-workspace-tests-final.log`：**1078 passed / 2 ignored**；
all targets/features Clippy、私有 Rustdoc 通过。State 的四条实际 SQL 测试覆盖无变化、
独立显式零事件命令、精确 JSON、真实写入/回滚/事务前写入、另一实际 connection、
pending command/event 与重启无 witness；实际 timer 及单 network worker 隔离测试通过。
Python maintained quality Ruff/format/函数文档和 strict mypy **364 files** 通过。

维护的真实产品 gate 显式 `--idle-timer-check`：只读观察原完整 SQL/checkpoint/
journal/parent revision 五秒；随后正常 HTTP 上传必须增长真实 journal，并继续全部
原内容多数、SIGKILL/wait、唯一 successor、同 inode/writer 和重启读取检查。旧二进制
反例与新版本成功的边界分别保留，不将短窗口作为长期性能或 SLO 证明。

## 实际产品与执行链

`unchanged-timer-native-01/result.json` **通过**。新二进制的原 **5.0025 秒**空闲
观察保持 local journal **1 → 1**、parent revision **1 → 1**、event sequence **0**
以及该轮完整 checkpoint 的原 SHA-256；不是从旧版本补填来源或复用其不同配置。
正常上传随后增长实际 journal，实际原 payload **131101 bytes** 在三个 infrastructure
成员独立核对。原 Controller PID **3818851** 实际 SIGKILL/wait **9**，同 epoch 的
唯一实际 successor **3819799** 完成原内容读取；supervisor **3818673 / 3818674 /
3818675** 实际 wait 全部为 **0**。原 parent/Controller/Root SQL inode 分别为
**56951567 / 56951580 / 56951136**，Artifact writer inode **56951578** 保持。

实际文件交接命令：

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-p7-idle-handoff-20261023-01 \
  --process-workers --handoff-case complete --async-workers 1
```

该轮 **通过**：两个按配置正式注册的软件 Node、**3** 个实际 owned CPU hash jobs、
**2** 个独立 verifier、**2** 个真实 SQL rollback windows。原 software worker exit、
source Context/provider revoke、原 budget 重启不续及两个后续 Task 的独立原 preparation/
import 均通过。目标没有在 Commit 前 Execute，target Mission 为 **Completed**，
source 没有自动 resume。原始路径与文件保留，另存 `unchanged-timer-file-handoff/`，
它是测试证据副本，不是恢复包或物理证明。

冻结实际二进制 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `controller-replica-adr0173-01` | `f1de5fd11e5aa3929cff25274697600a4da237ea11364490efd3793645a4a207` |
| `integration-server-adr0173-01` | `b0f777feb62843605399236ce04fba427d97ade7c9a06804b742fb689f905481` |
| `roboguide-node-adr0173-01` | `dca84fe9cc67eb00906a586d9f605d2ba7f33135be50dc46259bcd0ad922e022` |
| `content-stream-probe-adr0173-01` | `423410beb4126c4042e2bf82ddd65c4735091c9ffe6d4b728f5bcb5b4bc8129a` |

## 完成边界

本优化解决空闲重复提交；它不关闭完整 N7、跨 owner 读取、Prepared takeover、完整
readiness/HA/升级/恢复/GC 或其它 N1–N7/P1–P7/F0–F10/R01–R20。真实设备停止、
物理连续性、跨主机恢复与长期 SLO 仍需各自实际证据。
