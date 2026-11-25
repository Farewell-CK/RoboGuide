# 跨域持久协调合同 v0.1

[ADR-0211](../decisions/0211-durable-cross-domain-coordination.md)定义权威与兼容边界。
Root Controller 的 Orchestration 协调完整全局事务，各域 Control 独立拥有本域资源；
Runtime/State/Memory/Local EAIOS 继续保留原职责。没有内置 Node、设备或部署域清单。

1. 部署显式声明实际 peer 地址、TLS CA/leaf、服务身份、domain grant、漂移与请求预算。
2. 原管理输入冻结全局目标、全部域与 canonical 本域计划；原命令 ID 和语义不可重写。
3. 实际 peer probe 持久化本域 nonce、真实 writer epoch、receive time 与原 kernel budget。
4. Prepare 在原事务内按正常 Control 接纳并取得资源，同时冻结 dispatch；普通入口不能绕过。
5. Root 的 SQL intent 先于每次实际 HTTP；丢失响应只读对账，保留原 Unknown，不能重发副作用。
6. 全部原资源证明齐备后，先持久化不可逆 Commit 决定，再通知各域；完整 Commit 回执先于 release。
7. 每域执行仍核对当前 epoch、原预算、Readiness/State/Memory/outbox；超时走原整组停止撤权。
8. 独立原目标验证先于全局完成；本地 Completed 或 metadata quorum 都不等于全局满足。

时钟证据保存实际往返区间与远端本地样本，不把远端 TimestampMs 当作本地绝对时间。
漂移是部署合同输入，超出不确定性上界即拒绝；进程重启不续原预算。设备与实际跨主机
观测单独记录，单机多进程只能证明软件路径。新权威显式使用 wrapper v60/recovery v0.29；
旧版本、既有 participant v0.2、原协调/拓扑/Memory/Replica/Archive 各自保留原义。

[双域实际进程、原目标和声明数据恢复](acceptance/2026-10-25-federation-execution.md)通过本地软件验收。
证书/分区/HA/全局内容的最终组合继续必需，不据此宣称整体 SoftwareComplete。

`global_dispatch_ready=false` 表示只读观察不声称瞬时全局就绪；实际执行授权来自已持久化的
完整决定、全体原 Commit 回执及参与域当前 epoch/预算/Readiness/Memory 门禁。
独立原目标接收记录不创建本域 replan policy；变更本域语义计划必须由全局完整停止和新审查事务协调。
