# 实现状态与证据边界

内容核对基线：`cd1faaf9d30e18d71fac82960e370540fe857aca`（2026-10-22）。下表依据代码与既有验收档案；本次文档工作不重演设备或昂贵实验。原始存量详见[10 月 22 日核对](docs/development/roboguide-branch-audit-2026-10-22.md)。

| 方面 | 已有软件路径 | 仍需完成或独立验证 |
| --- | --- | --- |
| MI / Orchestration | 澄清、Review、计划接纳、DAG、satisfaction、版本化运行期修订与自动 MI work | 复杂拓扑、其他 Memory 类型与复合故障矩阵 |
| Control / quota | Match/Commit/Bind、calendar、恢复、tenant/category quota 原子记账与 usage view | divisible allocation、全资源本地强制、完整时空/成本证据 |
| Runtime / stop | attempt/generation、durable intent、relations、whole-Group stop、原进程 exit proof | 运行中抢占、耦合多 Role 迁移、hazard/regrasp 与设备 stop |
| State / verifier | 来源策略、Belief lineage、TTL、原 Task verifier 与 CAS proof | 当前部署的物理来源、冲突与持续效果证明 |
| Memory / Spatial | 准备/use/revoke/handoff、文件空间算法、provider 局部 GC；原 import Replica、Catalog/lineage、Snapshot/Archive 引用、active ref 与恢复 CAS 对账 | Archive abort/退休、Artifact GC、全部生产者覆盖、跨主机内容与组合恢复 |
| Replica / recovery | 证书轮换、原 child drain、V3 transfer、writer/inode fence、静止完整 bundle | binary/profile 升级回退、在线内容恢复与跨主机故障矩阵 |
| Federation | Control 内持久 session | 真实跨域 transport、授权、路由与双域恢复 |
| 运维 | write-disabled、已提交视图、恢复探针、真实短窗口监控检查 | 全 listener/阻塞收敛、完整 runbook、长期 SLO 与总体退出条件 |

原内容 Archive 及实际 Catalog 归档/重启/冷恢复见[本轮验收](docs/development/acceptance/2026-10-22-original-content-archive.md)。storage v3 的已知 consumer 清单、原预算与同 owner 内容一并校验；这些进程证据没有创建设备或外部模型事实。原全部非发布整改计划继续适用。

## 如何解读证据

- **合同已接受**：决策语义确定，不表示全部实现完成。
- **代码已实现**：存在实际调用链，不等于在用户部署中验证。
- **离线/进程验收**：确定性、真实文件、SQL、TLS、CPU child 等分别证明其范围内的软件事实。
- **当前部署或物理验证**：需要当前真实来源、设备、拓扑和独立观测；缺失仍是 unavailable。

内部 TaskSatisfied 与 Habitat 官方指标是不同判定。历史实验的分母、冻结模型/代码和公平性限制保留在原报告，不从软件检查推算 benchmark 收益。

## 状态维护

按“合同 → 实际代码 → 原始验收 → 当前缺口”更新本页，中英文同提交维护。依赖版本由[构建参考](reference.md)提取。不能仅因合并、类型定义、测试替身或 ADR 状态而宣布 SoftwareComplete。
