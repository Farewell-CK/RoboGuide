# 2026-10-23 真实 Controller 内容 Profile 验收

唯一实施分支 `codex/nonrelease-remediation-closure`，前置提交 `2ad4cc6`。
[ADR-0172](../../decisions/0172-runnable-owned-controller-content-profile.md)和 V2 先说明
权威、部署和生命周期。证据根为
`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`，原失败不删除。

## 本包行为

显式 process v0.2 使用真实 network v0.4、已存在的 Root4、parent8、mTLS 和私有
Unix endpoint。实际 Root owners 与原 committed member data 核对。原 process
v0.1 不隐式升级；新 profile 的合同、child budget 和 quorum 观察窗口进入同构
application policy digest，路径和每成员 owner 不冒充同构策略。实际源 writer
由 integration-server 同一 composition 创建，与原 Artifact HTTP Prepared registry
及正常 State 完整恢复提交共享，不能打开第二个 writer 借其身份。

原 spawn intent、实际 PID/boot/start ticks、原 wait 继续保留；子进程只在原有界预算
内做只读 registration 等待，不生成任何控制、payload 或执行效果。实际当期完整
内容 quorum 与原完整恢复投影核对后才 materialize inactive Controller SQL，原
epoch 再复核后才启动实际新 child。

`ProcessFence` 只证明实际原 PID/TLS/current epoch、transfer fence 和 infrastructure
quorum；watchdog、事务前检查使用它，不宣告内容 ready。原完整 Fence、Commit ACK
和 dispatch gate 仍要求完整候选与同一 current/joint voters 的 metadata+全部字节。
首次效果的 Unknown 和后续完整候选只读观察分别保留，观察不重发 copy/Commit。

Raft log 读取、vote/committed SQL、truncate/purge 和 applied-state 原同步 I/O 移到
owned blocking scopes；最多 16 个 small consensus scopes 与原 8 个 heavy producer
scopes 分别限额。原时钟在入队前取样，实际 scope 持有 SQL/Root leases 与容量，取消
async 等待不能提早释放。Supervisor status 串行等待其实际 blocking scope 完成。
current continuity/transfer 查询使用原 inode 下已提交 WAL snapshot，避免与应用锁互等。

新 profile 显式声明 100 ms–30 秒 quorum 观察窗口，且不能超过 child 效果预算。
每次检查只有一个原窗口，可重复实际只读 linearizable query；每次均核对原 epoch，
真实多数派不可用或到期仍拒绝。旧构造器保持原单次两秒语义；Raft 原 heartbeat/
选举配置保持。这是部署的故障检测声明，不能成为设备停止期限或续期的效果权限。
本轮实际配置为 **15000 ms / 45000 ms**，分别为观察窗口/child 完整操作预算。

## 原失败与实际成功

`runnable-content-native-01` 暴露检查配置将实际只接受 0/1 的 local recovery admin
写成 `true`；配置修正，并在产品启动验证中提前拒绝非法声明，避免真实 child 反复
spawn。native-02–05 保留短 quorum 探针、同步 Raft SQL/状态读取和实际刷盘期间的
失败；据实际调用链修复阻塞边界。native-06 已走通上传与三份原 bytes，但 **5000 ms**
观察窗口在 child 重启后读取时不足，整轮失败，原日志和所有 actual waits 保留。
没有补写原成功回执、刷新旧预算或自动重执行未知上传。

`runnable-content-native-07/result.json` **通过**。实际 infrastructure 为
**307 / 311 / 337**；原真实 Controller PID **3747434** 完成正常 HTTP
Started→Prepared→Completed、下载与独立三份完整字节核对。实际 SIGKILL 后原
wait signal **9** 持久化，新的实际 PID **3749369** 在同 member/epoch 下恢复，
再次 HTTP 返回原完整 payload。三个 supervisor PID **3747268 / 3747269 / 3747270**
原 wait 均为 **0**。没有创建 embodied Nodes、设备、模型或模拟器。

最终检查额外核对重启前后全部原 SQLite inode、实际 Artifact writer 身份不变，且
原 process journal 只有首次 child 与这次故障后的实际 successor。它不把健康入口、
状态文件或同 hash 当作原 writer/内容来源的证明。实际源和二进制 digest、每个完成
阶段、完整原候选/原回执及实际 wait 均保存在该轮 `source-identity.json` / `result.json`。

## 回归与边界

`runnable-content-native-final/result.json` **最终通过**，实际 source identity 在整轮
前后保持；产品不含临时逐 RPC 诊断输出。原 child **3763444** 的实际 wait signal
**9** 后，原 member **307**、同 epoch 的唯一实际 successor **3767304** 完成原
HTTP 内容读取。三个 supervisor **3763305 / 3763306 / 3763307** 正常退出 **0**，
无强制清理或额外 Controller 重启。原 Controller SQL inode **56700673**、parent
inode **56700411**、Root SQL inode **56699493** 以及原 Artifact writer inode
**56700611** 在重启后保持。三个原 Root SQL inode 为 **56699493 / 56700025 /
56700399**。以上是各自实际存储/进程证据，不归并独立 owners。

最终冻结可执行文件 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `controller-replica-adr0172-final` | `e96027056fa93556223720623aa468c8c282ebbbaa7401901674737b5385230b` |
| `integration-server-adr0172-final` | `df6db4ebc192660e239d1430414db9e1e7f4eae1d5858d4530e75ad4d04dfe92` |
| `content-stream-probe-adr0172-final` | `f3f6d19a7f1ef682deb9d9d40881c87f0872573455c3abff8bffb34bb9c945d5` |
| `content-network-probe-adr0172-final` | `6f0bd676d11477947bdadad7c057e938bcf9d5b42b4ba002e6f317229f93e2d0` |
| `owned-content-source-probe-adr0172-final` | `753b32d38f3d8b00544e360ec532534a1375ea3d409c7f849dcaaf01a9ebbfdd` |

复现入口要求显式冻结二进制和新的 evidence 目录：

```bash
uv run python tools/quality/check_runnable_content_processes.py \
  --supervisor /explicit/frozen/controller-replica \
  --controller /explicit/frozen/integration-server \
  --storage-probe /explicit/frozen/content_stream_probe \
  --output /explicit/new-evidence-directory
```

`runnable-content-workspace-tests-final.log`：**1073 passed、2 ignored**。
新增实际单 worker/原 SQL 锁/十六个 worker 取消与 lease 释放测试，以及真实网络
瞬断恢复、真实少数派到期与 epoch 不匹配拒绝测试通过。配置测试核对原 policy、
旧 schema、非法 budget 和启动前非法 admin flag。Clippy all targets/features、
私有 Rustdoc 和 maintained quality Ruff/format/函数文档、strict mypy **364 files** 通过。
`runnable-content-owned-regression/result.json` 使用同一最终冻结 probes 通过原
parent8 file-contract、实际 SQL receipt failure/Unknown、原 source wait/restart、
完整 metadata+bytes 多数缺失反例与实际 infrastructure SIGKILL/原 inode 重开。
这些 opaque file-contract 子进程与本轮真实 Controller kernel 分别标识，不能互相冒充。

这只闭合当前 owner 的真实 runnable HTTP/内容提交与原 child restart。跨 owner
公开读取、Prepared takeover、完整 readiness、joint/catchup/Snapshot、在线恢复、
升级/回退、stream/reader GC 与原业务目标故障矩阵继续实施。全部 N1–N7/P1–P7/
F0–F10/R01–R20 保持必需；本包不证明物理停止、跨主机 RTO/RPO、硬实时、设备
连续性、长期 SLO 或整体 SoftwareComplete。
