# 实现状态与证据边界

内容基线：`f961a69e`，核对日期 2026-10-26。[构建参考](reference.md)提取当前源码版本。
[唯一执行队列](docs/development/roboguide-software-completion-plan-2026-10-15.md)与
[总验收矩阵](docs/development/nonrelease-acceptance-matrix-2026-10-26.md)保留完整非发布范围。
最终双域 HA/完整恢复、当前完整 CI 和 main 集成继续，尚未签署 SoftwareComplete。

| 方面 | 已实现并有实际局部软件验收 | 最终组合或独立证据 |
| --- | --- | --- |
| MI / Orchestration | 原 Request/provenance/Review、自动运行期 MI、独立原目标与完整 scoped typed-map 修订 | 最终全局组合；实际部署模型与物理目标来源 |
| Control / 资源 | 当前来源成本、quota/divisible Compute/Context、POSIX proof、time/space/booking、正常与 recovery Commit、到期 stop/revoke | 当前 HA 时钟组合；实际 GPU/traffic/物理强制 |
| Runtime / 协调 | 原 attempt/intent/stop、同事务受益 admission 的运行中抢占、耦合恢复和活动 hazard | 当前全局组合；设备 stop/handoff/regrasp |
| State / verifier | 来源/冲突/TTL/Belief 消费策略、原 Task/attempt/CAS proof 与独立目标 | 当前物理来源与持续效果 |
| Memory / Spatial | 每 Task import/use/revoke、Group handoff、Active Map、scoped typed-map/自动 MI、原 producer/GC 与完整冷恢复 | 当前域间/Archive 组合；实际跨主机字节与定位 |
| Replica / recovery | 原 child drain、V3 transfer、换证、learner/joint、升级/拒绝回退、完整内容、在线 private Archive 与原 GC | 最终多域 v0.31 整包验收；部署 RTO/RPO |
| Federation | 持久 root/真实 pinned peer/正常 Control、原 clock/intent/Unknown、全 Commit barrier 与独立目标；显式 peer v0.2 | 最终分区/换证/HA/内容组合；实际跨主机证据 |
| 运维 | 全 listener/本地 adapter 容量与有限等待、真实原生告警、默认 128 健康采样、明确 SLO 分母和 runbook | 当前完整 CI/main；长期 SLO 与人工响应 |

## 历史验收记录

下述记录保留其原版本和当时剩余项。当前闭合情况以上述队列与矩阵为准，旧待办不能否认后续实现。

原内容 Archive 及实际 Catalog 归档/重启/冷恢复见[本轮验收](docs/development/acceptance/2026-10-22-original-content-archive.md)。storage v3 的已知 consumer 清单、原预算与同 owner 内容一并校验；这些进程证据没有创建设备或外部模型事实。原全部非发布整改计划继续适用。

[原未发布 Archive 清理](docs/development/acceptance/2026-10-22-original-archive-abort.md)新增实际 writer fence、原 tombstone/SQL receipt 和 v49→v50 升级验证。partial 文件及接收方引用保留；清理原 copy 不授予删除接收内容的权限。

[私有接收 owner 回收](docs/development/acceptance/2026-10-22-private-archive-reclamation.md)单独核对全部原 writer、空的已知 consumer 清单和唯一 producer；终态 storage v4 fence 与原 tombstone 先于 unlink。v4 只表达该接收 owner 的不可逆终态，不能作为普通可写 CAS。

[旧 Catalog 显式接管](docs/development/acceptance/2026-10-22-original-catalog-adoption.md)固定原 publication raw envelope、实际 owner/bytes 和 typed parent；当前 admin/TLS 另记 receipt。历史 publisher/tenant 仍 unavailable，不授予额外 read/use/import 权限。wrapper v51/recovery v0.15 拒绝丢失原 receipt。

[源 Artifact 离线回收](docs/development/acceptance/2026-10-23-source-artifact-reclamation.md)使用 wrapper v52/storage v5/recovery v0.16：原上传、Catalog/child、Memory/MI、Snapshot/Archive 与验证引用分别核对；原 tombstone 先于 unlink。实际 MI policy 撤销、独立原目标、parent/child 释放、文件/SQL 故障和冷恢复通过。该入口拒绝 Replica/online 权威，不能代替多数派内容 ACK。

[Prepared 恢复与重复校验优化](docs/development/acceptance/2026-10-24-prepared-recovery-and-runtime-latency.md)已通过实际三副本/Controller/SQL/CAS 故障检查，未创建设备。原 Prepared 与 Unknown 保留，新 owner 通过独立正常上传取得 Completed；最新权限和完整多数派 checkpoint 保护故障后的只读观察。最终恢复约 77 秒，仍需优化；短窗口结果不计长期 SLO。显式 v0.18 仅支持原 standalone 存储合同，不覆盖 parent8/Root4 或新增恢复事件。

[跨 owner Prepared 释放](docs/development/acceptance/2026-10-24-cross-owner-prepared-release.md)另通过真实故障后 child wait/restart、DELETE 和重复请求检查。原上传输入已释放，原 origin/Unknown 与独立 Root pins/bytes 保留。Completed 退休由下述 ADR-0182 接续；完整在线恢复仍待完成。

## 如何解读证据

[原恢复事实对账](docs/development/acceptance/2026-10-24-original-recovery-assessment.md)已通过故障、重启和 source 放弃后的实际只读检查：独立 replacement Completed 与原 Unknown 分别保留，缺记录是 unavailable，退休回执另列。它不补完成或重开工作；完整 Root 绑定/恢复仍待完成。

[Completed 跨 owner 退休](docs/development/acceptance/2026-10-24-cross-owner-completed-retirement.md)已通过实际 Root 完整性读取、原 owner receipt、重复请求和独立 SQL/文件复核。原上传引用生命周期已闭合，Root 生产者回收与完整在线 HA 仍独立待完成。

[v53 standalone 兼容接线](docs/development/acceptance/2026-10-24-recovery-profile-propagation.md)已通过 GC SQL 的10类实际故障、接管/源回收及 Catalog/child/Archive 冷恢复；原 v0.16 和 parent8/Root4 边界保留。实际软件 Node 只执行文件/CPU 工作，设备、外部模型、模拟器为0。

- **合同已接受**：决策语义确定，不表示全部实现完成。
- **代码已实现**：存在实际调用链，不等于在用户部署中验证。
- **离线/进程验收**：确定性、真实文件、SQL、TLS、CPU child 等分别证明其范围内的软件事实。
- **当前部署或物理验证**：需要当前真实来源、设备、拓扑和独立观测；缺失仍是 unavailable。

内部 TaskSatisfied 与 Habitat 官方指标是不同判定。历史实验的分母、冻结模型/代码和公平性限制保留在原报告，不从软件检查推算 benchmark 收益。

## 状态维护

按“合同 → 实际代码 → 原始验收 → 当前缺口”更新本页，中英文同提交维护。依赖版本由[构建参考](reference.md)提取。不能仅因合并、类型定义、测试替身或 ADR 状态而宣布 SoftwareComplete。

[Scoped typed-map 完整软件验收](docs/development/acceptance/2026-10-25-scoped-typed-map-lifecycle.md)通过两条实际 retained-storage 进程链和完整 v0.26 冷恢复。每个 Task 有独立 preparation/import/use，原生产信息保持不变。设备、跨主机与其他阶段证据独立。

[时空资源软件验收](docs/development/acceptance/2026-10-25-resource-topology.md)通过明确部署声明、跨 ID 冲突、原预算与活动/未确认停止恢复。实际 CPU 进程不能证明物理排他或设备停止；其余 A/E/F 继续必需。

[运行协调验收](docs/development/acceptance/2026-10-25-execution-coordination.md)通过真实 CPU 抢占、耦合恢复、文件完整性 hazard、原实例重启和完整声明数据冷恢复。设备协调保留原 reviewed Memory 与独立设备 assurance 门禁；没有设备或外部模型。

[跨域软件验收](docs/development/acceptance/2026-10-25-federation-execution.md)通过正常执行、真实 Prepare 响应丢失、原 Root SIGKILL、完成后原实例重启和六份声明数据冷恢复。只读观察不声称瞬时全局就绪；实际执行仍消费原完整决定、全体 Commit 回执和本域当前门禁。
