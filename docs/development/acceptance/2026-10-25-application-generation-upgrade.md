# 应用代次升级、失败修复与完整恢复验收

日期：2026-10-25。开发基线为 `d2c6918c`，唯一分支
`codex/nonrelease-remediation-closure`。本包对应执行队列 A3/N3 的基础设施滚动替换、
原应用升级、失败修复与不安全回退拒绝。它不替代全局 Archive/引用矩阵或 B–F。
实现和命令含义见[模块说明](../application-generation-upgrade-v0.1.md)及
[ADR-0207](../../decisions/0207-explicit-application-generation-upgrade.md)。

## 真实输入与证据边界

原 v55 Controller/supervisor 从原提交
`dbe7c1e7ad9c4b43b85909be4dff7b61676d55dd` 独立构建；原 v53 compatibility probe
从 `faede53c3e149119406fa1b5bc064c7fa538db03` 独立构建。旧、新构建使用不同 target
目录，未将当前源码当成旧版本。实际运行副本移除 debug section，并独立比较每个
SHF_ALLOC section 的字节、地址、长度、flags 和 alignment；原文件与运行文件哈希
分别保留，不能混用。完整记录在 `/tmp/rg-upgrade-compact-4/identity.json`。

最终实际运行输入：

| 程序 | 实际 SHA-256 |
| --- | --- |
| 当前 supervisor | `757fe497246c4c15ee38e95346dae08d8aabed03c2099d41b8c8814877827c3e` |
| 当前 Controller | `af8883ec94f4825625be3ef6b95603a20b3a9a8e1731fb2017a622706e6f5c5e` |
| 原 v55 supervisor | `8c8cb02de9e416f7d7087905ef8a0ea45688012fdb324f7079583d83e21a2457` |

每个实际 case 保留原配置、binary identity、实际 TLS leaf、原 SQL/inode、原 PID/wait、
原始 HTTP/管理结果和失败记录。三个成员均为基础设施进程；本包新建 embodied Nodes、
设备、模型、模拟器数量均为 0。现有 source initialization case 读取历史软件 Node
数据，但不会注册或执行新 Node，不将历史数据升级成当前设备事实。

## 结果

| 实际门禁 | 证据与结果 |
| --- | --- |
| genuine v55 → v56 滚动升级 | `/tmp/rg-upgrade-native-4/result.json` 通过；follower 逐个替换、原 leader child 实际 wait 后 transfer、原 parent inode 保留、all-member support/原 child wait/activation、原 quota 不变、三个 leader 实际服务 |
| 旧程序与不支持的 target | 同 case 实际拒绝旧 supervisor 打开 parent10；真正的旧 Controller 没有新协议，作为 target 在产生新 intent/进程/检查点前被拒绝 |
| 原 Unknown 重启对账 | `/tmp/rg-upgrade-unknown-1/result.json` 通过；读取真实失败 case 的三份原 parent10，重启后原日志提交；从未重发 activation，原时钟、wait、checkpoint、revision 与 inode 不变 |
| 升级后完整冷恢复 | `/tmp/rg-upgrade-cold-1/result.json` 通过；recovery v0.25 backup/verify/restore/同 ID 只读复核、原二进制拒绝、三个恢复后 leader 的原 quota/升级历史/空 Node inventory；最终运行字节的独立 case `/tmp/rg-upgrade-cold-2/result.json` 也通过，manifest `sha256:a8855e5f927b7ec75a70b7cc4e55ec0bfaa38f83cbd4cc5596b4cac48d8d89de` |
| 真实坏版本修复 | `/tmp/rg-upgrade-patch-1/result.json` 通过；实际失败 source 先完整静止恢复，原坏版本只启动一次、管理面保持可用，新同 schema 补丁提交且原坏版本 history 保留；退休程序回退被拒绝 |
| 补丁后完整冷恢复 | `/tmp/rg-upgrade-patch-cold-1/result.json` 通过；最终 SQL 失败/显式放弃/补丁的 `/tmp/rg-upgrade-patch-cold-2/result.json` 也通过，三个恢复后 leader 核对原 authority/retained targets/原 quota，manifest `sha256:2bdc64d058fea22f14cb7126498b57f597f8b89528e2377d461f9f3e46bfeea3` |
| 提交前 SQL 失败/原操作显式放弃 | `/tmp/rg-upgrade-patch-3/result.json` 通过：真实 SQLite writer 阻塞、原未提交 intent、显式放弃及独立新补丁；原时钟/局部 wait 不变，未重发失败 activation |
| 原归档源初始化 | `/tmp/rg-upgrade-init-2/result.json` 通过；genuine v53 拒绝 parent9，完整 drain/upgrade 后导入原 v55 source，原来源/CAS/Unknown/换主与重启保持；未创建新 Node |

## 保留的真实失败

`/tmp/rg-upgrade-init-1` 原失败是新 Raft 命令包含数字成员 map 时，untagged serde
不能恢复原嵌套命令；日志和原三份 intent 均保留。现改为按单一完整命令字段解码，
拒绝额外字段，数值成员 round-trip 反例已回归。后续 Unknown case 在原 inode 上重启，
观测到原日志实际提交，没有重发或修饰原结果。

`/tmp/rg-upgrade-native-2` 原失败是已经启用的 v56 程序启动时拒绝原复制 v55 检查点。
已启用坏版本的实际 SHA-256 为
`f318cc9c1fab73202b3a1b0ad8bb7eacec003a2302a5619a761de0046b474a5f`。
修复没有放宽原严格 validator；新增路径独立核对实际 quorum activation、原完整
predecessor 和当前程序完整字节。原失败 activation 留在真实补丁和冷恢复证据中。

`/tmp/rg-upgrade-patch-2` 复用第一份 backup command ID 到另一目的地，被原 Artifact
Snapshot journal 正确拒绝。checker 已改为每个独立证据根固定不同原 command ID，
而同一次 backup/restore 的重试仍保持原 ID；原失败和原 Snapshot 记录未删除。

## 工程检查

Rust 全 workspace：1180 通过、0 失败、2 按原配置忽略；其中 Replica 168、supervisor
12、integration-server 282。全 targets/features Clippy、Rustfmt 通过。恢复 Python
228 项通过；恢复包/测试 strict mypy 49 个源文件、实际工具 strict mypy 5 个源文件、
Ruff/format、函数文档和 warnings-denied 私有 Rustdoc 均通过。

CI 已接入 genuine 原 v55 独立构建、实际滚动升级及完整 v0.25 三 leader 冷恢复。
本地结果不冒充远端 CI 状态。发布、设备停止、跨主机故障和长期 SLO 不在本包证据内，
原完整非发布整改范围保持，尚不宣布总体 SoftwareComplete。
