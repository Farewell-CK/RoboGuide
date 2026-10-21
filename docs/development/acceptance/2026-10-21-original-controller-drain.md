# 原 Controller 子进程排空与完整恢复

ADR-0138 接通真实 supervisor 的 pre-spawn intent、实际 PID/boot/start tick 和原 Child.wait。
transfer 冻结原 child，提交 intent 后封住源应用；独立 wait 持久化、原 quorum 和八秒
预算齐备后才发 `TransferLeadershipV3`。target 在实际认证 source 下核对 proof，原接收
预算包含 member-writer 排队。Restart 不重复 election，也不能替换未对账 spawn/进程。

storage v3 / recovery v0.7 固化完整本地 process/transfer 日志。旧 binary/profile 拒绝；
旧应用有 checkpoint 而缺少原 child journal 时保持 Unknown，不能视为空进程。完整
受控旧部署升级仍需显式排空迁移证据。基础设施 wait 不证明 Node 或设备安全停止。

当前 39 项 Replica、31 项 Recovery 回归通过，含真实 `/bin/sleep` 子进程：即使实际
kill/wait 已发生，未写原 authority journal 时也不发目标 mutation；写入原 wait 后才
允许选举。另覆盖重启后未对账 spawn、原接收预算排队耗尽、v2 应用连续性缺失和完整
SQL 恢复缺少 process journal 的 repinned 拒绝。合成单位合同不进入部署设备事实。
整仓 Rust 939 项通过、2 项保持原 ignored；fmt、完整 Clippy、私有 Rustdoc 严格检查通过。

独立进程记录：

| 证据 | SHA-256 | 范围 |
|---|---|---|
| `/tmp/rg-original-drain-storage-upgrade-20261021-b/RESULT.json` | `bd713022f7fe29256028c9a2e5465a436afa8c2bf1a6065c086673162fa33ddc` | 十个进程，实际旧 v0/v2 活跃排空、原 identity、hardlink fence、旧 binary 拒绝、probe 首尾摘要一致 |
| `/tmp/rg-original-controller-drain-certificates-20261021-c/result.json` | `26d2e15e46a73b080748051edf3b004769f4c5840e5a00805fbe2aecc97fc626` | 原 source PID/wait 与目标保存 proof 一致；独立 SQL 查询原日志 |
| `/tmp/rg-original-drain-complete-recovery-20261021-b/result.json` | `49727b84aee14c23fe7e5866cf8b35ce2223100c2ed8a715253c1625945aeae8` | 完整 SQL/process/transfer/CAS 与三个恢复 leader |

较早完整链路结果 `/tmp/rg-original-controller-drain-certificates-20261021-a/result.json`
（摘要 `a32ebc39d888deeb52bcd1b846060aa0e4706deed3492850125fcc07ae619171`）和
`/tmp/rg-original-drain-complete-recovery-20261021-a/result.json`
（摘要 `ac039020a3419767cb9dbbcd5d9dba059c453d62819ca9c59e215ee4b5ae1fb9`）保留为
连续性 marker 加入前的证据，不冒充最终 binary 的完整验收。

`/tmp/rg-original-controller-drain-certificates-20261021-b/result.json` 保留一次后续换证
前 quorum 未确认的失败，所有原进程已回收，未重复修改命令。实际 checker 在每个正向
修改前等待原认证只读 quorum；修改仍仅发送一次，运行中 quorum 失效仍如实失败。
单机观察不能证明跨故障域在线 HA、长期可用性或整体 SoftwareComplete。

最终三副本 Replica binary 为 `b440dcc5ee6bf6f708a335c96cfc64793967ef6c3530c6f4e37e950322ffc7fb`。
完整恢复保留 6 SQL/42 张表，三个实际恢复 leader 独立读取原 CAS 与 finalize receipt；
所有测试进程已回收。Node、设备、模型和模拟器均为零。Python Ruff/format、strict mypy、
函数文档和严格站点构建通过。
