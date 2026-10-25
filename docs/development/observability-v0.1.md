# 运行指标、告警与排障 v0.1

对应 [ADR-0115](../decisions/0115-bounded-observability-and-alert-delivery.md)。指标、queue
和告警只表达软件观察，不能代替 Control reservation、Runtime ACK、State 来源或设备停止。
短窗口进程验收与长期 SLO 分开记账。

## 服务与权限

| 服务 | Prometheus 路径 | 最小读取身份 |
| --- | --- | --- |
| Controller | `/metrics` | 全局证书角色 `metrics-reader`；只允许 health/readiness/metrics |
| Mission | `/metrics/prometheus` | Mission authorization v0.2 的 `metrics-reader`、tenant=`*` |
| Artifact | `/metrics/prometheus` | 实际 Artifact 只读证书与当前审计/授权；不复用 Node writer |
| Replica supervisor | 显式 `metrics_address` 的 `/metrics` | 仅本机非零 loopback，远端从受控监控 agent 采集 |

Controller Operator authorization v0.2 明确 metrics-only 角色；原 v0.1 grants 保持兼容，
接纳 v0.2 后拒绝更高 revision 的 v0.1 回退。共享 wrapper v42 fences 旧 writer，v41
standalone 在原启动事务安全迁移。
旧 wrapper 若藏有 metrics-reader authority 则拒绝；原 process-exit、Memory、stop 和
verifier identity fence 全部保留。Node Contract v0.12/config v0.13/catalog v0.15 与 journal
v13 不变。复制升级不能通过单改配置 digest 绕过原 accepted-state/membership 合同。

Mission v0.1 原角色兼容读取；新角色必须显式 v0.2。v0.2 持久 grants 拒绝所有 v0.1
回退，包括提高 revision；metric certificate 不能访问 Request、执行创建/审批/取消。
JSON metrics 与 Prometheus metrics 共享门禁，未注册/撤销证书和 tenant viewer 均拒绝。
指标读取本身不需要全局业务管理员凭证。开发 raw loopback 是原显式本地信任配置。

## 已实现的信号

- write-enabled/generation、SQL count 可读性、原事件与 command high-water。
- 实际 pending Execute、Runtime Unknown、stop pending/manual/原 deadline overdue、
  Group recovery 与 Memory 原 provider reconciliation/handoff。
- 固定 kind/stage 的本地 duration histogram、checkpoint/scalar、queue depth/bytes/
  oldest age 与 coverage、observer dropped counter。
- Replica 实际 accepted revision、当前 supervisor 的 majority epoch 与 owned child。
  status 本身缺失/超过五秒/时间倒退时拒绝 scrape；重新读取不会刷新原 publication time。
- Node、Artifact、Mission 的实际连接 active/limit/rejected，加上原 Operator 观察。
  [入口容量合同](listener-capacity-v0.1.md)独立保留实际 permit、线程与网络预算。

统计标签不包含 Mission、Node、session、证书、路径或指令。聚合只 try-lock，观察失败不
阻塞实际工作；丢样、缺少 queue identity、超容量或 timestamp 缺失保持 unavailable。
当前 queue coverage 一旦无法证明完整，进程内保持不完整；不能用一条后续样本抹掉
丢失来源。无 duration 样本不生成虚构零延迟，观测不全不声明 p99/SLO 达标。

## 配置与部署

通过[独立生成工具](../../tools/observability/README.md)冻结实际地址、cluster/domain、
mTLS paths/server name 和采样/评估/pending/延迟/backlog 阈值。必须校验原生配置，
再由部署启动独立 Prometheus/Alertmanager 并分配独立监控存储目录。密钥不写入仓库；
重渲染使用新输出目录，保留原 profile 与输出 digest，经过相同校验后切换。

生成的规则覆盖 scrape/合同缺失、write-disabled、Unknown、stop/manual、Memory 原对账、
queue/observer 不完整、backlog/p99、跨声明 replica 的 majority epoch 与 revision lag。
部署必须为完整声明集采集所有目标；profile 不证明现实中不存在遗漏的服务或故障域。

配置外部收件端只生成声明，不授权助手向真实人员发送消息。实际软件门禁使用该次拥有的
private loopback webhook，独立保存 Alertmanager 原生 firing/resolved 与 Prometheus 查询。
生产 receiver 的凭证、外部路由和人工值守确认由部署的实际授权和演练完成。

## 告警处置

1. `ScrapeUnavailable`/`MetricsContractMissing`：核对真实 endpoint、mTLS leaf/有效期、
   角色/撤销、原配置 digest 与服务进程。HTTP 200 不等于包含正确指标；缺失不记为健康。
2. `AuthorityWriteDisabled`：保留原目录和 first failure，按[写入恢复指南](controller-write-recovery-v0.1.md)
   核对原 process/generation/checkpoint/journal。禁止用新 key 重发业务副作用。
3. `RuntimeUnknown`/`StopManual`：保留 ownership，核对原 attempt/receipt/safe-state；
   Cancel 接纳不能解除告警。只有独立真实证据与原恢复流程可以 release/repeat/handoff。
4. `MemoryUnresolved`：查询原 provider/instance/context 与不可续期预算；不替换 import ID
   或新 backend 伪装成原 completion/revoke。耗尽转人工，不延长期限。
5. `ObservationIncomplete`/`QueueBacklog`/`ControllerLatency`：先检查覆盖与实际 backlog，
   再检查 write gate、SQL/CAS I/O 和真实控制流。丢样无法支持已达标结论。
6. `ReplicaQuorumUnavailable`/`ReplicaRevisionLag`：核对原 majority/epoch、成员和 accepted
   prefix、内容可用性，再做受控迁移/升级。禁止在少数派强制启动第二 writer。
7. `NodeCapacitySaturated`/`ArtifactCapacitySaturated`/`MissionCapacitySaturated`/
   `TransportConnectionsRejected`：区分当前满载和五分钟拒绝增量，核对原连接/线程退出、
   timeout 和慢 SQL；先保留原命令并只读对账，再按实际主机容量变更部署限额。

恢复后保留 resolved 通知和原 incident 时间线；消息到达不证明人工确认或设备恢复。
30 天 99.9%、跨主机响应/恢复时限和人工 SLA 必须由实际窗口与完整故障矩阵统计验收，
不能从本机一次成功演练外推。实验、E6、模型效果和模拟设备不参与这些判据。
