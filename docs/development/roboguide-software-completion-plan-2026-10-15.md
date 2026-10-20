# RoboGuide 全部非发布整改实施与验收

状态：Accepted，实施中。用户于 2026-10-15 要求执行完整计划。
基线：`main@5285a92a6af8bf4a99bb0852b5cdb80728c9144c` 加已备份的未提交整改工作树。
实施分支：`codex/remediation-complete-software-2026-10-15`。

2026-10-20 后续连续实施使用 `codex/software-completion-2026-10-20` 隔离工作树，
各验收工作包完成后合入并同步 main。后续记录按实际合并提交与证据 identity 保留。

2026-10-20 集成工作在 `codex/main-integration-2026-10-20` 隔离工作树执行，目标合并 main；
包含 MI 部署校验和执行故障闭环分支。全部远程分支与当前必需剩余项见
[分支同步与剩余整改核对](roboguide-integration-status-2026-10-20.md)。
ADR-0111 统一独立分支冲突合同为 Node v0.12/config v0.13/catalog v0.15、wrapper v41/
journal v12，并保持旧 Memory 证据和原预算。集成完成不等于全部阶段 SoftwareComplete。
本工作包结果见 [main 分支集成与软件组合验收](acceptance/2026-10-20-main-integration.md)。

后续 [原 provider 回收与内容恢复验收](acceptance/2026-10-20-content-reclamation-and-recovery.md)
接通实际原引用/revoke/tombstone/unlink 对账、完整 Active Map 输入门禁、Node journal v13
唯一 writer，以及 6 stores/72 tables/实际 CAS 的静止恢复。该结果不关闭 F4/F5 的全局引用
与在线 HA 数据面，不改变 R01–R20 或后续必需交付。

[写入禁用与显式恢复](acceptance/2026-10-20-authority-write-recovery.md)接通 R09/F10 的本地
SQL 故障路径：原进程/代际、完整 checkpoint 与 journal revision、原主体和零事件探针
Commit；事实/ACK/命令被封住时保留最后提交查询。复制/catalog 不确定性仍经受控重放，
告警/SLO、写锁内 I/O 收敛和全部其余阶段继续必需。

[运行指标与实际告警验收](acceptance/2026-10-20-observability.md)补齐原生配置生成、受限
mTLS scrape、两次实际 SQL 故障的 firing/resolved、固定 histogram/queue/drop/stop 信号
与 replica status。wrapper v42/Mission 授权 v0.2 保护 metrics-only 角色和降级门禁；实际
短窗口不关闭长期 SLO、跨主机人工响应或写锁 I/O 优化，完整其余阶段仍必须实施。

## 交付要求

[原属组 Memory 计划修订](acceptance/2026-10-20-scoped-memory-plan-revision.md)接通显式
v0.3 许可、原 child/Review digest、完整停止/撤权和 Control 新组的正常 import/use。
实际正常/SQL/已确认停止后重启/策略/到期五条链路通过，旧 writer 拒绝且原 MI/迁移回归
通过；wrapper v45 fences 新权限。非空 MI 声明的整链及全局 Memory 数据面继续验收，
不以两条各自通过的路径替代组合，也不关闭完整 F8 或其他必需阶段。

[文件进程与原接收身份](acceptance/2026-10-20-file-process-memory-ownership.md)接通部署侧
独立 OS wait、原 kernel/use 读取许可、崩溃 Unknown/GC fence 和后续 Task 自己的 import。
实际双 Node 停止/交接/复算与四种生命周期、单 worker 内容/恢复/告警组合通过；源 session
变化的复合故障保留 hold，完整跨 session 停止恢复、F4/F8 及其余阶段继续必需。

[运行期修订与原目标验收](acceptance/2026-10-20-runtime-plan-revision.md)验证原计划/事实、
当前版本完整 attempt、真实进程停止、预审自动替代、独立目标、普通 resume 拒绝、取消和
重启，以及实际 v42/inner v22 → v43/inner v23 原数据迁移。完整新 MI/Memory 组合仍实施中。

[Operator 网络边界验收](acceptance/2026-10-20-operator-network-isolation.md)以真实单
worker/TLS/SQL 排队和旧源码反例验证：有界连接、原 writer 授权、慢 SQL 期间健康读取、
完整文件/告警与三副本恢复链。其他 listener 容量、完整 HA 升级及全部其余阶段继续必需。

[单 worker 与重复 transfer 验收](acceptance/2026-10-20-worker-and-transfer-liveness.md)
复现旧 timer/transfer 的三项反例，当前代码用 1 network worker 完成实际双 Node 文件/
监控与三副本证书/完整恢复。其他 Operator 同步工作、完整升级/在线恢复及其余阶段继续必需。

[完整副本静止恢复](acceptance/2026-10-20-replica-cold-recovery.md)验证实际全 writer 门禁、
6 SQL/39 tables/原换证历史与显式 CAS 内容恢复；三个实际 leader endpoint 读取相同原
内容/receipt。在线内容、版本升级与前两轮间歇 HTTP 中断仍须继续解决，不关闭完整 F5/F10。

[副本证书与根迁移验收](acceptance/2026-10-20-replica-certificates.md)完成 F5 的显式
v0.3 同一成员 Stage/Retire/Abort、实际全成员 TLS、CA overlap/removal 和原 store 重启。
896 项 Rust 与 2623 项 Python 通过；旧 v0.2 升级、二进制滚动升级、HA 内容恢复及其余
阶段仍为必需交付，不能从本项推断 SoftwareComplete。

原审计 14 层、R01–R20 的全部非发布工作均为必需项，原 P2 不再是本轮的排除项。
保留部署配置、升级迁移、回退保护、监控告警和故障演练；排除正式发版、公开分发、生产上线、
项目许可证决策和外部安全认证办理。实验、E6 和模型效果评测不参与验收。

交付状态为 `SoftwareComplete`：通用软件完整实现并验收。设备缺席时，真实停止、交接、
硬实时、硬件身份和物理连续性保持 `HardwareEvidencePending`。测试替身、合成事实和
软件进程不能冒充设备或物理证据。新设备由配置、正式注册和声明的 Local EAIOS 合同接入；
Core 不按设备名、Node ID、固定节点数或固定任务图选择行为。

Orchestration 管理 Mission 与计划版本，Control 独占资源承诺和绑定，Runtime 管理 attempt
和命令，State 保留来源，Local EAIOS 保留即时操作和最终安全权威。副作用前持久化；Unknown
保留所有权；重启不续预算；原始 receive time 不刷新。模型只提候选，自动重规划、抢占和
交接须有策略预授权并通过 Review、Control 和安全门禁。

## 阶段与当前状态

| 阶段 | 对应项 | 必需交付 | 当前状态 |
| --- | --- | --- | --- |
| F0 基线与合同 | R03、R19 的非发布部分 | 备份、独立分支、软件 profile、权威/支持/验收矩阵 | 进行中 |
| F1 停止与当前 HA | R01、R02 | 类型化 stop、Mission hold、证据/receipt/deadline、全生命周期 gate、schema 接线 | 当前切片软件验收通过；后续 stop reconciliation/recovery 升级仍由 F7 验收 |
| F2 身份与入口 | R04、R05、R14 | pending reconciliation、tenant、证书轮换/撤销、Node 生命周期、硬件证明验证 | 当前身份/入口切片软件验收通过；State/Memory 跨服务授权与副本证书在 F3/F4/F5 接续完成 |
| F3 State 与 MI | R06、R07、R08 | policy registry、authority consumer、Belief、provenance、verifier/持续验证 | 当前软件阶段验收通过；设备证据独立待验证，后续阶段仍必需 |
| F4 Memory/Spatial | R10、R11、R12、R20 | reconciliation、Group 授权/handoff、active-map/GC/revocation、自动配准/融合/增量 | 实施中，见 ADR-0105；不得提前报告完成 |
| F5 动态 HA/恢复 | R09、R13、R15 | join/remove、leader transfer、升级协议、archive/catalog/CAS 恢复 | 动态 learner/joint membership、leader transfer、同一成员 leaf/部署 CA 迁移已通过；旧数据/二进制升级及在线 archive/CAS 仍待完成 |
| F6 资源/调度 | R16、R20 | divisible quota、完整时间/空间承诺、真实成本 evidence 与确定性策略 | cost evidence、逻辑 quota ledger 与 Prometheus 入口已通过；物理/OS 强制、完整时空和运行中抢占仍待完成 |
| F7 执行协调 | R17、R20 | 运行中抢占、耦合恢复、异构交接/regrasp、hazard/stop certificate 接入 | 当前 stop/recovery/hold/rebind gate 回归通过；运行中抢占、耦合迁移和 hazard/交接协议软件仍须完成，真实物理证据独立待验 |
| F8 运行期计划 | R20 | 版本化候选/替代计划、预授权、持久预算、去重及安全切换 | 原目标/版本、显式 MI generation/claim/phase nonce、实际主体/tenant 自动发现、SQL 原回包/只读对账与 stop/revoke 后新 Group 已接线；普通 resume/recovery 不可绕过，scoped Memory 迁移仍实施中 |
| F9 层级/Federation | R20 | 控制域、跨域 prepare/commit、路由/授权与恢复 | 持久 epoch/digest/deadline prepare/commit session 已通过；跨域 transport/授权/恢复部署仍待完成 |
| F10 运维与总验收 | R18、R19 的非发布部分 | 指标/告警、write-disabled 只读恢复、部署/迁移/回退、全功能验收 | 待完成 |

每阶段通过对应验收后才进入依赖阶段。类型、文档、占位返回值、内部直调和通用测试通过
不能独立关闭功能。阶段记录必须包含当前源码/配置 identity、复现命令、调用链、结果和剩余项。

当前阶段记录：[F1 停止权威与当前 HA 软件验收](acceptance/2026-10-15-f1.md)。
后续记录：[F2 身份与入口软件验收](acceptance/2026-10-15-f2.md)、
[身份软件合同](identity-lifecycle-v0.1.md)、[F3 State/MI 验收](acceptance/2026-10-15-f3.md)、
[F4 当前 Memory 连续性切片](acceptance/2026-10-15-f4-memory-continuity.md)、
[F4 内容门禁切片](acceptance/2026-10-15-f4-content-authority.md)、
[F4 实际空间文件切片](acceptance/2026-10-15-f4-spatial-files.md)、
[F4 Operator 事务门禁](acceptance/2026-10-15-f4-operator-transactions.md)。F4 其余工作继续实施。

文件计算后续：[Occupancy 与原始 monotonic 预算](acceptance/2026-10-15-f4-planar-and-clock-budget.md)。
点云/平面文件计算已有实际验收；[正式 Node/CAS 文件链路](acceptance/2026-10-15-f4-spatial-node.md)
已通过显式 loopback 进程配置，含独立验证与 Controller/Node 无重复 worker 重启。
[producer mTLS/dispatch 权威](acceptance/2026-10-15-f4-map-provider-authority.md)已接入实际文件链路，
含原始声明冻结、当前发布 attempt 和旧二进制拒绝。
[完整 map 内容策略](acceptance/2026-10-15-f4-full-map-policy.md) 已验证显式升级、完整来源 digest、
拒绝剥离投影、撤除/修改/downgrade 和旧 v33 binary；Group grant/撤销、pre-Execute、handoff、
active-map/GC 和其余阶段继续实施；不从文件测试推断设备能力。

[执行前 Memory 准备切片](acceptance/2026-10-15-f4-memory-preparation.md)已通过实际两软件 Node 的
Prepare ACK→原 import/SQL 中断只读对账→当前 bytes Probe→Execute→独立验证与重启检查。
原 boot/monotonic 预算、不可逆撤销 ACK、整组 pending barrier、wrapper v35 / inner v22 /
journal v9 和旧版本拒绝已接线。完整 Group lifetime/handoff、typed-map 消费、active-map/GC
仍继续实施；F4 不提前关闭，F5–F10 继续为必需项。

[typed-map 字节副本切片](acceptance/2026-10-15-f4-typed-memory-replicas.md)现通过实际 native
地图生成→两软件 Node/CAS/prepared import→独立验证，以及消费后的 Controller/Node/backend
重启。wrapper v36 / codec v25 保留完整原 map，provider-qualified 字节副本不产生第二 manifest
权威。地图语义消费/激活、完整 Group lifetime/handoff、GC 和后续阶段仍须完成。

[当前使用许可切片](acceptance/2026-10-16-f4-memory-use-leases.md)已接通原 context 的当前 Read
授权、真实后台计算、续约、撤销确认、原期限到期及进程崩溃恢复，保留原预算与 Unknown。
Node Contract v0.10/config v0.11/preparation v0.2、journal v10/wrapper v37 拒绝旧 authority。
实际大文件准备窗口失败仍保留，继续优化有界逐块门禁和写锁内 I/O；不得放宽期限或删掉要求。
active-map、完整 Group handoff/GC、动态 HA 与 F5–F10 仍为必需项，F4 不提前关闭。

[Active Map、交接与保留切片](acceptance/2026-10-16-f4-active-map.md)已在实际两软件 Node、真实
CAS/SQLite、mTLS 和独立 verifier 链路通过。它验证了 active-map 选择、tile 查询、重启失活、
软件 stop barrier handoff 与八类引用 GC 门禁；不提供物理定位或设备停止证明。大文件准备窗口、
完整 Group 授权/撤销生命周期和 F5–F10 仍未关闭。

[F6 成本/quota 与 F10 Prometheus 切片](acceptance/2026-10-16-f6-cost-quota-f10-metrics.md)已补齐
来源/TTL 绑定的成本排序证据、持久逻辑 quota ledger 以及 Mission/Artifact 的 Prometheus
scrape 入口；这些不等于物理/OS quota、真实 GPU/交通成本或告警 SLO 达标。

[F9 Federation session](acceptance/2026-10-16-f9-federation-session.md)补齐了持久跨域
prepare/commit 的 epoch、plan/ownership digest、deadline 和 dispatch-ready gate；真实跨域
transport、证书路由和多域恢复仍未关闭。

[F7 停止与恢复软件验收](acceptance/2026-10-16-f7-stop-recovery-software.md)记录了现有生产
路径的 StopConfirmed、Mission hold、Unknown/recovery 和 whole-Group gate；不把软件测试或
`SoftwareProcess` assurance 当作设备停止证明。

[2026-10-19 多 Node Memory 生命周期](acceptance/2026-10-19-f4-memory-group-lifecycle.md)
进一步修正 Task 与整组 release 的作用域：本地 completion 可单独入库；正常 Task 等待自身
原 context/provider ACK，活跃兄弟 Task 保留许可；取消、最终失败、负向 verifier 和恢复
核对对应完整参与方。回执固定原 backend instance/contract；每次 Execute 发送重查整组
最早原期限。共享 wrapper v38 fences 旧 writer。两软件 Node/实际文件/CAS/独立复算/重启
与标准 SQL 故障对账均已验收；部分 Unknown、跨 Group handoff 和后续阶段继续为必需项。

[原撤销部分失败对账验收](acceptance/2026-10-19-f4-original-revoke-reconciliation.md)
已完成逐 provider 进度、原预算内恢复、独立有界 GET 会话、原 Unknown/预算不变、
迟到/替换 backend 拒绝与旧二进制门禁。三种实际软件故障配置和正常并行 Task 链路分别
验收；内容对账不绕过独立 stop hold。后续 Group handoff 工作包见下文，
跨主机、HA 内容恢复及 F5–F10 仍必需；完整 F4 不提前关闭。

[双 Group 交接与成员生命周期验收](acceptance/2026-10-19-f4-group-memory-handoff.md)
已通过停止/撤销 → immutable child lineage → 原接收方 import/probe/use → 显式 commit →
目标 Execute/独立验证的实际软件链路；后续 Task 在原接收方退役后取得新成员代际与新许可。
两个 SQL 回滚窗口、超时、策略变化、待交接重启、旧 writer/零事件 fence 均有实际证据。
wrapper v40/codec v27 保护该权威。软件 worker 停止不升级为设备事实；完整 F4、跨主机、
HA 数据面恢复及 F5–F10 仍必需，后续实施应核对 active-map/GC 与内容恢复的完整生命周期。

## 已接受的实现决定

- Stop 冻结完整受影响 attempt 集合、operation-owned export、策略、owner/session 和 deadline。
  receipt 仅证明接纳；实际停止证明和新鲜 safe-state 独立核对。旧观察、Unknown、自然
  Completed/Failed 不能合成确认；确认后不回退；全 release/resume 路径共享门禁。
- principal、tenant、domain、Node、LocalSystem、PhysicalEntity 分离。授权 revision 持久化，
  撤销隔离已有连接；证书轮换具有准备、受限重叠、激活、撤销阶段。硬件证明校验可信密钥、
  challenge、签名与测量策略；签名有效不自动证明物理身份。
- State 不成为 Global Truth。原始 records 保留；布尔/枚举显式归约；有协方差的数值证据
  提供保守融合，不把 confidence 当方差。持续 verifier 影响当前执行，终结后的争议为 incident。
- Spatial provider 与 Rust Control 分离；独立 Python 3.11/Open3D 0.19.0 环境。PLY/PCD 和版本化
  占用数据支持 FPFH/RANSAC、ICP、pose-graph、三维分块融合与增量 revision；阈值/预算由策略
  配置，未观测空间保持 unknown。融合产物不证明当前设备已经定位。
- Memory immutable scope 不原地改写。跨 Group 交换有显式授权和 lineage；GC 先 tombstone，
  核对执行/import/active-map/snapshot/archive/副本引用后删除。Unknown 只凭原 provider 查询
  合同 reconciliation，不换 import identity 重做。
- OpenRaft 0.9.25 保留。动态成员使用 learner catch-up/joint consensus；handoff 使用 durable
  barrier、旧 Leader drain、目标同步、租约到期后的定向选举和新 epoch/quorum 确认；尊重
  多数派，处理线性读续租，超时恢复正常选举，不假设已有原生 transfer API。
- 新资源合同区分 exclusive/divisible 与精确单位。旧 units 保留原语义；确定性有界 Scheduler
  消费真实 evidence；缺 GPU/定位/交通数据为 unavailable。实际限额由声明支持的 Local EAIOS
  实施，Controller 的逻辑分配不能伪装成物理强制保障。
- 交接使用 prepare、完整 stop barrier、上下文导出/导入验证、Commit/Rebind、继续执行。
  重规划替换未完成部分，保留既成事实、原目标和 grounded identity，预算不可因重启续期。
- 根 Orchestration 管理全局 Mission/协调，子域 Control 管理本域资源。跨域持久 prepare/commit
  全部完成后才 dispatch；不确定事务保留所有权。epoch 必须限定 cluster/domain。
- 存储或复制不可写时进入 write-disabled/degraded，查询最后提交状态并提供恢复入口。
  指标统一 Prometheus；采集/告警/runbook 必须实际验收，候选 SLO 不写成已达标。

## 合同与兼容

沿现有服务扩展可查询的管理操作：幂等 reconciliation、Memory/stop/recovery/handoff、计划
revision、成员和 leader transfer。mutation 必须有幂等身份、期望 revision 和证书主体授权。
verifier ingress 绑定 source、tenant、Mission/Task/Role/attempt、计划 revision、finality/freshness。

Node Protocol 使用显式版本/协商扩展提供 stop receipt/certificate、查询恢复、上下文和 domain
identity；bytes 仍走 Artifact。MissionPlan 新版本明确 quota/运行期策略/计划 revision；旧输入
兼容读取但不能绕过新授权。共享 schema/version 检查防止 Controller/supervisor 分离。
迁移保留预算/receive time/owner/attempt/history，缺证据进入 unavailable/hold；不安全降级拒绝。

## 强制验收

1. ID/拓扑/注册顺序/能力/provider 配置变化不修改 Core；扩展协议被真实业务消费。
2. 正向软件进程通过公开 API/正式协议产生真实计算、文件、CAS 和恢复结果，由独立 verifier 检查。
3. stop 覆盖旧 epoch/session、过期/冲突、无消息超时、取消/完成/失败/resume/release 竞态与多 Mission 隔离。
4. 在持久化、发送、副作用、receipt、quorum、本地 Commit、snapshot、GC 和计划切换窗口注入故障。
5. 独立 ownership/quota/生命周期模型验证无双重分配、超额、未授权迁移和危险重放。
6. 空间算法使用可追溯文件和独立真值/误差检查，覆盖退化、低重叠、高度/单位、回环和增量损坏；
   样本不伪装成当前设备观察。
7. 隔离环境真实验证采集/告警、撤销、动态成员、handoff、archive、升级和拒绝不安全回退。
8. Rust tests/fmt/Clippy/Rustdoc，Python format/lint/strict typing/函数文档/tests，跨语言 schema、
   迁移和文档检查全部通过。软件 TEST GAP 未关闭时不得报告 SoftwareComplete。

最终交付：逐项状态矩阵、完整合同与配置、扩展指南、部署恢复 runbook、复现命令和
代码/配置/依赖 identity、独立验证结果、分阶段提交、同步架构/ADR/README/AGENTS/实现图。
设备证据待验证清单独立保留；不通过删除要求或把 Unknown 改成成功关闭软件工作包。
