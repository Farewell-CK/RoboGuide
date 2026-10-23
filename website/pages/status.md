# 实现状态与证据边界

内容核对基线：`815ffcd`（ADR-0177–0180）（2026-10-24）；[版本参考](reference.md)记录完整代码基线。下表依据代码与既有验收档案；原始存量详见[10 月 22 日核对](docs/development/roboguide-branch-audit-2026-10-22.md)。

| 方面 | 已有软件路径 | 仍需完成或独立验证 |
| --- | --- | --- |
| MI / Orchestration | 澄清、Review、计划接纳、DAG、satisfaction、版本化运行期修订与自动 MI work | 复杂拓扑、其他 Memory 类型与复合故障矩阵 |
| Control / quota | Match/Commit/Bind、calendar、恢复、tenant/category quota 原子记账与 usage view | divisible allocation、全资源本地强制、完整时空/成本证据 |
| Runtime / stop | attempt/generation、durable intent、relations、whole-Group stop、原进程 exit proof | 运行中抢占、耦合多 Role 迁移、hazard/regrasp 与设备 stop |
| State / verifier | 来源策略、Belief lineage、TTL、原 Task verifier 与 CAS proof | 当前部署的物理来源、冲突与持续效果证明 |
| Memory / Spatial | 准备/use/revoke/handoff、文件空间算法、provider 局部 GC；原 import Replica、Catalog/lineage、Snapshot/Archive 引用、恢复后原 provider Snapshot 退休、Archive abort/私有接收 GC、源上传退休和 standalone 已知引用闭包离线 GC | Raft/online 全部生产者覆盖、跨主机内容与组合恢复 |
| Replica / recovery | 证书轮换、原 child drain、V3 transfer、writer/inode fence、已支持静止 bundle、qualified Root HTTP read、Prepared 显式恢复 | 跨 owner 上传释放、Unknown 对账、parent8/Root4 bundle、完整 readiness/在线恢复、升级回退与跨主机故障矩阵 |
| Federation | Control 内持久 session | 真实跨域 transport、授权、路由与双域恢复 |
| 运维 | write-disabled、已提交视图、恢复探针、真实短窗口监控检查 | 全 listener/阻塞收敛、完整 runbook、长期 SLO 与总体退出条件 |

原内容 Archive 及实际 Catalog 归档/重启/冷恢复见[本轮验收](docs/development/acceptance/2026-10-22-original-content-archive.md)。storage v3 的已知 consumer 清单、原预算与同 owner 内容一并校验；这些进程证据没有创建设备或外部模型事实。原全部非发布整改计划继续适用。

[原未发布 Archive 清理](docs/development/acceptance/2026-10-22-original-archive-abort.md)新增实际 writer fence、原 tombstone/SQL receipt 和 v49→v50 升级验证。partial 文件及接收方引用保留；清理原 copy 不授予删除接收内容的权限。

[私有接收 owner 回收](docs/development/acceptance/2026-10-22-private-archive-reclamation.md)单独核对全部原 writer、空的已知 consumer 清单和唯一 producer；终态 storage v4 fence 与原 tombstone 先于 unlink。v4 只表达该接收 owner 的不可逆终态，不能作为普通可写 CAS。

[旧 Catalog 显式接管](docs/development/acceptance/2026-10-22-original-catalog-adoption.md)固定原 publication raw envelope、实际 owner/bytes 和 typed parent；当前 admin/TLS 另记 receipt。历史 publisher/tenant 仍 unavailable，不授予额外 read/use/import 权限。wrapper v51/recovery v0.15 拒绝丢失原 receipt。

[源 Artifact 离线回收](docs/development/acceptance/2026-10-23-source-artifact-reclamation.md)使用 wrapper v52/storage v5/recovery v0.16：原上传、Catalog/child、Memory/MI、Snapshot/Archive 与验证引用分别核对；原 tombstone 先于 unlink。实际 MI policy 撤销、独立原目标、parent/child 释放、文件/SQL 故障和冷恢复通过。该入口拒绝 Replica/online 权威，不能代替多数派内容 ACK。

[Prepared 恢复与重复校验优化](docs/development/acceptance/2026-10-24-prepared-recovery-and-runtime-latency.md)已通过实际三副本/Controller/SQL/CAS 故障检查，未创建设备。原 Prepared 与 Unknown 保留，新 owner 通过独立正常上传取得 Completed；最新权限和完整多数派 checkpoint 保护故障后的只读观察。最终恢复约 77 秒，仍需优化；短窗口结果不计长期 SLO。显式 v0.18 仅支持原 standalone 存储合同，不覆盖 parent8/Root4 或新增恢复事件。

[跨 owner Prepared 释放](docs/development/acceptance/2026-10-24-cross-owner-prepared-release.md)另通过真实故障后 child wait/restart、DELETE 和重复请求检查。原上传输入已释放，原 origin/Unknown 与独立 Root pins/bytes 保留。Completed 上传退休和完整在线恢复仍待完成。

## 如何解读证据

[v53 standalone 兼容接线](docs/development/acceptance/2026-10-24-recovery-profile-propagation.md)已通过 GC SQL 的10类实际故障、接管/源回收及 Catalog/child/Archive 冷恢复；原 v0.16 和 parent8/Root4 边界保留。实际软件 Node 只执行文件/CPU 工作，设备、外部模型、模拟器为0。

- **合同已接受**：决策语义确定，不表示全部实现完成。
- **代码已实现**：存在实际调用链，不等于在用户部署中验证。
- **离线/进程验收**：确定性、真实文件、SQL、TLS、CPU child 等分别证明其范围内的软件事实。
- **当前部署或物理验证**：需要当前真实来源、设备、拓扑和独立观测；缺失仍是 unavailable。

内部 TaskSatisfied 与 Habitat 官方指标是不同判定。历史实验的分母、冻结模型/代码和公平性限制保留在原报告，不从软件检查推算 benchmark 收益。

## 状态维护

按“合同 → 实际代码 → 原始验收 → 当前缺口”更新本页，中英文同提交维护。依赖版本由[构建参考](reference.md)提取。不能仅因合并、类型定义、测试替身或 ADR 状态而宣布 SoftwareComplete。
