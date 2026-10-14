# RoboGuide 生产候选版整改实施计划

**状态：** 整改进行中；S1–S5 部分接入，S2 的 HA composition 和 S3 的软件停止闭环仍有 P0 缺口。

**原审计基线：** `af6bf3428e6ebc03afc2fdaf2572449b397cc18a0`

**当前复核基线：** `main@5285a92a6af8bf4a99bb0852b5cdb80728c9144c` 加未提交整改工作树

**范围：** Core、Mission、Integration、Node Service、State/Memory、Controller、运维边界
**排除：** `experiments/`、E6、模型效果、正式真机性能和实验结果

本计划采用 **ADR → 实现 → 验收 → 部署演练** 流程。已完成的 O1–O8、S1–S13 和对应 ADR 不重新实施；软件证据、合成进程证据和物理证据始终分开记录。

**实施状态复核（2026-10-14）：** Command Journal、入口安全、Memory import 和 State readiness consumer 已有实际调用链。relation stop 仍缺 Mission hold、完整安全证据校验、deadline timer 和全释放/恢复路径 gate；Controller v27 与 HA supervisor v26 尚未统一。此前库级/模块测试不能关闭这些缺口。逐项证据见[原审计问题整改状态](roboguide-remediation-status-2026-10-14.md)，下一步工作与关闭条件见[剩余整改清单](roboguide-remediation-remaining-2026-10-14.md)。

## 目标和固定边界

- 目标等级是生产候选版，不等同于物理安全或任意异构设备交接已经完成。
- Mission、Artifact、Controller 可远程绑定时都必须使用服务内 mTLS、角色和请求主体校验；默认 loopback 开发模式保留。
- Control 使用 Command Journal + Checkpoint，不在本阶段重写为完整 Event Sourcing。
- Controller HA 目标包含动态成员、证书轮换和 Leader 迁移；跨站点和物理连续性仍需独立验收。
- Memory 平台完善 manifest、权限、provider、导入导出、检索、验证和生命周期；不同设备、不同高度和不同坐标系的地图保持独立，不自动拼接成全局地图。
- 运维验收必须包含 readiness、指标、SLO、告警和故障演练。

## 阶段和状态

| 阶段 | 交付物 | 状态 | 退出条件 |
| --- | --- | --- | --- |
| S0 基线与权威矩阵 | DeploymentProfile、authority matrix、编号化 backlog | 进行中：文档草案 | 整改计划、authority matrix、ADR-0095、`deployment-profile-v0.1.md` 已落地；具体部署值、MVP 与退出条件尚未冻结 |
| S1 入口安全 | Mission/Artifact mTLS、角色、principal、审计和非 loopback 启动门禁 | 部分实现 | tenant/幂等/Artifact grants 已接入；pending 结果核对、tenant 生命周期、跨服务授权与部署证书 profile 待关闭 |
| S2 Control durability | Command Journal、checkpoint high-water、恢复状态和复制接线 | Journal 已接入；HA composition 未闭合 | v27 Controller/v26 supervisor 必须统一并完成实际进程验收；不能以库级 quorum/snapshot 测试替代 |
| S3 物理安全合同 | Stop/SafeState/Confirmation 通用协议 | 部分接入；P0 未关闭 | 先补 Mission hold、receipt 状态机、record 自身来源/时效校验、deadline timer、全部释放/resume gate 和竞态测试；物理验收独立 |
| S4 Memory 平台 | durable selective import、ACK、retry fence、ACL、retention、独立地图管理 | Controller/Node 软件链路部分实现 | exact binding、receipt、Unknown fence、worker 和 ACL 已接入；Unknown reconciliation、active-map/GC/revocation、完整 Group 授权待完成；handoff 后置 |
| S5 State Authority | DecisionEvidencePolicy、provider 冲突归约、freshness/confidence 门禁 | 已有实际 readiness consumer | Memory import readiness 已实际消费 policy；通用 Match/Commit/Rebind/Satisfaction registry 和 provenance 审计待完成 |
| S6 动态身份与 HA | identity lifecycle、动态成员、leader 迁移、snapshot/archive replay | 动态能力待实施 | 先依赖 S2 composition 修复；随后验收 join/remove、leader transfer、证书生命周期、升级和数据面恢复；authority/evidence/physical continuity 分别报告 |
| S7 调度优化 | load、queue、GPU、travel、traffic、spatial evidence | 后置 | 不改变 Scheduler 纯函数和 Control ownership 边界 |
| S8 运维与发布 | readyz、metrics、SLO、告警、故障演练、部署和发布材料 | 部分实现 | 统一 JSON/text 指标采集、补齐告警/演练、生产部署/升级/回退、文档/schema 同步、LICENSE 和提交验收 |

下一步先关闭剩余清单的 R01（S3 停止闭环）和 R02（S2/S6 schema 接线）。S0 冻结材料与 S1/S4/S5 可独立推进；S6 动态能力必须建立在已验收的 S2 composition 上。S7 按冻结 MVP 选择成本维度，S8 随各项接入，不能留到发布时才验证。

## S0：基线和权威矩阵

新增或维护以下材料：

- `DeploymentProfile`：共址/跨主机、持久化目录、故障域、信任边界、网络路径、RTO/RPO；
- `docs/development/deployment-profile-v0.1.md`：生产候选拓扑、持久化、信任边界和候选 RTO/RPO；
- Authority Matrix：每个状态、命令、projection 和 evidence 的唯一写入者；
- Remediation Register：问题、ADR、代码 owner、依赖、测试、acceptance artifact 和 `TEST GAP`；
- 已完成 O/S 工作包映射，防止把已实现软件能力重复列为缺陷。

## S1：入口安全

Mission Service 增加 mTLS listener、principal/tenant/role、idempotency key 和 durable audit。Artifact HTTP 增加 mTLS listener、读写权限、provider/manifest/epoch 绑定。Controller 复用已有 operator mTLS 的证书 digest 和角色校验原则。

默认 loopback 是受限开发 profile；非 loopback 必须具备完整的 server cert、private key、client CA 和 role map，缺一项即启动失败。Header 只能携带可核对的上下文，不能授予角色。

## S2：Control durability

新增 Control authority command journal，记录 `command_id`、`authority_revision`、`controller_epoch`、`plan_digest`、`input_digest`、`ownership_digest` 和 `resulting_state_digest`。恢复顺序固定为 checkpoint → journal high-water → authority replay → Runtime reconciliation → State projection refresh。任何不一致都拒绝重新接收 mutation。

## S3：物理安全合同

目标状态序列：`StopRequested → StopCommandAccepted → SafeStateObserved → StopConfirmed → ContinuationPrepared | GroupStopped | ManualEscalation`。`core/domain/src/physical_safety.rs` 已有 exact identity、deadline 和阶段合同，但 Application 仍使用独立的字符串 phase purpose，没有完成该合同的业务接入。剩余清单 R01 列出实际差距与专项测试条件。`CancelAccepted` 不代表 `StopConfirmed`；未确认不得释放或自动重放。这是待闭合的验收要求，不能由 Domain 类型存在推定已经满足。

## S4：Memory 平台

实现 durable `Controller → Node` selective-import command、provider authorization、application ACK、retry fence 和 Task/Group/attempt provenance。当前已落地 `contracts/node/v0.9` 在 v0.4 session 上的 additive `MemoryImport`/`MemoryImportReceipt` extension、Node 注册 opt-in、authorization digest、Node SQLite import journal，以及 `Pending → Completed | Rejected | Unknown` 的重启/重试 fence；Controller 侧发送入口是 `GrpcNodeRouter::import_memory`。旧 Node 不声明 extension 时保持拒绝。Memory catalog 支持 schema、provider、visibility、scope、placement、retention、revocation、audit 和 snapshot/replay 的完整生命周期仍需继续接入。

Spatial Memory 采用独立 `MapId`、`MapRevisionId`、frame、anchor、provider 和 digest。系统可以管理多张地图、显式建立关联并验证导入，不自动将不同设备或不同高度地图拼接为一张地图。

## S5：State Authority

为可影响 Match、Commit、Rebind、Readiness 和 Satisfaction 的 key 定义 `DecisionEvidencePolicy`：allowed sources、freshness、confidence、conflict reducer、allowed decisions 和 unknown/stale behavior。State 默认保持 evidence federation；未通过 policy 的 evidence 不能改变 authority。当前 `core/state` 已实现可复用的 v0.1 policy evaluator，`IntegrationRuntimeBridge::evaluate_decision_evidence` 提供了不融合来源的 composition consumer facade；deployment policy registry 和每类 authority transaction 的 acceptance gate 仍是下一步。

## S6：身份与 HA

分离 `NodeIdentity`、`LocalSystemIdentity` 和 `PhysicalEntityIdentity`，分别支持注册、轮换、撤销、anti-rollback 和恢复。Controller HA 实现动态 join/remove、leader transfer、term/epoch、证书轮换、quorum loss、rejoin 和 snapshot/archive replay。

先修复 Controller v27 与 supervisor/进程工具 v26 不一致，再实施动态能力。HA 以三项 gate 报告：`AuthorityContinuity`、`EvidenceContinuity`、`PhysicalContinuity`。只完成前两项不能报告设备执行连续性。

## S7：调度优化

新增独立 evidence provider，输入 load、queue、GPU memory、duration、travel、traffic、spatial occupancy、fairness 和 temporal confidence。Scheduler 仍只执行 `CandidateSet + EvidenceSnapshot + CalendarSnapshot → Proposal`，不直接 Match、Commit、Bind、释放资源或选择 Local How。

## S8：运维和发布

区分 `healthz`、`readyz` 和 degraded 状态，建立 Mission/Runtime/Control/Memory/Replica/Archive 指标、SLO、告警和结构化 audit log。当前 Controller `/metrics` 为文本，Mission/Artifact 为 JSON；端点存在不等于已接入统一采集告警。`production-candidate-slo-and-drills-v0.1.md` 只是候选阈值和演练矩阵，实际采集、告警路由、跨主机演练和发布清单仍待完成。

## 统一验收门槛

每个工作包必须提交 ADR 或 ADR 修订、schema/API 变更、迁移说明、正常和故障测试、acceptance artifact、当前 SHA、复现命令、`TEST GAP` 和 `NOT PROVEN`。

总验收必须满足：Rust workspace tests、fmt、Clippy、Rustdoc、Mission/Memory/HTTP/HA 检查通过；所有可远程绑定服务有 mTLS negative test；Control journal、Memory import 和 HA 故障演练可恢复；StopConfirmed 前不释放物理 ownership；实验不作为整改成功证据。

## 后置能力

自动跨设备/跨高度地图融合、任意异构机器人自动交接、未经 StopConfirmed 的运行中抢占、用 State 代替 Global Truth、用 checkpoint 代替物理连续性证明、用模型输出代替验证，均不作为本轮生产候选的必需能力。
