# 原审计问题整改状态复核

本文是 2026-10-14 的历史复核快照。2026-10-15 已开始执行全部非发布软件目标，
进展与当前验收见[实施计划](roboguide-software-completion-plan-2026-10-15.md)。
本文中的 v26/v27 缺口和 pending 入口描述不能作为后续实现的当前状态。

复核日期：2026-10-14。对象：`main@5285a92a6af8bf4a99bb0852b5cdb80728c9144c` 加当前未提交工作树。范围：用户原审计表的 14 个模块；排除 `experiments/`、E6、模型效果、实验结果和物理性能。

**当前结论：仍未全部关闭，停止协议与 HA composition 还有软件正确性缺口。** Command Journal 已接入 Checkpoint、复制批次、snapshot 和恢复校验；Mission/Artifact 入口已有 mTLS、主体权限、Request tenant 和幂等路径；Memory import 已有 Controller intent、receipt reducer、exact current binding 和 Node side-effect fence。relation stop 只是部分接入，不能称为完整的软件停止闭环；Controller v27 与 supervisor v26 的组合也尚未闭合。

完整剩余工作、代码证据、P0/P1/P2 和逐项关闭条件见
[原审计剩余整改清单](roboguide-remediation-remaining-2026-10-14.md)。此前整改记录包含离线
Rust/Python、HTTP/TLS、复制和 Node Memory 测试；本次状态复核是源码/配置/测试源码核对，
未重新运行这些测试，也没有新的跨主机或物理验收证据。

## 对照原审计

| 层 / 模块 | 当前状态 | 已落地调用链 | 仍未关闭 |
| --- | --- | --- | --- |
| 产品定义与部署拓扑 | 部分实现 | authority matrix、deployment profile、候选 RTO/RPO、ADR 已维护 | MVP、真实拓扑、持久化/故障域、指标和退出条件仍未冻结 |
| Mission Service 入口 | 软件路径已接入 | mTLS、证书主体、角色、tenant 原子绑定、远程 mutation 幂等、audit、依赖 readiness、TLS 负向测试源码 | HTTP pending 结果核对、tenant 生命周期/迁移、跨服务授权范围、正式证书 profile |
| Mission Intelligence | 部分实现 | Catalog、Grounding/draft/review digest、satisfaction policy、bounded pre-submission repair | 通用 Belief、通用 verifier ingress、模型/Provider/Catalog/config/policy 全量持久化 provenance |
| Orchestration | 部分实现 | DAG、priority/aging、isolation、recovery、durable dispatch 继续由既有 Orchestration 权威执行 | relation stop 的 hold/全释放路径 gate、durable failure 可用性策略；执行期重规划/运行中抢占后置 |
| Control Plane | Journal 已接入，HA composition 有缺口 | Journal revision 独立于 event sequence；metadata、checkpoint high-water、quorum v2 DTO、snapshot/materialization 和 response-loss recovery 基础 | Controller v27/supervisor v26 不一致、真实进程验收、动态 membership、leader transfer、证书轮换 |
| Scheduler | 保持现有确定性实现 | 联合搜索、future reservation、priority/aging、commit authority 边界保持不变 | load/GPU/queue/travel/traffic 成本、quota、运行中抢占、多角色复杂恢复 |
| Runtime | relation stop 部分接入，未关闭 RT-G9 | relation event → stop purpose → 现有 Cancel outbox；purpose 随 checkpoint 保存；取消 finalization 有局部 gate | Mission hold、receipt 状态机、安全记录自身 session/TTL/来源校验、deadline timer、完成/失败/恢复/释放竞态和专项测试；物理确认另行验收 |
| State / EventLog | 有实际消费但非全量 registry | policy 校验和 Memory readiness consumer 已接入；Journal 与 checkpoint 恢复门禁已接入 | 通用 Match/Commit/Rebind/Satisfaction policy registry、决策审计和完整 provenance |
| Memory / Spatial | selective import 软件链路已接入 | durable intent、receipt reducer、exact Group/Task/Role/attempt/provider、Node worker、Unknown retry fence、Artifact ACL 和故障测试源码 | Unknown reconciliation、active-map、GC/retention/revocation、完整 Group 授权；跨 Group handoff 后置 |
| Artifact HTTP | mTLS + 资源权限已接入 | 证书主体、Node/provider/memory/map/upload grants、主体 audit、readiness、TLS 负向测试源码 | 权限更新/撤销、生产证书轮换、跨服务授权和内容恢复 |
| Node Protocol / Integration | 部分实现 | Memory import/receipt、current session/epoch、旧版本 opt-in、worker 隔离和 identity fence | 注册中心生命周期、证书轮换/撤销、物理身份连续性、硬件 attestation |
| Node Service / Local Engine | import 恢复边界已接入 | 本地 journal、provider/workflow identity、Execute provenance、Unknown 不自动重跑、有界 worker | 通用 Local EAIOS safe-state/stop certificate 软件接入、系统 hazard/硬实时合同及独立部署验收；异构 handoff 后置 |
| Replication / HA | 库级基础已增强，composition 未闭合 | quorum、epoch fence、Application Commit v2、snapshot/history/command materialization 和自动选主 | v26/v27 配置接线、实际进程测试、动态 membership、leader transfer、证书轮换、archive/数据面恢复 |
| 运维与发布 | 部分实现 | Mission/Controller/Artifact readiness、部分聚合 metrics、audit、SLO/演练文档 | 指标格式/采集/告警统一、跨主机演练、生产部署/升级/回退、文档/schema 同步、项目 LICENSE 和提交验收 |

## 关键调用链证据

1. `application::safety_stop::admit` 从 `RelationReconciliationRequired` 进入现有 Runtime cancellation outbox，但没有建立 Mission hold。`reduce` 核对当前输入 session，未核对被选 State record 自身的 source epoch/TTL/operation-owned export；release gate 只进入 cancel finalization，普通 Task failure 和 resume 未消费。RT-G9 不能关闭。
2. `IntegrationRuntimeBridge::evaluate_decision_evidence` 被 `memory_import::authorize` 调用；不通过 policy 的 State 只能产生拒绝/Unknown。
3. `GrpcNodeRouter::import_memory` 由 `memory_import::flush` 在 Checkpoint Commit 后调用；`MemoryImportReceipt` 由 fact batch reducer 持久化后才完成协议 ACK。Node `Unknown` 保持副作用恢复 fence。
4. `save_controller_checkpoint` 将 controller epoch、plan digest、ownership digest 和 Journal row 写入同一事务；Application Commit v2 将 commands 带入 replica view/snapshot，wrapper v27 对旧权威作 schema fence。
5. Mission `/readyz` 检查 Mission SQLite writer 和 Controller `/readyz`；Controller readiness 检查 Journal/Checkpoint、quorum 和 Unknown degraded；Artifact readiness 检查 catalog、writer 和 authority storage。
6. Artifact TLS principal 保留 certificate digest，并按 Node/provider/Memory/map/upload grant 授权；Header 只能作为当前 session 的一致性证据，不能创造权限。
7. `apps/integration-server` 写 v27 checkpoint，`apps/controller-replica/src/config.rs` 和进程检查工具仍指定 v26；replica store 拒绝不同 schema 的 batch。先修该 composition 缺口，再补动态 membership、leader transfer 和 cert rotation。静态三成员自动选主不等于主动 leader transfer。

## 验收口径

阶段关闭必须同时具备：业务路径实际使用机制；权限与 identity/freshness fence 正确；命令、状态、证据在既有事务提交；中断、重启、响应丢失后可恢复且不重复危险副作用；正常、负向和故障测试通过。类型、接口、文档或全工作区测试单独都不能关闭缺口。

跨主机与物理验收独立记录 `TEST GAP` / `NOT PROVEN`；不使用实验结果代替整改成功证据。
