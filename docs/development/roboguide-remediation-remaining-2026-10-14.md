# RoboGuide 原审计剩余整改清单

本文保留 2026-10-14 的审计证据。用户于 2026-10-15 已授权全部非发布项，原 P2
同样必须交付；当前实施和验收状态以[新计划](roboguide-software-completion-plan-2026-10-15.md)为准。

复核日期：2026-10-14。复核基线：`main@5285a92a6af8bf4a99bb0852b5cdb80728c9144c`
加当前未提交工作树；该 SHA 单独不包含本轮整改代码。

范围是[原分层审计](../architecture/v2/roboguide-distributed-embodied-os-audit-2026-10-14.md)
的 14 层。本文记录剩余软件工作、产品决策和部署验收条件；不启动、修改或使用实验、E6、
模型效果、实验结果作为证据。本次依据当前代码、调用链、配置和测试源码复核，未重新运行
Rust/Python 全套测试或 Controller/Node 进程演练。

**结论：整改尚未全部完成，而且部分缺口仍在软件实现中，不能仅归为真机验证待补。**
入口认证、Command Journal 和 selective import 已有实际实现；停止协议仍未形成完整软件
闭环，当前 HA composition 还有 v26/v27 版本不一致。先关闭这两项，再推进动态 HA、
State 决策策略、Memory 生命周期和发布验收。

状态口径：`部分实现` 表示已有代码，但目标调用链或恢复规则未完整闭合；`未实现` 表示目标
能力尚未提供；`待决策` 表示需要冻结产品或部署要求；`TEST GAP` 表示缺少对应验收测试；
`NOT PROVEN` 表示不能据当前软件证据认定部署、物理安全或连续性成立。P0 是当前软件
正确性阻断项，P1 是生产候选范围内的整改和验收项，P2 是后置能力。P1 不代表都必须立即
增加实现，先由部署 profile 明确哪些能力需要交付。

## 1. 按原 14 层对照

| 层 / 模块 | 已有基础 | 剩余工作 | 对应项 |
| --- | --- | --- | --- |
| 产品定义与部署拓扑 | authority matrix、候选 deployment profile、RTO/RPO 和演练草案 | 完整 MVP、实际组件共址/跨主机关系、持久化目录、故障域、入口路由、指标和退出条件未冻结 | R03、R19 |
| Mission Service 入口 | mTLS、证书角色、Request tenant 绑定、远程 mutation 幂等与审计 | HTTP 幂等 `pending` 的结果核对/处置入口；tenant 和证书权限生命周期；跨服务身份授权的范围声明 | R04、R05、R14 |
| Mission Intelligence | Catalog、Grounding snapshot/digest、Review/Repair 和 satisfaction policy | 模型/Provider/Catalog/配置/策略的完整持久化 provenance；通用 verifier ingress；显式 Belief provider/冲突策略 | R06、R07、R08 |
| Orchestration | DAG、priority/aging、Mission isolation、显式恢复和 durable outbox | relation stop 的 Mission hold、失败/取消/恢复竞态；持久化故障的服务和人工介入策略；执行期自动重规划为后置能力 | R01、R09、R20 |
| Control Plane | Match → Propose → Commit → Bind/Rebind、唯一 reservation authority、Journal/Checkpoint | HA composition 的 schema 一致性；跨副本升级/接管合同；未来 State policy registry 的 authority 接入 | R02、R08、R13 |
| Scheduler | 有界联合搜索、duration evidence、future reservation 和未来窗口抢占 | load/GPU/queue/travel/traffic 的有来源成本模型和完整时空资源语义；可分配 quota、运行中抢占和耦合恢复为后置能力 | R16、R20 |
| Runtime | attempt history、Unknown fence、relation checkpoint、Cancel outbox 和部分 stop purpose | exact stop receipt/安全证据状态机、时限驱动、全退出路径 gate、重启/旧 session/完成竞态；新的 relation-stop 专项测试 | R01、R17 |
| State / EventLog | source-aware federation、TTL、checkpoint/Journaling、policy evaluator | 部署策略 registry、Match/Commit/Rebind/Satisfaction 消费门禁、来源/冲突/决策审计；完整 archive restore 与 catalog replay | R08、R15 |
| Memory / Spatial | CAS/manifest、provider-qualified replica evidence、durable selective import/receipt | Unknown import 的显式 reconciliation；active-map、retention/GC/revocation、完整 Group 授权；跨 Group handoff 后置 | R10、R11、R12、R15 |
| Artifact HTTP | 非 loopback mTLS、证书主体与 Node/provider/resource grants、审计 | 权限撤销/更新与生产证书配置；内容可用性和恢复；与 tenant/Group 授权的贯通范围 | R05、R11、R14、R15 |
| Node Protocol / Integration | session/sequence/application ACK、Node 证书映射、Memory import extension | 注册/退役/撤销/轮换生命周期、动态配置版本、软件/物理身份连续性；硬件 attestation 未证明 | R14、R17 |
| Node Service / Local Engine | 本地 execution/import journal、provider/workflow fence、Unknown 不自动重跑 | Local EAIOS safe-state export 和 stop certificate 的通用配置与接入；系统 hazard/硬实时合同和部署验收；异构交接后置 | R01、R10、R17、R20 |
| Replication / HA | 静态三成员 Raft、自动选主、quorum/epoch fence、snapshot/materialization 基础 | v27 进程接线；动态 join/remove、主动 leader transfer、证书轮换、升级协议；archive 和 Artifact/Memory 数据面恢复 | R02、R13、R14、R15 |
| 运维与发布 | health/readiness、部分聚合 metrics、recovery view、SLO/演练草案 | 指标格式和采集统一、告警接线、实际恢复演练、部署/升级/回退清单、项目 LICENSE、文档/schema 同步和提交验收 | R18、R19 |

## 2. P0：先关闭的软件缺口

### R01：停止协议仍是部分实现

涉及 S3、Orchestration、Runtime、State 和 Local EAIOS。已接入的路径是
`RelationReconciliationRequired → safety_stop::admit → Runtime Cancel outbox`，停止 purpose
也进入 checkpoint。以下是代码可直接确认的剩余问题：

| 缺口 | 当前证据 | 必须补齐的行为 |
| --- | --- | --- |
| Mission hold 与所有释放路径未闭合 | `admit` 请求 Group cancellation，但没有建立 Mission hold；`blocks_release` 只在 Mission cancel finalization 使用，resume 和普通 Task failure 路径未消费该 gate | 停止目的必须形成 Mission 范围的执行 fence；失败、取消、satisfaction、resume、repeat、release 和 takeover 都核对同一停止权威 |
| 安全观察校验不足 | `reduce` 按 object/schema/value/Node 搜索记录，没有核对记录自身的 source epoch、LocalSystem/export、TTL、停止请求后的 receive time、置信度或冲突结果 | 冻结 operation-owned export descriptor 和策略，使用 exact-attempt、current-session、fresh receive-time evidence；旧观察和冲突不能确认停止 |
| 时限与阶段不完整 | timer 没有专门推进 stop deadline；任意该 Node 后续消息超过 deadline 会把已有 `stop_confirmed` 改为 `manual_escalation`；`stopped_at` 用当前输入 receive time，而非 Runtime 原始终态 receive time | 无新消息也能按原 deadline 升级未确认记录；保留首次终态时间和确认结果，定义晚到事实处理规则 |
| receipt/通用状态机未接通 | Application 使用字符串 phase；`SafetyStopRecord`/`SafetyStopPhase` 主要仍是 Domain 合同，没有在这条业务链推进 `StopCommandAccepted` | 接入版本化状态机、durable command receipt、证据摘要和完整 restore validation，禁止以 receipt 合成物理确认 |
| Unknown/失联处理会返回上层错误 | `admit` 拒绝 Unknown 或无当前 session；`apply_runtime_events` 直接传播 admission 错误 | 保留 ownership 和原证据，明确 Mission-local hold/manual escalation 与共享故障的边界，避免无定义的整体停服 |

证据：[stop application](../../apps/integration-server/src/application/safety_stop.rs)、
[relation event consumer](../../apps/integration-server/src/application/recovery.rs)、
[fact batch](../../apps/integration-server/src/application/fact_batch.rs)、
[timer](../../apps/integration-server/src/application/timer.rs)、
[outcomes](../../apps/integration-server/src/application/outcomes.rs)、
[resume](../../apps/integration-server/src/application/isolation.rs)、
[Task failure/release](../../core/orchestration/src/mission/lifecycle.rs)、
[Runtime outcome reduction](../../core/runtime/src/execution/observation.rs)。

这里存在明确的释放竞态：非 Mission-cancelling 情况下，relation 发出的 Cancelled 可以被
普通 Task outcome 归约成 Failed，继而进入 `task_failed → release_group`；此路径没有
`safety_stop::blocks_release`。这是调用链缺口，不是仅等待真机测停止距离。

关闭条件：覆盖 violation/unknown、无消息超时、旧 session、过期/请求前 safe-state、
conflicting evidence、Cancelled/Completed/Failed race、Mission cancel/resume、checkpoint restore
以及其他 Mission 继续工作的专项软件测试。未确认停止前不得释放或重复危险执行。

`TEST GAP`：未找到针对新 `SafetyStopPurpose` 链路上述边界的专项断言；既有 Role/Group recovery
和 Domain 状态机测试不能替代。`NOT PROVEN`：真实物理停止、hazard model 和硬实时保证。

### R02：当前 HA composition 的 v26/v27 不一致

涉及 S2/S6。Controller 的 `SERVER_CHECKPOINT_SCHEMA` 已是 v27，但 supervisor 的
`APPLICATION_SCHEMA` 和实际进程检查工具配置仍是 v26。`ProcessConfig::validate` 只允许
配置指定 v26，复制存储的 `apply_batch` 又要求 incoming checkpoint schema 与配置完全相等，
因此当前组合无法一致接纳 v27 批次。

证据：[Controller schema](../../apps/integration-server/src/main.rs)、
[supervisor config](../../apps/controller-replica/src/config.rs)、
[replica schema gate](../../core/controller-replication/src/store.rs)、
[实际进程检查工具](../../tools/quality/check_controller_replication_processes.py)。

关闭条件：统一 application schema、DTO、profile digest 和 migration 规则；明确旧库/旧副本
接纳与拒绝边界；重新完成真实 replica/Controller/Node 进程链的首次提交、零事件提交、
Leader loss、quorum loss、rejoin、Journal/snapshot materialization 和旧 epoch 发令拒绝验收。
库级复制测试通过不能关闭 composition 配置错误。

`TEST GAP`：本次没有重新运行实际进程检查；以上 schema 不一致由源码确认。

## 3. P1：产品和生产候选剩余工作

| ID / 阶段 | 当前状态与剩余交付 | 关闭条件 |
| --- | --- | --- |
| R03 / S0：MVP 与部署冻结 | `mvp-definition.md` 仍为 Draft；deployment/SLO 文件只是候选 profile，实际网络地址、组件共址、存储、故障域、恢复目标未冻结 | 项目负责人冻结目标 Mission、节点/角色/资源、正常流、故障矩阵、量化指标、Non-goals 和退出条件；实际 deployment digest 可追踪 |
| R04 / S1：HTTP pending 的恢复 | 幂等异常会保留 `pending` 并拒绝同 key 重入，这是正确 fence；但缺少专用的结果核对和人工处置流程，明确领域拒绝也需定义如何结束该 key | 通过已有 durable Request/Controller acceptance evidence 核对结果，恢复响应或保留可解释的阻断；不得删占位/换 key 自动重跑 Provider 或提交 |
| R05 / S1/S4：跨服务授权范围 | Request tenant 已绑定，Artifact 已有 exact resource grants；tenant 生命周期/迁移、Controller/Artifact/Memory 的主体授权贯通未闭合 | profile 明确单 tenant 或多 tenant 支持边界；若支持多 tenant，执行主体、Mission、Memory/Artifact 权限和管理员跨租户审计端到端可核对 |
| R06 / MI：完整 provenance | Grounding/draft/review digest 和 satisfaction policy 已存在；Request record 没有完整的模型、Provider、Catalog、配置与策略版本归档 | 每次 deliberation/repair/approval/submission 可关联冻结的 model/Provider/Catalog/config/policy identity 与 digest；旧记录的缺失保持 unavailable |
| R07 / MI/State：通用 verifier ingress | 已有 startup-frozen 本地文件 terminal verifier source 和正/负 verdict 消费；尚无通用产品级 verifier 接入和持续验证合同 | source 注册/授权、revision、Task/Role/attempt、freshness、finality、重复/冲突/晚到 verdict 和恢复均有版本化入口与审计；不改变本地完成与语义满足的区别 |
| R08 / S5：State/Belief 决策策略 | policy evaluator 已被可选 Memory readiness gate 消费；尚未成为全域策略 registry 或 Match/Commit/Rebind/Satisfaction 的统一接纳合同 | 部署冻结 allowed source/schema/freshness/confidence/decision/conflict policy；所有相关 authority consumer 保存策略和选中证据；Belief 融合由显式 provider 负责 |
| R09 / S2：持久化故障的可用性 | 共享/SQL/复制失败仍保守 fail closed，不能据此认定产品恢复服务已经完成 | 冻结不可写时的只读/degraded 行为、mutation 拒绝、恢复入口、原目录保护、人工介入 SLA 和恢复验收；恢复不制造新 Execute |
| R10 / S4：Memory Unknown reconciliation | durable intent、receipt、provider side-effect fence 已实现；Unknown 只保留晚到恢复 receipt，没有完整的 operator/provider 核对闭环 | 增加基于旧 import identity 的查询/证据核对和处置；有 provider 幂等/查询合同才允许恢复，不能换 identity 重做副作用；保留 deadline 和历史证据 |
| R11 / S4：Memory/Spatial 生命周期 | catalog/CAS/immutable map revision 已有；active-map、引用生命周期、retention/GC/revocation 仍缺完整 authority/replay 合同 | map 激活/失效与 localization、引用/所有权、删除前门禁、撤销传播和恢复可验证；CAS 存在不能证明已导入或可定位 |
| R12 / S4：完整 Group Memory 授权 | 当前 import 核对同 Group、当前 consumer binding/attempt/provider 和参与者；没有完整的成员变动、撤销和 handoff 合同 | 明确 scope/visibility/placement 的独立规则，授权覆盖 Commit、成员变化、Recovery 和 restore；跨 Group handoff 另列 P2，不隐式放行 |
| R13 / S6：动态 HA | 静态三成员、自动选主和 epoch/quorum fence 已有；动态 join/remove、主动 leader transfer、滚动升级未实现；supervisor 业务路由仍限定 loopback，Mission 的 Controller 客户端使用固定单入口 | 目标副本追上已提交权威，成员变动经一致性接纳；handover 状态可查询且幂等；旧 Leader 不写/不发命令；冻结跨主机服务入口、mTLS 与客户端切换方式；quorum loss 不授权强制接管；执行恢复保留 Unknown/ownership |
| R14 / S6：Node/证书生命周期 | Node 证书绑定和 Replica leaf pinning 已有；注册/退役、证书轮换/撤销、动态身份 profile 缺产品流程 | identity revision/anti-rollback、证书更新和撤销传播、旧 session/旧证书 fence、重启恢复和审计可验证；软件身份与物理设备 identity 分开记录 |
| R15 / S2/S4/S6：archive 和数据面恢复 | Raft snapshot/materialization 基础已有；完整 checkpoint + archive restore、catalog/localization replay、replicated archive maintenance 和 CAS/Memory 内容可用性未闭合 | 从保留 snapshot/archive 重建 authority 和全部依赖 catalog；缺失/损坏内容可报告；删除前有可恢复证明；HA 接管后旧 Artifact 引用仍能访问或明确不可用 |
| R16 / S7：真实调度 evidence/cost | 已有 duration estimate、确定性排序和少量部署 preference；缺通用 load/GPU memory/queue/travel/traffic/spatial occupancy 成本 provider | 独立 evidence snapshot 带来源、时效、不确定性和可解释成本；不改变候选资格或 Control ownership；按冻结 MVP 选需交付的成本维度 |
| R17 / S3/S6：Local EAIOS 安全接入 | Domain stop 合同不等于 Local Engine 已提供通用 stop certificate、safe-state 或系统 hazard 合同 | 明确 operation-owned export、stop/receipt/confirmation、断连处置、硬实时责任和设备恢复合同；软件先验收，再由独立部署证据证明真实停止与身份连续性 |
| R18 / S8：可运营的指标/告警 | Controller `/metrics` 是文本，Mission/Artifact `/metrics` 是 JSON；已有聚合观察，尚无完整采集、告警和 SLO 验收 | 统一可采集格式或提供明确 exporter；补延迟/错误/积压/Unknown/stop/replica/archive 指标，配置采集、告警阈值/路由与 runbook，完成部署演练 |
| R19 / S0/S8：发布完整性 | 原审计整改仍是未提交工作树；生产部署/升级/回退清单、当前 schema/AGENTS/README 同步和项目 LICENSE 未完成 | 当前 diff 经复核与必需质量检查；人类负责人确定 LICENSE 与产品冻结项；交付可复现配置、迁移/回退说明和对应提交/验收产物；不夹带实验目录 |

主要证据：

- R03：[MVP](../mvp-definition.md)、[deployment profile](deployment-profile-v0.1.md)、
  [SLO/演练草案](production-candidate-slo-and-drills-v0.1.md)。
- R04–R06：[Mission HTTP](../../mission/src/mission/api.py)、
  [Request store](../../mission/src/mission/request_store.py)、
  [Request record](../../mission/src/mission/request_record.py)、
  [satisfaction policy](../../mission/src/mission/satisfaction_policy.py)、
  [Artifact grants](../../apps/integration-server/src/artifact_http/authorization.rs)。
- R07–R08：[verifier source](../../apps/integration-server/src/application/verifier_source.rs)、
  [verifier consumer](../../apps/integration-server/src/application/task_verifier.rs)、
  [policy evaluator](../../core/state/src/decision_policy.rs)、
  [readiness consumer](../../apps/integration-server/src/application/memory_import.rs)。
- R09–R12：[application timer](../../apps/integration-server/src/application/timer.rs)、
  [Memory authority](../../apps/integration-server/src/application/memory_import.rs)、
  [import routes](../../apps/integration-server/src/controller_http/server.rs)、
  [Node import journal](../../core/node-service/src/journal/authority.rs)、
  [ADR-0100](../decisions/0100-selective-memory-import-authority.md)。
- R13–R15：[Replica config/epoch](../../core/controller-replication/src/lib.rs)、
  [HA route/profile 限制](../../apps/controller-replica/src/config.rs)、
  [supervisor](../../apps/controller-replica/src/supervisor.rs)、
  [Mission 固定 Controller 入口](../../mission/src/mission/controller.py)、
  [Node certificate identity](../../core/integration/src/grpc_server/mod.rs)、
  [archive gates](../../core/state/src/archive.rs)、
  [Runtime restore](../../core/runtime/src/execution/checkpoint.rs)。
- R16–R19：[Scheduler](../../core/control/src/scheduler/policy.rs)、
  [future preemption](../../core/control/src/calendar.rs)、
  [physical stop contract](../../core/domain/src/physical_safety.rs)、
  [Controller metrics](../../apps/integration-server/src/controller_http/server.rs)、
  [Artifact metrics](../../apps/integration-server/src/artifact_http/server.rs)、
  [发布准备审计](release-preparation-audit.md)。

## 4. R20 / P2：仍未实现，但不能混作当前版本必须完成

| 能力 | 当前边界 | 下一步前提 |
| --- | --- | --- |
| 执行期自动 MI 重规划与替代计划 | 当前 pre-submission repair/reconsideration 不授予执行期重写 accepted plan 的权力 | 显式版本化计划替换合同、旧 attempt/资源处置、审批与有界恢复 |
| 运行中物理抢占 | 已有抢占只针对未激活、未绑定、未提交的严格未来窗口 | R01/R17 停止证据、重复/交接授权及 Control 原子承诺 |
| 可分配 quota 与完整时空资源模型 | `units` 是整项资源选择的容量下限，不是可拆分份额；Time/Space 是当前 bootstrap profile | 新资源/配额语义与承诺模型 ADR，不把 Scheduler 成本当资源权威 |
| 耦合多 Role 恢复、异构 handoff/regrasp、跨 Group Memory handoff | 已有单 Role/独立 Session retained-binding 恢复，不证明任意联合迁移 | 完整 stop set、Context/Actor/物体 identity、安全与新的 Commit/Rebind 合同 |
| 自动跨设备/跨高度地图融合、实时增量同步 | immutable map revision、独立 frame/anchor 和显式 import 已有 | 显式 Spatial authority、融合/冲突策略与定位证据；不自动当成 Global Truth |
| 跨站点物理连续性、硬件 attestation、安全认证 | 当前软件日志、mTLS 和 checkpoint 不提供这些证明 | 明确产品要求、设备/信任根和独立部署验收；当前记为 NOT PROVEN |
| 层级 Control / Federation | 当前实现是一个逻辑 Control authority 与静态副本集 | 先明确 Q3 的适用场景和权威/Lease/故障域合同，不能把多副本当成多控制域 |

## 5. 后续实施与验收顺序

1. **先修 R01、R02。** 补停止证据和所有生命周期 gate，统一 HA schema 并验收实际进程调用链。
2. **完成 R03 的可审阅冻结材料，同时推进 R04–R08、R10–R12。** 先明确范围，再补恢复、
   provenance、verifier、State policy 和 Memory 生命周期，保留显式 authority 边界。
3. **推进 R13–R15。** 动态成员、主动 leader transfer、身份/证书生命周期以及 archive/数据面
   恢复必须覆盖失联、旧身份、响应丢失和重启。
4. **按冻结 MVP 选择 R16/R17，完成 R09、R18、R19。** 交付必要成本、安全接入、人工恢复、
   指标告警、部署/升级/回退和发布证据。MVP/许可证等负责人决策不得由自动化冒充完成。
5. **R20 单独立项。** 只有前置停止、授权和恢复条件成立，才扩展执行期重规划、运行中抢占和
   任意异构交接。

每个关闭项都需要当前提交/配置 identity、业务调用链、正常/负向/故障测试、恢复记录和可审阅
产物。通用 workspace 测试、文档中的 Accepted 或接口存在本身，都不能代替对应验收。
`AuthorityContinuity`、`EvidenceContinuity` 与 `PhysicalContinuity` 分别报告，不相互推断。
