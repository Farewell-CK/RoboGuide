# wrapper v53 的 standalone 恢复与回收接线

2026-10-24，接续 ADR-0177 的显式 recovery v0.18，前置 `8a2117d`。
原包升级共享 wrapper 后，部分离线 GC/恢复调用仍固定使用 v0.16/v0.17，导致
真实 CI 在完整 Controller 检查前正确拒绝。CI 40004133092 的 exhaustive match
遗漏由 `07ecada` 修复；CI 40005715005 又暴露这条 profile 传递缺口。

修复将实际选择的 profile 传到 source GC、原 Snapshot 退休、writer inventory、
Catalog/Archive 检查及完整 Controller inspection。检查器从实际 wrapper/SQL
header 生成明确的 legacy 或 v1 声明，CLI 继续独立验证全表、marker、实际 leases、
bytes 与原 bounds。旧 v0.16 仍拒绝 v53；v0.18 仍拒绝新 recovery 事件和
parent8/Root4。原 intent/receipt/profile digest、Unknown 和预算不重写。

## 验证

证据根目录：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。
相关 recovery-tool **143 项通过**；完整维护范围 mypy **366 source files 通过**，
Ruff/format/函数文档通过。Rust 未修改；实际 product binary 为原冻结
`integration-server-adr0181-01`，SHA-256
`8b82683a29ade962358b85f2fa512bb107756af28c75779e8f736b1b05a8c468`。

| 实际链路 | 原始结果 | 范围 |
| --- | --- | --- |
| GC SQL 恢复全部 10 类 | recovery-profile-gc-sql-native-02/result.json | normal、guards、SQL、copy、receipt、expiry、source、kill、missing-history、WAL；原 legacy→v1 与冷恢复 |
| 旧 Catalog 接管 | recovery-profile-catalog-adoption-native-02/result.json | 原 publication、真实 mTLS、SQL rollback、Archive、重启与 v0.18 冷恢复 |
| 原 source GC | recovery-profile-source-gc-native-01/result.json | 原上传/Catalog/升级 Snapshot 退休，文件/SQL 故障、Unknown、原 tombstone/unlink 与冷恢复 |
| Catalog/child 冷恢复 | recovery-profile-catalog-cold-native-02/result.json | 真实 revision-mi child lineage 的实际 bytes 与完整 receipt |
| Archive/Catalog 冷恢复 | recovery-profile-archive-cold-native-01/result.json | 实际归档原 envelopes、CAS、manifest pin、restore/retry |

上述检查均通过。三个新输入分别位于 `/tmp/rg-prf-20261024-01`、
`/tmp/rg-prf-arc-20261024-01`、`/tmp/rg-prf-mi-20261024-01`。每个输入运行维护的
两个真实软件 Node、文件 provider、受管 CPU worker 和独立哈希验证，实际 waits
完成；不产生机器人、设备、外部模型或模拟器。revision-mi 的本地 Responses
endpoint 是明确的协议 fixture，不能当外部推理或物理成功。

历史副本留在证据根的 `recovery-profile-legacy-source-retained-01`、
`recovery-profile-archive-source-retained-01`、`recovery-profile-mi-source-retained-01`。
各自 retention.json 保留原路径/结果 hash/排除的 Unix sockets；复制后的 inode
不是原 live authority 或恢复许可。索引现有 155 项，不用条数推断总体完成度。

未通过的先前尝试保留：过期旧 TLS 输入、过长 Unix socket 路径、缺少真实 child
lineage 的普通文件输入均被拒绝，不计通过；使用新短路径及实际 revision-mi 输入
完成验证。GC native-01 因声明缺少强制 storage format 被拒绝，补齐实际声明后
native-02 的全部案例通过，未放宽产品 fence。

## 推进规则与剩余项

共享类型/版本变化先做全工作区 targets 编译，核对完整 producer→recovery/GC
调用链，再跑相关进程案例。复用历史输入前先核对 case、lineage、证书有效性；
Unix socket 使用短根目录。已验证功能及时提交，重复全量矩阵需有具体变更或失败。

本包只修复已承诺的 standalone 兼容性。Completed 跨 owner 退休、Unknown 只读
对账、parent8/Root4 完整恢复与升级、在线 readiness，以及全部原非发布计划仍
必需，尚非 SoftwareComplete。远端 CI 按最终提交另行确认。

## 私有接收方检查接续

CI 40013334284 已通过原 GC/SQL 节点，随后在私有 Archive recipient 的旧 profile
拒绝断言失败：检查器只接受 v0.14/v0.15 错误，当前 v53 正确要求显式 v0.18。
断言现按实际选择的 profile 核对，继续要求目标未发布，产品 fence 未放宽。

另核对发现该 CI 曾把当前 binary 传给名为 old-controller 的参数。维护入口现为
`--writer-controller`（保留旧参数别名），分别记录实际 producer/writer schema、
hash 和 `actual_older_controller_proof`；当前 writer 的拒绝不计旧 binary 证据。
`recovery-profile-private-recipient-native-02` 使用真实 v49 binary
`099e3f8a559be67b8c2cebdc4296e404920c4af837e7a38dc7a7c7814a363f89`，
older proof 为 true；`recovery-profile-private-recipient-current-native-01` 使用当前
v53，older proof 为 false、old-controller 字段为 null。两条完整实际退休/GC/
故障/冷恢复均通过，零 Nodes/设备/模型/模拟器。初次 v47 尝试的不同目录格式
拒绝记录保留，不计整条 gate 通过。完整维护范围 mypy 366 files 通过。
