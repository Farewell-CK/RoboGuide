# RoboGuide 全部非发布整改实施与验收

状态：Accepted，实施中。用户于 2026-10-15 要求执行完整计划。
基线：`main@5285a92a6af8bf4a99bb0852b5cdb80728c9144c` 加已备份的未提交整改工作树。
实施分支：`codex/remediation-complete-software-2026-10-15`。

2026-10-20 后续连续实施使用 `codex/software-completion-2026-10-20` 隔离工作树，
各验收工作包完成后合入并同步 main。后续记录按实际合并提交与证据 identity 保留。

2026-10-22 [全分支与整改存量复核](roboguide-branch-audit-2026-10-22.md)确认冻结
`main@fc5d2ba` 已含所有 18 个本地分支的已提交历史，并逐条核对远端独有补丁、工作树、
stash 与 reflog。下表阶段仍开放不表示整项尚未实现：当前能力、缺失接线/软件组合验收
与设备/跨主机证据分别记录，不重复开发已经进入 main 的算法或服务入口。

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

[部署 profile 软件门禁](acceptance/2026-10-21-deployment-profile.md)提供配置驱动的拓扑、
共址/故障域、原文件/store owner、TLS/协议与故障/退出矩阵。实际零 Node 检查通过；CI
补齐维护模块、同一实际 native gate 和独立 Python 3.9 runtime compatibility。它不代替
其余阶段实测、硬件或生产批准，也不把原证据文件的完整性当作全阶段完成。

[Memory writer 与完整恢复](acceptance/2026-10-21-memory-provider-writer-recovery.md)补齐实际
四 SQL inode 和主 Store 关闭后的共享 writer 生命周期，旧连接排空后保留原数据升级。
显式 recovery v0.4 冻结相同原租约并完整恢复；全局引用和在线 HA 仍须各自验收。

[原属组 Memory 计划修订](acceptance/2026-10-20-scoped-memory-plan-revision.md)接通显式
v0.3 许可、原 child/Review digest、完整停止/撤权和 Control 新组的正常 import/use。
实际正常/SQL/已确认停止后重启/策略/到期五条链路通过，旧 writer 拒绝且原 MI/迁移回归
通过；wrapper v45 fences 新权限。后续[非空 MI 整链](acceptance/2026-10-21-runtime-mi-scoped-memory.md)
已补实际原 Request、显式 v0.9 DTO、自动故障/原停止撤权、MI 中断和响应丢失、独立 Review
与正常新组/原目标组合。全局 Memory、其他类型和跨阶段矩阵仍须完成。

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
| F0 基线与合同 | R03、R19 的非发布部分 | 备份、独立分支、软件 profile、权威/支持/验收矩阵 | 闭合可执行 profile/原文件/TLS/退出矩阵与实际零 Node 门禁通过；后续阶段实测与真实部署输入继续分别保留 |
| F1 停止与当前 HA | R01、R02 | 类型化 stop、Mission hold、证据/receipt/deadline、全生命周期 gate、schema 接线 | 当前切片软件验收通过；后续 stop reconciliation/recovery 升级仍由 F7 验收 |
| F2 身份与入口 | R04、R05、R14 | pending reconciliation、tenant、证书轮换/撤销、Node 生命周期、硬件证明验证 | 当前身份/入口切片软件验收通过，副本换证另有软件证据；State/Memory 完整跨服务授权组合随 F3/F4/F5 接续验收，硬件身份来源证据独立保留 |
| F3 State 与 MI | R06、R07、R08 | policy registry、authority consumer、Belief、provenance、verifier/持续验证 | 当前软件阶段验收通过；设备证据独立待验证，后续阶段仍必需 |
| F4 Memory/Spatial | R10、R11、R12、R20 | reconciliation、Group 授权/handoff、active-map/GC/revocation、自动配准/融合/增量 | import/Unknown 查询、Group use/revoke/handoff、Active Map、本地 tombstone/GC 已接线；实际文件配准/融合/增量算法也已有。ADR-0142–0145 接通显式引用、紧凑 use-lease、FileJobs execution 引用及静止恢复的原 CAS 对账。实际 Replica/Archive/Snapshot/Catalog 引用生产与释放、跨服务对账和 typed-map 完整生命周期/融合结果消费/恢复组合仍待完成；算法和局部证据不重复列为零实现 |
| F5 动态 HA/恢复 | R09、R13、R15 | join/remove、leader transfer、升级协议、archive/catalog/CAS 恢复 | 动态 learner/joint-consensus 成员、transfer、证书轮换/Abort 和静止 SQL/CAS 恢复已有。Replica storage v3 / recovery v0.7 保留 inode writer、旧连接排空、原 transfer 与 Controller spawn/PID/wait；V3 election 须原 durable wait/quorum/预算，排队不续期，Unknown 原 ID 不重发。缺原进程日志的旧部署显式排空迁移、在线 archive/snapshot/catalog/CAS、滚动版本升级/回退组合和跨故障域仍待完成 |
| F6 资源/调度 | R16、R20 | tenant/category quota、divisible allocation、完整时间/空间承诺、真实成本 evidence 与确定性策略 | ADR-0140 / wrapper v47 已由 recovery-admin revisioned policy API 把原 Role demand 接到正常、Group、recovery Commit 与精确 release；ADR-0141 让 v0.2 运维 view 暴露未设限的已有用量。真实双向 TLS/SQLite/重启进程 gate 通过且 0 Node。local worker POSIX CPU/address-space proof 独立通过。divisible allocation、GPU/交通成本、完整时空、Local EAIOS 其他资源强制和运行中抢占仍待完成 |
| F7 执行协调 | R17、R20 | 运行中抢占、耦合恢复、异构交接/regrasp、hazard/stop certificate 接入 | relation violation/Unknown 已接入 whole-Group stop barrier，并保留每个 current attempt 的独立 stop gate；运行中抢占、耦合迁移、异构 regrasp/hazard 协议和真实物理证据仍待完成 |
| F8 运行期计划 | R20 | 版本化候选/替代计划、预授权、持久预算、去重及安全切换 | 原目标/版本、MI generation/claim/nonce、主体/tenant 发现、原回包对账与非空 scoped Memory 通用文件组合已验收；其他类型/复杂拓扑仍须随 F4/F7 总矩阵完成 |
| F9 层级/Federation | R20 | 控制域、跨域 prepare/commit、路由/授权与恢复 | 持久 epoch/digest/deadline prepare/commit session 已通过；跨域 transport/授权/恢复部署仍待完成 |
| F10 运维与总验收 | R18、R19 的非发布部分 | 指标/告警、write-disabled 只读恢复、部署/迁移/回退、全功能验收 | write-disabled、最后提交查询、显式恢复探针、Prometheus/Alertmanager 实际短窗口 firing/resolved、部分 worker/连接隔离已有。其余 listener/阻塞 I/O、完整 SLO/runbook/告警覆盖、升级/回退组合和非发布总矩阵仍待完成；长期/跨主机/人工响应证据独立验收 |

每阶段通过对应验收后才进入依赖阶段。类型、文档、占位返回值、内部直调和通用测试通过
不能独立关闭功能。阶段记录必须包含当前源码/配置 identity、复现命令、调用链、结果和剩余项。

## 当前执行队列（2026-10-25）

用户已接受后续高效收尾计划，冻结基线 `1dc5b6d`，仍只使用
`codex/nonrelease-remediation-closure`。本轮优先单机真实多进程软件验收；跨主机、
设备和长期 SLO 证据独立列待验证。软件实现和原非发布完成条件均不缩减。

| 顺序 | 对应原项 | 当前必需交付 | 状态 |
| --- | --- | --- | --- |
| A1 | N1/N3 | 历史 Log/Snapshot 原授权内容追赶及独立缺失状态 | Log、purged Snapshot、空 learner、准备/投递心跳及 ACK 门禁通过；ADR-0195 原归档源首次初始化、Unknown、Log/Snapshot 追赶、完整实际入口及最新完整存量三 leader 冷恢复通过；ADR-0198 已补 committed replay 缺失输入的启动拒绝、显式原 owner 修复、重入群与冷恢复；全局 Archive/原完整故障矩阵随 A2/A3 继续 |
| A2 | N1/N3 | 全 producer 引用产生/退休/GC、learner/joint/readiness | readiness、Root7 GC/恢复、实际 mTLS learner/joint、损坏与旧 epoch 拒绝、Root8 私有 staging/原 Unknown/完整恢复已验收；全 producer 退休与最终 CAS 回收组合继续 |
| A3 | N3/N7 | 旧部署实际排空、滚动升级、不安全回退拒绝 | 原 Root4 → 6 → 7 排空同 inode 升级、旧入口拒绝与三 leader 冷恢复通过；滚动/回退继续 |
| B | N2/F8 | typed-map 发布/import/use/撤权/交接/MI/恢复/回收整链 | ADR-0199 已接通 typed-map handoff 的完整原始 manifest、schema v0.2、Control policy/retry/restore fence；发布/import/use/撤权/MI/恢复/回收的跨阶段组合仍待验收，不重写空间算法 |
| C | N4 | divisible allocation、真实成本源、完整时空及实际支持的本地强制 | 待实现与验收；保留现有 quota/units |
| D | N5/F8 | 运行中抢占、耦合恢复、hazard/handoff/regrasp 与原目标 | 待实现与验收；复用 stop/revoke/revision |
| E | N6 | 双独立 Control 服务的 mTLS、持久决定及原事务恢复 | ADR-0201/0202 已接入 participant HTTP、持久版本化 tenant/domain grant、原回执、双 SQLite 重启/故障对账；持久 coordinator、真实 peer 请求、资源/epoch 证明和跨主机时钟/恢复组合仍未实现，global dispatch 保持 false |
| F | N7 | 运维随包接入，最终固定 profile 全矩阵及 main 集成 | 待全部软件必需项通过 |

当前清单为唯一执行队列，下面按原时间保留的切片记录不能替代上表完成条件。

ADR-0197 的实际组合进一步复现：已提交 Log 依赖的原 stream 回执 Unknown 时，副本在
Raft 启动回放等待内容，而网络尚未开放。ADR-0198 现已在真实存量上闭合该入口：完整备份
仍正确拒绝 active 缺回执状态，启动快速拒绝并由本地 exact owner repair 补齐 placement；
原始 Log/Unknown 不改写，三成员重入群、重启和固定 bundle 冷恢复通过。原始双
writer/inode 文件仍另存为 forensic-only，不能当成有效恢复包。全局 Archive/原完整故障
矩阵、A3 与 B–F 继续必需。

[Committed replay input repair 验收](acceptance/2026-10-25-committed-replay-repair.md)已通过：
原生 gate 保留 1 个真实缺失 input、所有拒绝路径、1 个 repair copy、三成员重入群/重启；
独立冷 gate 复核固定 post-repair bundle、原 stream Unknown 行和 repair operation。软件
进程证据不建立设备、物理停止或全局 HA 结论。

[原 producer 退休与最终 CAS 验收](acceptance/2026-10-25-complete-producer-retirement.md)
现已通过：158 项 Replica 回归、每成员两个不再使用的实际 CAS 对象删除、原 transfer
对账、重启及同一固定 bundle 的三个实际恢复后 leader。原 GC/tombstone/staging/producer
和全部当前必需 bytes 已独立核对。该结果闭合成员本地组合，原不确定启动、全局
Archive/跨服务引用、A3 及 B–F 继续必需；下一包先修复实际已复现的 committed-input 启动入口。

A2 随包运维先按 ADR-0196 收敛真实长历史中 reader 小事务导致的重复全历史校验，保留
原权限/预算/行与 Unknown，再接完整 producer 退休/最终 CAS 回收组合。该内部复用不
另造存储版本，不缩减 A2/A3 或 B–F；约 141 秒的原性能记录继续保留。

[精确 reader 校验验收](acceptance/2026-10-25-managed-reader-validation.md)已通过 155 项
Replica 回归、实际长历史接管/SQL 故障/原 Unknown/release 以及三个恢复后 leader。
本次首次接管 96.10 秒、SQL 故障路径 115.01 秒；两次独立软件运行只提供短观察，不能
替代长期 SLO。下一项为完整 producer 退休与最终 CAS 回收组合。

[原归档源初始化验收](acceptance/2026-10-24-original-archived-source-initialization.md)
保留原 floor 82/checkpoint 102/77 个命令，显式 parent9/recovery v0.23 接纳空应用；
普通增量不放宽连续性，原 source/anchor/Unknown/首次预算保持。真实身份/TLS 输入、
实际原进程退出、缺失 Log/purged Snapshot 与三个恢复后 leader 已有证据。
同 bundle 反复丢失第三目标 epoch 的反例由原 WAL 共识只读/重复持久值和受限工作任务
收敛；实际原 SQL held-writer 反例和全部 152 项 Replica 回归通过。
当前长历史接管仍约 141 秒，性能与完整 producer/最终 CAS 回收、滚动升级以及 B–F
继续必需，不能据此宣布 SoftwareComplete。

[原 staging 回收与 Root8 恢复](acceptance/2026-10-24-original-staging-reclamation.md)补齐真实
旧 copy 硬链接和非空中断文件的有界回收、删除前 SIGKILL/删除后 SQL 结果失败、旧 ID 只读
与新命令独立缺失。实际包检查发现并收紧新恢复格式的私有文件范围；同一批原 Root/SQL
保留 Unknown/预算，经完整恢复后三个 leader 读取通过。该切片不替代全 producer/最终
CAS 回收、历史归档源、滚动升级/回退或其余 B–F 要求。

[完整内容 voter 准入与 Snapshot 活性](acceptance/2026-10-24-content-voter-admission.md)
保留错误返回后 joint 扩大、准备期选举、真实 Root7 metadata ACK 超时及旧 epoch
管理请求先产生效果的原反例。当前实际 mTLS 管理入口核对字节后才切换；原 epoch、
实际 writer/TLS、未知结果和预算随独立任务保留。内核源 read 阻塞超过选举超时期间，
35 个真实持久 vote 采样不变；后续换主、同 inode 重开和实际 Controller 组合门禁通过。

[Root7 GC 与有界追赶验收](acceptance/2026-10-24-replica-content-gc.md)覆盖真实删除前
SIGKILL、删除后 SQL 回执失败、原 Unknown/同 ID 只读、61 条原 stream 退休与 3 个原 Root
同 inode 升级。首个冷运行失败暴露 Append 重叠追赶耗尽 work capacity；ADR-0191 将其
收敛为独立持有原 admission 的唯一任务。同一 pinned bundle 的 337 → 307 → 311
恢复/读取/原 Unknown 和全部实际 supervisor wait 均通过；原 GC/退休行另做只读独立比较。
每项状态必须附实际源码/配置、公开调用链、通过证据和原提交。A1 的缺口是普通
committed route 要求双方已接受同一应用，无法给 apply 前的落后 Log 供给缺失内容；
独立历史路由必须核对双方实际原 Log、来源提交位置和现存引用，不能放宽原路由。

[ADR-0187 Log 专项验收](acceptance/2026-10-24-retained-log-content-catchup.md)已通过实际
离线成员重启/原 Log/原字节、换主、SIGKILL/wait/同 inode 重开和独立 SQL/pin/CAS。
两版本源场景还修复了 apply 的共识前缀等待循环。已归档源首次捕获缺少原来源绑定的
失败保留在 A2；网络 Snapshot、成员差异和原完整矩阵继续必需。

后续每个提交固定一个可独立验收的缺口，先复用已有实现并检查实际调用链，再做相关验证
和提交。共享合同变化才扩大回归；普通内部复用不另造合同版本。已通过且未受影响的
大型进程矩阵保留原证据，最终总体门禁仍按原完整条件执行。

N3/P3 的 [ADR-0184–0186 完整静止恢复与重启](acceptance/2026-10-24-replica-content-cold-recovery.md)
已通过：显式 v0.19、原 parent8/Root4、Root5 新 backing、二次完整恢复、实际三 leader
追赶/transfer/读字节及原 Unknown 保留。并发活 producer 对账与字节晚到 apply 的两条
实际故障已修复，原权限和预算未续期。接下来接完整 online Archive/Snapshot/GC、
历史缺失内容的路由、joint/fault/升级回退组合，再按原 N1–N7 队列完成 typed-map、资源、
执行协调和双域 transport；本项不关闭总体 SoftwareComplete。

N3/P3 的 [ADR-0183 原 recovery 事实对账](acceptance/2026-10-24-original-recovery-assessment.md)
已通过写禁用、原 SQL 故障、真实重启/source 放弃后的同一 Unknown，以及当前
replacement Completed/退休/缺失、权限与旧路径检查。该切片闭合本地已提交
records 的只读组合，不证明原 Root reader 的操作绑定或补造原完成。接续完整
Root reader 来源/恢复、parent8/Root4 bundle/升级与 online readiness/GC。

N3/P3 的 [ADR-0182 跨 owner Completed 退休](acceptance/2026-10-24-cross-owner-completed-retirement.md)
已通过实际 fresh quorum/source/Root bytes、原 receipt、重复请求和独立 SQL/文件
检查。原上传引用的 Prepared 放弃/Completed 退休已分别闭合；独立 Log/View/
Snapshot/reader 仍保留责任。接续原 Unknown 只读对账、parent8/Root4 完整恢复与
升级、online readiness/GC；不能把上传引用释放计为全部 Root/HA 完成。

共享 wrapper v53 的 [standalone 恢复接线](acceptance/2026-10-24-recovery-profile-propagation.md)
补齐原 GC/Snapshot/writer inventory 的实际 profile 传递，保留旧 profile 和新
recovery/Root4 拒绝规则。真实 10 类 GC SQL 故障、Catalog 接管/source GC、
真实 MI child 与 Archive 冷恢复通过。Completed 退休由上述 ADR-0182 接续；完整
Replica 恢复仍必需，不能以 standalone 验收关闭在线 HA。

N3/P3 的 [ADR-0181 跨 owner Prepared 释放](acceptance/2026-10-24-cross-owner-prepared-release.md)
已通过：在原完成 SQL 故障、真实 child wait/restart 后，新 writer 的 DELETE 保留
原 owner origin，仅解除原上传输入；原 Unknown、独立 Root stream/log 和三份实际
bytes 保留。相关全工作区编译和一次扩展进程链通过。Completed 退休由上述 ADR-0182
接续；原 Unknown 只读对账和 parent8/Root4 完整恢复及其余必需矩阵保持。

N3/P3 与 N7/P7 的 [ADR-0177–0180 实施验收](acceptance/2026-10-24-prepared-recovery-and-runtime-latency.md)
已通过：原 Prepared 保留、新 owner 独立正常上传、原 Root reader SQL close、一次性
原预算、完成 SQL 故障后的 Unknown/只读重试，以及 fresh complete-quorum checkpoint
保护的故障观察。Root 同连接结构 witness 与审计单行 witness 降低重复工作，不能
缓存动态授权或跳过真实审计。最终实际三个 supervisor 均 wait 0，创建零 Node/设备。
完整恢复仍约 77 秒；短窗口观察不计长期 SLO。wrapper v53/codec v32、显式 v0.18
standalone 冷恢复已有检查；v0.18 仍拒绝新恢复事件和 parent8/Root4。

跨 owner Prepared 释放由上述 ADR-0181 接续；随后完成 Completed 退休、Unknown 只读对账、
parent8/Root4 恢复/升级、完整 readiness/在线 HA，再依原 P1–P7 推进其它功能。
采用单一缺口→相关验证→提交的节奏，避免重复运行不受改动影响的全量进程矩阵。
原全部 N1–N7/P1–P7/F0–F10/R01–R20 和最终总体检查仍必需。

N3 的 [ADR-0176](../decisions/0176-owned-controller-qualified-content-reads.md)接通原
owned child 的 qualified Root read、公开内容/启动完整性用途及 HTTP fallback。实际
三个 supervisor/真实 Controller 切换已返回原 owner 的完整 HTTP bytes，且新 owner
本地 CAS 保持空缺。原 reader/回执核对和相关回归已通过；Prepared 显式恢复由
上述 ADR-0177 接续。完整 readiness/在线 HA 和其余 N1–N7/P1–P7 仍必需；本项
只证明软件内容读取连续性。

N3 以 [ADR-0175](../decisions/0175-exact-committed-content-routing.md)将 Root 路由
收紧为独立 committed command/API：双方实际完整 SQL target、source member/Root/
TLS、epoch/预算逐块检查，原 leader proposal staging 保持。维护 native gate 新增
实际 follower→leader、隐藏 source 后原 ID 只读重试及未提交 target 拒绝。后续继续
原 Controller 的受权内容读取、Prepared takeover 和完整 readiness，不提前标完整 HA。

N7 按 [ADR-0173](../decisions/0173-unchanged-timer-transaction-elision.md)收敛实际空闲
timer 重复提交的 I/O：完整 checkpoint、实际当前/上次完整提交 SQL 计数、外部连接
data_version、事件及 pending 写入均不变才回滚。原 side effects/outbox 与显式
零事件命令保持原 journal/Commit。旧冻结二进制的真实零 Node 反例、新版本的实际
空闲/上传/同 inode 重启、文件/CPU stop/revoke/handoff 回归通过，见
[验收](acceptance/2026-10-23-unchanged-timer-transactions.md)。这是 N3 接管前的性能问题修复，
不替代其余 P1–P7；接着继续跨 owner/Prepared takeover 和完整 readiness。

N3 的 [ADR-0174](../decisions/0174-committed-member-content-routing.md)已接通 Replica
Root 跨成员读取：current/joint voter 中任一实际 source member 可在 target/epoch/
qualified owner/TLS/budget 约束下路由完整 bytes，原 Artifact `source_owner` 不被
替换。实际测试隐藏 leader Root 后由另一个 voter 路由恢复，metadata-only majority
仍拒绝。下一项继续 Prepared upload takeover/Artifact owner finalization、完整
readiness 和 online HA；本项不关闭其它 N1–N7/P1–P7。

N3 的 [ADR-0172](../decisions/0172-runnable-owned-controller-content-profile.md)已接显式
runnable process v0.2：实际 parent8/Root4、原 PID journal、mTLS 与真实 Controller
共享源 writer。原 quorum 观察预算进入 policy，ProcessFence 不代替完整内容 ACK；
实际 HTTP 上传/下载、三副本完整 bytes 与原 child SIGKILL/wait/同 inode 重启见
[本包验收](acceptance/2026-10-23-runnable-controller-content.md)。接下来继续跨 owner
读取与原 Prepared 接管、完整 readiness，再做 joint/catchup/Snapshot/升级/回退/
recovery/GC/业务目标矩阵及全部 N1–N7、P1–P7、F0–F10、R01–R20，尚不标 SoftwareComplete。

N3 的 [ADR-0171](../decisions/0171-owned-controller-content-ingress.md)正在接原 owned
child ingress：显式 parent8 在每个原命令（含空输入）前保存完整候选/process/TLS/
target/预算，真实 Unix peer PID 匹配原 spawn/PID/boot/start ticks。独立成员有界
并行传输与完整观察，原 ID 只对账，完整原候选的只读确认不改写第一次 Unknown。
实际软件文件源已走通 State/Prepared/Completed、SQL receipt failure、实际原 wait
和重启；同时修复 network TLS 等待 application mutex 和 SIGKILL 残留 IPC 的问题，
见[验收记录](acceptance/2026-10-23-owned-controller-content-ingress.md)。完整 runnable
Controller 接线、内容路由/readiness、全 joint/catchup/Snapshot/旧升级/回退/recovery/
GC/业务目标矩阵继续实施；本包不是完整 N3 或 SoftwareComplete。

N3 的 [ADR-0170](../decisions/0170-scoped-prepared-artifact-sources.md) 已补原 Prepared
source：完整实际 hash/fsync 后建立原非序列化 guard，原 append/finalize 与 live readers
共享冻结，撤销不冒充 FD 退出。HTTP 原 Prepared 提交前持有 source，SQL 失败保留原上传。
实际文件/HTTP/SQL 与独立进程 SIGKILL/reopen、旧 Unknown 不重做通过，见
[验收](acceptance/2026-10-23-prepared-artifact-sources.md)。这只闭合原源读取能力；下一步
连接实际 Unix peer PID/原 supervisor process journal、真实 mTLS 与原候选/预算的 child
入口，再完成内容路由/readiness 和下述完整 N1–N7、P1–P7、F0–F10、R01–R20。

N3 的 [ADR-0169](../decisions/0169-consensus-bound-content-stream-transport.md)已接 core
显式网络 v0.4/parent7、实际 source writer、mTLS Ready/有界队列/原租约与完整原候选。
metadata Commit 后实际逐成员读原 committed view 和全部 qualified bytes，同一 current/
joint voting group 各自严格多数才返回完整 ACK。真实三服务与三个独立进程、原实际
Controller/CAS、SIGKILL failover 和 inode/原候选重开通过，见[验收](acceptance/2026-10-23-network-content-transport.md)。
下一步接 runnable supervisor 原 child/Prepared source/幂等对账、内容读取路由与完整
readiness，再完成完整 joint/catchup/Snapshot/旧升级/回退/recovery/GC/业务故障矩阵。
原 P1–P7、F0–F10 与 R01–R20 继续必需，尚不标完整 N1/N3 或 SoftwareComplete。

N1/N3 的 [ADR-0168](../decisions/0168-original-bounded-replica-content-streams.md)
已接实际 stream 后端：原完整 target/source/kernel intent 与独立 pin 先于源读取，
唯一有界 worker 执行原权限/预算、chunk、hash/fsync/原子 publication 和原 SQL receipt。
实际源打开租约/SIGIO、进程 kill/wait、原期限、文件已发布但 SQL 失败、回执丢失与旧
Log/View/Snapshot/reader 完整历史升级已通过，见[验收](acceptance/2026-10-23-original-content-streams.md)。
下一项接真实 mTLS Ready/stream、Root7 与原 Controller 已发布/Prepared 源、完整候选和
同一 current/joint voters metadata+bytes ACK；再完成 routing、恢复/GC/升级及原 N1–N7。
现有后端 gate 的 target 仅是标明的 retention context，不是实际 election/quorum 证明。

N7 的 [ADR-0165](../decisions/0165-bounded-snapshot-reader-closure.md)将实际 Snapshot
reader 的 SQL 关闭移到原 root 的单一有界 blocking drainer。原 intent/预算/lease 不变，
原 receipt 未发布时 Unknown/pin 保留；有界等待与队列状态不授予内容或回收权限。
实际单 worker/TCP/SQL contention、取消/租约、独立 root 失败及同步兼容通过；原 Root3
Snapshot/producer、Archive、完整 State 接管和 cold restore 进程门禁通过。
见[验收](acceptance/2026-10-23-snapshot-reader-closure.md)。完整在线内容、相同 voters
bytes+metadata ACK、生产 profile/升级、Node/Mission 路由与原 N1–N7 继续实施。

N7 的 [ADR-0164](../decisions/0164-owned-controller-projection-installation.md)修复原
supervisor 只锁 sidecar 并 rename 原 SQL 的实际 hardlink 反例。接管使用原 inode 的
managed writer/原生排空与 SQL 事务，保留命令前缀、recovery/Unknown 和显式 v1；Raft
view 读取/安装移到原 owned blocking task。实际认证命令/持锁/回滚/旧投影拒绝/重启及
含 copy/abort 的原 Archive/CAS 链路通过，见[验收](acceptance/2026-10-23-controller-projection.md)。
Source Snapshot reader Drop、完整生产 v0.4/同一 voters 内容 ACK、旧 child/完整升级、
路由与原 N1–N7 继续必需；该局部安装不提升 metadata profile 或宣告完整 HA。

N7 的[检查器 SQL 生命周期修复](acceptance/2026-10-23-checker-sql-lifetimes.md)在实际
交接 CI 对应步骤复现原 readonly Connection 未关闭导致的重启排空拒绝；保留原故障，
23 个 quality modules 的 71 处作用域实际关闭并保留提交/回滚。禁用 GC 的独立 FD 对照、
原交接/MI 修订/原目标软件进程复测通过，没有放松产品 writer 门禁或宣布完整阶段通过。

N7 的 [ADR-0163 副本传输容量](replica-transport-bounds-v0.1.md)补齐本地 TLS 前连接准入、
原成员异步收发共享帧池、独立小帧 reserve、原 handler 生命周期与固定标签只读计数。
容量不改变原 wire/profile/权限，也不把网络取消视为已提交副作用不存在。实际单 worker/
mTLS 门禁与原旧基线独立记录；supervisor 原存储接管、其余阻塞路径、同一 voters bytes+
metadata ACK、完整 online failover 与原 N1–N7 验收继续实施。

本轮连续实施冻结 `main@45a82410867e01241394d1b9116aa53634ce2a06`，只在
`codex/nonrelease-remediation-closure` 开发。不得为工作包另建分支；阶段提交、推送和
CI 均使用该分支，全部非发布软件验收完成后再合入 main。原工作树及实验目录保留。
证据根目录为 `/data/workspace/roboguide-evidence/nonrelease-closure/`，P0 的
`baseline.json` 固定原提交、分支、工作树和范围；后续每包保留原命令、退出码、源码/
配置 identity 和 SQL/CAS 观察。历史 `/tmp` 文件若不可用须按原项重跑，不由文字记录
补造结果。Unix socket 使用独立短运行目录，避免证据目录长度影响实际协议。

本轮 P0–P7 分别对应下表 N0–N7（P0 同时承担完整 F0–F10/R01–R20 证据索引）。
所有既有必需项继续适用。P3 的内容成功 ACK 要求当前 voting 配置的实际字节和元数据
已持久化；joint configuration 须分别满足两组多数。副本元数据成功不能替代内容 ACK。
跨 owner 内容身份包含实际 store owner、provider/backend instance、digest 和 size；
同 digest 不合并不同 owner 的保留责任。引用保留权不授予内容读权。

本轮[实际 import 与 provider Snapshot 生产者验收](acceptance/2026-10-22-import-and-snapshot-references.md)
接通 ADR-0146 的原 import→Replica pin→原 revoke→本地回收，以及 ADR-0147 的显式
v4/v0.8 Snapshot 原意图→实际复制→完整验证→精确退休/对账。实际旧 writer、SIGKILL/
wait、SQL 与回包中断、冷恢复和双 Node 文件组合已验证；Catalog/lineage、Controller
Artifact/Raft Snapshot、Archive 和在线副本生命周期仍属 N1/N3 必需工作。

[Artifact 实际 owner 与显式格式验收](acceptance/2026-10-22-artifact-owner-and-retained-layout.md)
接通 ADR-0148 的原 root writer/nonce/inode intent、旧路径文件 fence、受限 owner 观察
及显式 v0.9 的真实内容恢复。实际旧 binary 与旧 CLI 拒绝新布局；本项是 Catalog/
lineage/Archive 引用的 owner 依赖，不把这些后续生产者标成已经完成。

[原 Catalog/child lineage 与输入流水验收](acceptance/2026-10-22-catalog-lineage-and-node-pipeline.md)
接通 ADR-0149 的真实 HTTP 发布及 handoff/MI child 发布、原 tenant/provider/owner 引用、
精确退休/重试/原 SQL 回滚和 recovery v0.10 原 CAS 对账。ADR-0150 接通原有界应用 batch，
保留 wire 顺序、receive time、首个 deadline 与 Commit 后 ACK，实际单 worker 的原目标/
MI/Memory 组合通过。N1 的下一必需工作为 Archive/Artifact/Raft Snapshot/在线 Replica
生产者与内容闭包、GC 及未归属旧 Catalog 显式迁移；N2–N7 继续保留全部原完成条件。

[Artifact 原 Snapshot 生产者验收](acceptance/2026-10-22-artifact-snapshot-producer.md)
以 ADR-0151/storage v2/recovery v0.11 接通实际 CAS owner、复制前引用、原 kernel 预算、
真实 CLI SIGKILL/wait、发布后回执失败、readonly retry、明确 abort 与冷恢复原 active。
旧实际 binary/CLI 拒绝新格式。此项是 N1 的另一个生产者；下一项继续 Archive/Raft
Snapshot/online Replica 与 GC，不把本项重分类成完整 N1 已完成。

[原内容 Archive 验收](acceptance/2026-10-22-original-content-archive.md)以
ADR-0152/0153 接通显式原 Archive producer：原 SQL intent/pin、实际源与接收
owner、完整 checkpoint/Catalog/bytes 发布校验和精确 compact receipt；原命令重试
不复制，接收方独立保留。wrapper v49/storage v3/recovery v0.12/bundle v0.4 记录
所有已知原 Controller reference authorities 及 mandatory 嵌套空目录。实际零 Node
SQL 故障/旧工具/缺内容门禁，以及真实双软件 Node 原文件/Catalog 归档重放与冷恢复
与持久 inventory head 缺行拒绝已通过。Archive abort/退休/GC、
Raft Snapshot/online Replica、未归属旧 Catalog 迁移和 N2–N7 仍须全部实施，不因
本项通过而删除原完成条件。

[原未发布 Archive 清理验收](acceptance/2026-10-22-original-archive-abort.md)以
ADR-0154/wrapper v50/operations v0.2/recovery v0.13 接通原 cleanup intent、实际
source/Archive writer descriptor 与 kernel/process fence、tombstone-before-receipt，
以及 SQL 故障后只读原操作对账。实际 v49→v50 保留原 checkpoint/bytes/budget；
过期 cleanup 仍 Unknown，原 copy ID 不重发，已发布 copy 不能 abort，接收方 pin
及 partial 文件不删除。实际零 Node 升级与真实双软件 Node 文件/Catalog/冷恢复
组合已通过。本项仅关闭前条的未发布 copy abort；接收归档退休/GC、全生产者
对账、未归属 Catalog、Raft/online 及完整 N2–N7 继续必需。

[私有接收 Archive 回收](acceptance/2026-10-22-private-archive-reclamation.md)以
ADR-0155/recovery v0.14 接通全部原 writer、空完整 consumer inventory、唯一原
producer 与原 compact receipt 的显式离线退休。原 tombstone/终态 storage v4
seal 先于独立原 GC intent/unlink；旧 ID 只读，partial unlink 不补造完成，新原
GC 仅处理剩余 bytes。实际零 Node/CLI/OS 锁、两种文件故障、旧 reader 拒绝和
已删 bytes 的冷恢复通过。本项不关闭源 Artifact 全引用 GC、其它 producer 释放、
未归属旧 Catalog、Raft/online 或完整 N2–N7。

[原未归属 Catalog 接管](acceptance/2026-10-22-original-catalog-adoption.md)以
ADR-0156/wrapper v51/codec v30/recovery v0.15 接通真实旧 publication 的显式当前
保留责任。实际原 raw envelope/metadata/CAS 与 parent 固定，当前 admin/TLS/原
command 及引用同事务提交；历史身份仍 unavailable，不新增 read/use/import 权限。
实际旧 v50 producer、SQL rollback/restart/冷恢复和旧 reader 拒绝通过。源 Artifact
全引用 GC、其它 producer 释放、Raft/online 与完整 N2–N7 继续实施。

[源 Artifact 保留释放与回收](acceptance/2026-10-23-source-artifact-reclamation.md)以
ADR-0157/wrapper v52/codec v31/storage v5/recovery v0.16 接通独立 Completed 上传
退休、全部已知原 Controller/引用闭包检查、原 intent/tombstone-before-unlink 与
不确定结果只读重试。真实原 adoption 和 MI child lineage 输入分别完成 SQL rollback/
重启、有效 policy 拒绝/显式撤销/独立原目标证明、parent 退休后 child 继续保留、
真实 unlink PermissionError/Unknown、新命令余量删除、重新出现 bytes 不复活、
旧 binary/profile 拒绝和完整冷恢复。旧 profile Snapshot 由完整原/当前 profile 与实际
全部 writer 的独立原 receipt 退休，历史源不改写。
本项只关闭 standalone 已支持 authority 的离线源回收；N1 继续接 Raft Snapshot/
online Replica producer；provider 旧 profile Snapshot 的未发布恢复路径见下条。N3 多数派
内容 ACK 不变。新 GC SQL 的 hot rollback journal 显式维护属于 N7 必需项，当前只读
启动/备份拒绝而非自动修复；N2–N7 原所有完成条件保留。

[恢复后原 provider Snapshot 退休](acceptance/2026-10-23-relocated-provider-snapshot.md)以
ADR-0158 接通完整原/当前 profile、全部实际 root/use/四 SQL inode writer、双 SQL
原 instance/request/pins 核对后的既有 v0.1 abort 原事务。原 source/partial/预算不改，
Memory SQL v4、Controller/Node 合同不变。实际原 copy SIGKILL/wait、两端 writer
占用拒绝、SQL rollback、精确 retry、provider 重启和 v0.16 冷恢复通过；同时修复
完整目录校验漏掉 nested SQL 必要父目录的缺口，额外旁系目录重算 manifest 仍拒绝。
N1 下一项为 Raft Snapshot/online Replica producers；N2–N7 原所有条件继续保留。

[ADR-0159](../decisions/0159-original-content-quorum-and-raft-reference-lifecycle.md)落实这项
N1/N3 接线：先原 qualified owner/实际存储及 log/Snapshot/reader pins，再真实 mTLS/
batch 同成员多数 ACK，然后完整 Catalog/Archive/Snapshot 换主与 cold 恢复，最后
原数据显式排空/升级/受控回退和故障矩阵。任何内部小步不单独关闭 N1/N3；当前
v0.3 仍是 metadata profile，不能把新 DTO 或 fresh profile gate 算成旧部署升级。

ADR-0159 的首步已实现 `core/controller-replication/src/content_store/`：实际独立
member/root/SQL inode owner、qualified source CAS、intent/receive pin 先于读取，完整
hash/size/fsync 后才有原 receipt；managed reader 自有 pin 和实际句柄/lease 生命周期。
中断的原命令不重复，原 process-end 观察与真实读取/复制 receipt 分开；旧 Unknown
不因新的 storage repair 改写。同成员/joint 多数校验也已实现，但尚未接真实 Raft/
mTLS Commit/Snapshot，不能关闭 N1/N3。实际进程证据与限制见
[原内容存储验收](acceptance/2026-10-23-original-replica-content-storage.md)。

随后在 `5e1d307` 实际零 Node 三副本换证流程复现现代 checkpoint 换主丢失原
Archive marker；原失败日志保留。`State/replica_recovery` 现以原 writer 下完整投影与
inactive 单事务导入保留原 ID、zero-operation/copy/abort/Catalog/manifest/floor，现代
旧入口缺投影时在写入前拒绝。实际独立 capture/import/Controller 重启、原字节读取
与完整 v0.16 冷恢复通过，未补造任何原 authority。此验证共用实际原 CAS，只验证
静止 metadata 捕获与安装；v0.4 网络、Raft storage/Snapshot 与内容多数接线继续为
N1/N3 必需。见[完整 State 投影验收](acceptance/2026-10-23-complete-original-state-projection.md)。

N7 的 Controller hardlink 反例已在 `b6fe5ff` 实际双进程、同 SQL inode/不同 CAS
复现。现按 [ADR-0161](../decisions/0161-controller-inode-writer-and-storage-generation.md)
将所有 runnable Controller 的 path/inode 双租约绑定到最后一个真实 SQLite 连接。
原生旧连接排空与持久 generation fence 分开；显式 storage v1 才取得旧 writer fence，
兼容 legacy profile 不声明该能力。新 recovery v0.17 显式声明实际 Controller 格式，
保留完整原 v0.16 内容门禁；不包含新 Raft content roots。实际旧 binary、clone/独立
只读 observer、原三条上传事件/Archive 的完整代际升级与 cold 组合通过，见
[验收](acceptance/2026-10-23-controller-inode-and-generation.md)。当前兼容 legacy
不宣布持久 fence；完整部署 adoption、supervisor replacement/旧 child 日志迁移、
在线内容、hot journal 与全部原 N1–N7 完成条件继续保留。

[GC SQL 显式维护](acceptance/2026-10-23-artifact-sql-recovery.md)按 ADR-0162 接通
完整实际 writer/known consumer、原 digest/intent/forensic copy 先于原生 SQLite
rollback 与完整后验核对。实际非空原 GC/内容、10 项故障、Unknown/新 repair 并存、
只读零 effect retry、完整 v0.17 cold 与既有文件链路通过。缺历史仍拒绝且不重建。
本项关闭上述 hot journal 本地维护，仍不关闭 online 原内容、supervisor 原替换/
迁移、其它 listener/I/O 与全部原 P1–P7。

ADR-0159 的 direct-storage v4/private Commit v3 已保存完整原 candidate、可独立重算
的 digest、原 event→owner 与完整 State projection；log/current-view/Snapshot/实际
SnapshotData reader 有独立 pins。缺实际 bytes 拒绝 build/install；替换只释放旧
Snapshot，stale 不得回退原记录。实际 Controller 上传、两独立 CAS、10 个存储进程
和原 Snapshot reader kill/退休通过；content layout v0.2 另以实际旧 v1 binary 的
42 进程升级通过。生产 ReplicaConfig 仍只支持 v0.3，未发布半接线 v0.4。后续仍须
真实 mTLS byte/观察与 same-voter ACK、未发布 Snapshot/回滚 log producer 的完整原
operation intent/非续预算/中断对账、在线 Archive/GC/recovery 和原部署升级。此小步
不关闭 N1/N3。见[存储生产者验收](acceptance/2026-10-23-original-content-snapshot-producers.md)。

随后 [原生产者中断对账](acceptance/2026-10-23-original-content-producer-journal.md)以
ADR-0160/content SQL v3/parent storage v5 接通实际 Log/View/Snapshot 原意图、完整 pins、
原 SQL descriptor/实际进程/预算、Commit 后 publication 及独立原 scope/process-end。
mandatory local heads 与实际 parent 同事务；Unknown 原 ID 不再执行，独立 storage
repair 不改写早先不确定性。39 个实际进程步骤与 10 项定向检查已通过，含原 SQL 已提交
而本地回执失败、SIGKILL/native wait、独立 target/reader、实际旧 writer/格式和缺行门禁。
旧 legacy pins/缺失 source/budget 保留，原 parent 不隐式升级；完整部署迁移/adoption、
真实 mTLS 同一 voters bytes+metadata ACK、online/路由、reader Drop 等 N7 与 N2–N7
原所有条件仍须实施，N1/N3 保持开放。

随后 [ADR-0166 原 parent 接管](acceptance/2026-10-23-original-parent-adoption.md)补齐显式
v4→pending→v6 管理路径：实际 root/parent writers、native drain、原 child wait、新原
意图/完整目录 precede 当前 Log/View/Snapshot 的新保留责任。49 个实际进程步骤通过，
包含旧 writer/SQL/hardlink、CAS 缺失、原期限、kill、parent rollback、Commit 后回执
丢失及只读 retry；实际旧探针与完整 Archive/State/Snapshot 组合另通过。原业务行、
legacy pins、缺失旧历史和 Unknown 均保留。本项关闭当前 direct-storage v4 的显式接管
入口；不退休 legacy pins，不替代旧生产 v3 的完整迁移，也不关闭生产在线 v0.4、
same-voter bytes+metadata ACK、Root3/v6 完整恢复/路由和原全部 N1–N7。

[ADR-0167 成员绑定](acceptance/2026-10-23-consensus-member-content-identities.md)要求完整
target 的 member/store map 等于确切 committed member data；旧 profile 拒绝隐藏
绑定。原 Node/membership/SnapshotMeta JSON 逐字节一致，实际混合 mTLS binaries 的
9 次 wait / 6 次前进回退保持原 SQL inode/原身份/原 member rows，并通过全冻结旧
Snapshot、producer、parent 接管与 Archive 组合。这关闭成员资料的旧编码兼容与 target
绑定前置；实际新网络 bytes/metadata ACK、生产 v0.4 与原全部 N1–N7 仍须实施。

进度复核基线为 `main@efd3c83`，其
[完整 CI 已通过](https://github.com/Farewell-CK/RoboGuide/actions/runs/39636037197)。
上次分支审计时尚未提交的 live-session 工作随后形成 `227fb62`；本轮将其与 main
组合检查并合入，不改写前一次 `fc5d2ba` 冻结审计的分支统计。整合在隔离工作树检查，
原实验目录保留。

本次组合全量 Python 为 **3021 passed，0 skipped，451.35 秒**；格式/Ruff、304 个源文件
strict mypy、函数文档和 MkDocs strict 通过。实际
`/home/duchaoke/miniconda3/envs/habitat/bin/python`（3.9.23）另完成 15 个正向和 35 个
generation 拒绝案例，Provider calls 为 0，physical episode 为 false。
测试结果保存在 `/tmp/rg-progress-20261022-pytest.xml` 与
`/tmp/rg-progress-20261022-runtime-check.json`。先前未设置部署 Python 环境变量时的
skip 不表示机器没有该解释器；本次已指定实际路径，没有通过跳过检查获得通过。

F0–F3 当前软件切片已有验收；F4–F8 和 F10 已有可执行实现与专项证据，剩余项包括具体
功能接线和跨模块故障组合。F9 目前主要是 Control 内部持久 session，跨域服务入口仍须
实现。阶段数、文档数、提交数和测试数都不折算为完成百分比。

下表落实原全部非发布范围，未增加一个新的 MVP。N0 本次收拢完成，当前重点是 N1；不
重复展开已验收的算法、证书轮换、quota、MI 修订或写入恢复。

| 顺序 | 工作包 | 交付与完成条件 |
| --- | --- | --- |
| N0（已完成） | 新提交集成 | `227fb62` 与 main 的组合通过定向/3021 项全量 Python、实际 Python 3.9 协议兼容和文档门禁；软件回归覆盖失败后的活动 peer 排空、未下发 slot 保留、迟到派发和 verifier coverage，不将替身结果升级为物理事实 |
| N1 | Memory 实际引用生命周期 | 在现有本地引用、use-lease、FileJobs 与 GC 上接真实 Replica/lineage/Catalog/Snapshot/Archive 生产与释放；先区分 provider-local CAS 与 Controller Artifact CAS 的 owner，不把不同存储的相同 digest 当同一副本。每个生产者须有副作用前持久 intent、原 owner/provider/generation 绑定、ACK 或只读原命令对账；Unknown/失联/重启不释放引用，过期不冒充退休证明。ADR-0200 进一步把新 Replica evidence 绑定到原始 consumer TaskRef/execution_id，旧历史保持 unavailable。首个实现包接通一个实际生产者的完整链路，其余生产者继续为 N1 必需项 |
| N2 | typed-map 与修订组合 | 复用既有实际文件配准/融合/增量算法，验证结果从发布、原 CAS/import、active-map、当前 use、撤权、交接到重启/回收的完整链路；将原地图来源、frame、revision 和完整 lineage 带入 F8 的 scoped Memory 修订。SQL 中断、晚到 ACK、来源/配置变化不得重复导入、重启 worker 或续预算 |
| N3 | 在线 HA 内容与升级恢复 | 以 N1/N2 的内容与引用身份接通 online Archive/Snapshot/Catalog/CAS 一致性，再验证 leader 接管后的原授权内容、原 receipt 与缺失状态；补缺原 child journal 的旧部署显式排空迁移、滚动版本升级/回退。元数据换主不计为内容就绪或设备连续执行 |
| N4 | 资源成本与可分配资源 | 连接实际 load/GPU memory/queue/交通等可用成本源，保留来源、接收时间、TTL 和 unavailable；增加显式版本化 divisible allocation 及本地强制，不重解释现有 `units` 最小容量语义；验证正常/Group/recovery Commit、释放和完整时间/空间冲突 |
| N5 | 运行中抢占、耦合恢复与 MI | 在现有 whole-Group stop/hold、Memory revoke 和 Control ownership 上接策略授权的运行中抢占、多 Role 迁移、hazard/handoff/regrasp 合同；验证 stop/退出/撤权证据齐备后才进入新 Commit/Rebind。与 F8 原目标/Review/新计划/复杂拓扑组合，不重做已有副作用；设备证明缺失保留明确待验证状态 |
| N6 | Federation 服务边界 | ADR-0201/0202 已有两个独立 Controller participant 服务的 mTLS、版本化证书 domain grant、tenant 路由、原 prepare/commit/abort 回执和重启/响应丢失/SQL 回滚对账；当前进程 checker 是两个服务的外部调用者。仍必须接持久 coordinator、真实 peer 请求与各域资源/epoch 证明、跨主机 clock translation、证书轮换/分区/恢复总矩阵，不能以 local committed 关闭 N6 |
| N7 | 运维与全量验收 | 在每个前述工作包持续执行 listener 容量、阻塞 I/O、write-disabled、metrics/alert、升级/回退门禁；最终以固定 deployment profile 运行全部非发布矩阵并核对 SLO/runbook。长期、跨主机、人工响应与设备结果按各自证据填写，不能由短窗口或合成事实代替 |

N1 的第一份交付必须同时包含真实生产者代码与进程证据，不能仅增加一种引用类型、
一个接口或一份矩阵。首个生产者只是内部里程碑；完整 N1 须覆盖表中全部引用生产者，
才可转入依赖其完整性的验收。矩阵列出每类引用的权威 owner、产生时点、可观察的终止证明、
持久 intent/回执和恢复入口；新公开权限/跨服务合同按 V2 与 ADR 版本化，保留现有
same-UID Unix 入口的本地边界，引用保留权不授予内容读权。验收至少覆盖实际 acquire、
GC 拒绝、进程重启/SQL 与响应丢失、原 generation 只读对账、精确 release 及实际内容回收，
并独立读取 SQL 与 CAS 核对结果。

下一包使用部署配置、真实文件、SQLite、协议和 OS 进程，不要求设备、模拟器或模型。
全部非发布软件闭合后记录 `SoftwareComplete`；实际停止、物理交接、硬实时、硬件身份和物理连续性
继续按原要求记录 `HardwareEvidencePending`，不因此省略通用软件实现。

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
