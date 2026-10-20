# 单网络 worker、原 writer 与重复 leader transfer 验收

日期：2026-10-20。基线 `main@e987a7e`；对应
[ADR-0118](../../decisions/0118-synchronous-application-worker-isolation.md)及
[执行/部署说明](../controller-worker-isolation-v0.1.md)。Node/事件合同与 wrapper v42 不变。

## 已确认的问题和修复

完整 timer、Node batch 与部分 Artifact authority/SQL 直接占用异步 worker 等待原 writer。
现在同步事务/有锁查询在进入前归还 multi-thread Tokio worker，实际 gate/TLS witness、
candidate、Commit、ACK、outbox 与原 receive-time/budget 顺序不变。单网络 worker 下，
另一个实际线程持有原 mutex 时，真实 TCP 可服务；原 timer 在 gate 释放前没有 checkpoint。

另确认 leader transfer 成功后原 source 的 heartbeat/election 未恢复；取消/部分 timeout
同样可能遗留禁用。串行 source guard 与 Drop 恢复全部退出路径，包括 source 已变成
follower 的情况。原 source 再次成为目标，或在目标原 writer 排队时取消，均保持正常
Raft liveness；这不授予新 epoch、资源、设备或物理停止事实。

## 旧实现的独立反例

独立 detached 工作树 `/tmp/rg-worker-old-source-20261020` 固定 `e987a7e`，只加入同一
回归测试；原 timer 与 replication/lib.rs 未修改。单 worker timer 对照失败：在网络能
处理前，原 writer 已等待 5 秒 timeout 并允许 timer 提交。重复 transfer 转回原 source
失败，目标未在原 deadline 内成为 leader。取消后等待 8 秒，旧 source 丢失原 epoch；
新代码相同用例通过。

4 秒取消观察最初不足以区分新旧实现；8 秒覆盖 follower lease 和 election 窗口后才形成
反例。保留该原始结果。第一次旧源码对照的命令目录误指向新源码，已中止，不作为证据；
只采用随后独立旧目录的三份结果。它们证明具体回归，不把所有历史 HTTP 中断归因于
单一原因。日志在仓库外，原生产文件可用 Git diff 复核。

| 对照日志 | SHA-256 |
| --- | --- |
| `/tmp/rg-worker-old-negative-corrected.log` | `b6991aa95ae91e9aa6132f606bb0243a50f7e8cb4ece6bf538a44b65bb188348` |
| `/tmp/rg-transfer-old-round-trip.log` | `29eef6d5c7cc93a8b204f5bea102dfd7b9d32f0a922ae15daacebf35d6157314` |
| `/tmp/rg-transfer-old-cancelled-eight-seconds.log` | `a03fd7416a2884bf54fda8082ed01a1829685c370936d8656fe49b16ec30029f` |

## 实际完整软件路径

```bash
uv run python tools/quality/check_replica_certificate_processes.py \
  --output /tmp/rg-worker-certificates-20261020-b --skip-build --async-workers 1 \
  --old-binary /tmp/rg-pre-v03-replica-20261020
uv run python tools/quality/check_replica_recovery_processes.py \
  --from /tmp/rg-worker-certificates-20261020-b --output /tmp/rg-worker-cold-recovery-20261020-b
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-worker-file-recovery-20261020-b --async-workers 1 \
  --authority-recovery --content-reclamation --monitoring-tools /tmp/rg-monitoring-tools-20261020
```

重现使用新输出目录。当前同一实际 Controller binary 完成：

- 三副本 mTLS 原 Stage/Retire/Abort、实际 CA 迁移、旧 key/旧 binary 拒绝、原 PID/epoch
  换证与原 store restart；7 次受控 supervisor stop 全 exit 0，零 Node。
- 完整 6 SQL/39 tables/10 原换证记录静止恢复；三个实际恢复 leader 各自读回原 CAS bytes
  与原 immutable upload receipt。6 次受控 stop 全 exit 0，零 Node。
- 两个正式软件 Node 的实际文件/CAS/CPU hash、独立 verifier、原 Memory revoke/reclaim、
  Node/backend/Controller restart。两次真实 SQL save/Commit 故障及原 recovery probes，
  写入禁用时真实在途上传第二字节被封住。
- 真实 Prometheus/Alertmanager mTLS scrape、2 firing + 2 resolved，投递到独立 owned
  loopback receiver；监控凭证没有 mutation 权限，没有向人员发送消息。

所有 positive 路径 devices=models=simulators=0；软件 Node 由真实程序正式注册，不能当作
设备证据。早期仅 worker 修复后的冷恢复 A 仍遇重复 transfer 的 quorum 问题，保留失败。
补齐 guard 后 B 完整通过。不能据单次窗口声称全部压力/SLO 或历史故障因果已证明。

| 原证据 | 实际身份 |
| --- | --- |
| Controller | `sha256:de4c94e64c54d7bf84198e008d85022431a0fcc04ab4f0db94d25547e92f6440` |
| Replica | `sha256:dfd59dd2813f60f2de4340f52afd6817d4c6e897abd07d98a88d495a0b8d5a66` |
| 双 Node 原 result | `sha256:8a53616e5bf7e9c94a8f50defcea293bdd16d02b473b53065824f76c8bc95f70` |
| 证书原 result | `sha256:53dd106657cd7943471353939c5f296f229fcd323a32ef8770156eb10442f9f5` |
| 冷恢复原 result | `sha256:dca243d801755dff02364529f3d3cb8e72a34901dbfb0b691e201f209f50f8b2` |
| 冷恢复 manifest | `sha256:1189ba770d53d1e505455f1db0916b5ce70ff60ac5352e3a952d9886cb05bc2d` |

完整 Rust workspace 900 passed、2 原有 ignored、27 suites，8 秒取消用例另单独通过；
workspace/all-target Clippy 与 private Rustdoc warnings denied 通过。Python lint/format、
strict mypy 主集合 231 files 和函数文档通过；完整 Python 2626 passed、1 原有 skipped。
线程数 1–64 输入与既有 policy digest 绑定有独立回归；原当前线程 offline 行为保留。

## 继续要求

Operator 其余同步 query/registry 工作线程边界、长期 SLO/跨主机压力、完整 durable
transfer/升级和在线内容恢复仍必需。F0/F4/F6–F9 以及整体 SoftwareComplete 不因此关闭；
物理停止、硬件身份、硬实时与物理连续性仍是独立待验证证据。
