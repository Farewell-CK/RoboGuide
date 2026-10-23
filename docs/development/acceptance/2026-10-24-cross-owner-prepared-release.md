# 跨 owner Prepared 上传释放验收

2026-10-24，接续 [ADR-0181](../../decisions/0181-cross-owner-prepared-upload-release.md)。
唯一开发分支仍为 `codex/nonrelease-remediation-closure`；前置 `07ecada`。

新 leader 的真实 writer B 可以将原 A 的 Prepared 显式放弃。新事件保留 A 的
完整 origin，构造方/接收方分别复核原不可变 successor；其它新字节仍来自实际
writer。该 DELETE 解除原上传活动输入，不更改历史 source、恢复 Unknown 或其它
consumer 的保留责任，不 unlink。跨 owner Completed 仍拒绝。

## 原始检查

证据目录：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。
`prepared-release-native-01/result.json` 为通过；检查器固定源码与 binary 后运行，
含 `--idle-timer-check --owner-failover --prepared-recovery --release-after-recovery`。
本次扩展原流程，未重复其它不变的 Archive/实验/完整文件矩阵。

实际三副本、真实 Controller/mTLS/SQL/CAS 先完成原 Prepared 故障、source 退出和
新 leader 恢复。恢复完成 SQL 再次故障后的原 child PID **4118253** 实际 SIGKILL，
supervisor 的原 SQL wait outcome 保留 signal 9；同 epoch 337/term 2 的新 child
PID **4122841** 启动，仍观察到原 Unknown。随后 DELETE 仅新增一次 Abandoned，
重复 DELETE 无新增 Domain event，原 Unknown 在放弃前后完全相同。

原 source owner：`artifact-store:ebb43c582b5faedca145fbdea451aca2fd6743cbdcd1b824e44b25f3261ea974`。
实际新 CAS owner：`artifact-store:5369922aaacb6f430ad196b2c6fa34d88d87751e27372b2f0c0a4e9d99b4866d`。
原 196655-byte key 从当前 inputs 移除；该 digest 的新 owner 上传仍是独立输入。
三个 supervisor PID **4114934 / 4114935 / 4114936** 最终均实际 wait 0。

`independent-release-verification.json` 在全部进程退出后直接只读核对原 Controller/
Replica/Root SQL 与真实 bytes：原 source 的三份实际文件 inode 为
**56703589 / 56704044 / 56703590**，各自完整哈希/大小正确，实际独立 stream/log
引用仍活动（原 source 还保留自己的旧 View）。这既不是新 use grant，也不是物理
删除/停止。证据索引增加本次 result 与独立观察，现有 147 项。

| 冻结文件 | SHA-256 |
| --- | --- |
| controller-replica-adr0181-01 | `8b34c88a6788abe5e99aa0dbfa2b8179929b4b9bd5f12ec21fb1e10ded2086b0` |
| integration-server-adr0181-01 | `8b82683a29ade962358b85f2fa512bb107756af28c75779e8f736b1b05a8c468` |

## 编译与反例

原 112 项相关 Replica 回归通过；新增实际文件/直接 SQL storage 测试通过，包含
改 owner、续原预算、伪造 Completed 拒绝，以及 protected Archive 前缀的原 origin
查找。它还实际创建并持有 Snapshot 验证独立 pins。最初测试误断言 direct apply
自动产生 Log pin，失败后改为真实创建的 Snapshot；没有放宽产品 pin 规则。

全工作区 all-targets/all-features Clippy 通过；Controller 4 项通过，相关私有
Rustdoc、维护检查器 Ruff/strict mypy/函数文档通过。共享 EventPayload 的上一包
远端 CI 暴露 Controller 测试 exhaustive match 缺分支，`07ecada` 已修复，本地
工作区编译检查通过。之后共享类型变更先做全工作区编译，耗时进程检查按影响范围
选择。双语站点 strict build 和 9 项测试另行记录。

## 剩余边界

本次零具身 Nodes、设备、模型、模拟器。原 kernel budget、writer/storage schema
及 Node 合同不变。Completed 上传的跨 owner 退休、原 Unknown 的只读对账、Root4/
parent8 完整 bundle/升级、在线 readiness/Archive/Snapshot/GC 和全部 N1–N7/P1–P7/
F0–F10/R01–R20 仍必需；不标整体 SoftwareComplete。
