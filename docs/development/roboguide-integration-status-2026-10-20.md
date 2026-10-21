# RoboGuide 分支同步与剩余整改核对（2026-10-20）

本次先同步远程并核对所有远程分支；在隔离工作树组合整改、MI 部署校验和执行故障闭环，
保留其他工作树和未跟踪实验文件。没有启动实验、E6、模型、模拟器或设备。

2026-10-21 [本地 Memory 内容引用生命周期](acceptance/2026-10-21-memory-content-reference-lifecycle.md)
为现有 v3 `content_references` 增加 same-UID Unix acquire/query/exact-generation release，并把
实际 use-lease grant/revoke/restart 接入引用行；跨 SQLite 失败靠原命令重放修复，lease 与引用
任一活动时均挡 GC。真实 provider restart gate 证明引用持久，零 Node/设备。它补齐本地引用写入
端，不闭合全局/跨主机 GC、replica/archive/Catalog 对账或 F4/F5。

2026-10-21 [Control tenant quota commitment](../decisions/0140-control-resource-quota-commitment.md)
接入 recovery-admin `/v1/resource-quotas` revisioned policy/authority journal、正常/Group/recovery
Commit 与 Task/Context/Group/recovery release，wrapper v47 保存 policy/history/精确 claims；缺少
历史 demand 的 v45/v46 reservation 不臆测用量；ADR-0141 的 v0.2 view 也列出尚未设限的现有用量。
167 项 Control 测试及实际双向 TLS/SQLite/重启进程验收通过，Node inventory 为零；divisible
allocation、GPU/traffic、Local EAIOS 其他
资源强制、完整时空预约和 F6/F7 总验收继续开放。

2026-10-21 [原 Controller child 排空](../decisions/0138-original-controller-child-drain-before-transfer.md)
将 pre-spawn intent、实际 PID/boot/start tick、原 supervisor wait 持久化；源应用先 fence，
只有原退出证明、原 quorum 与八秒预算齐备才发 V3 election。target 的原 receive budget
包含 member-writer 排队。storage v3 / recovery v0.7 保留完整进程日志；旧应用 checkpoint
缺少原进程记录时保持 Unknown，不能推成空进程。独立旧部署排空迁移、在线内容与跨域
仍须接续完成，软件进程证明不升级为 Node/设备停止。

2026-10-21 [成员本地 transfer 历史](../decisions/0136-member-local-leader-transfer-evidence.md)
修正本地 hold 与 Raft snapshot 混用；丢失原回包保持 Unknown，同 ID 不重发，完成须目标
实际新 quorum epoch 和源的认证只读观察。storage v2 / recovery v0.6 fence 保留旧原值与
完整操作历史；source-child stop/wait、在线内容和跨故障域恢复仍继续。
[实际进程验收](acceptance/2026-10-21-member-local-transfer-recovery.md)覆盖旧 v0/v1 writer、
三副本换证与恢复、6 SQL/42 tables 和三个恢复 leader 的原 CAS/receipt；源排空仍须独立证明。

2026-10-21 [Replica storage v1 与数据库本体 writer](acceptance/2026-10-21-replica-storage-v1.md)
修复了已确认的 hardlink inode writer 绕过：完整旧表事务迁移到 v1、旧表只读 view fence、
实际新/旧进程和 alias writer 门禁通过。Recovery profile v0.5 已冻结；durable leader drain、
在线 CAS/archive、跨故障域恢复和正式旧 binary bundle 恢复仍不关闭 F5。

2026-10-21 [Replica 升级排空与 v0.5 恢复脚本对齐](../decisions/0135-replica-upgrade-quiescence-and-recovery-validation.md)
补充迁移前 identity、SQLite 实际排空、完整 view/trigger 校验；Replica 冷恢复脚本按实际
SQL version 选择原 v0.2 或显式 v0.5，混合/未知格式拒绝。Memory provider 原 v0.4 语义保持不变。响应丢失、source child drain、在线内容和
跨故障域恢复仍未关闭完整 F5。

2026-10-21 [持久化 leader transfer intent](../decisions/0131-durable-leader-transfer-drain.md)
已保存 intent/target acknowledgement/new epoch/reconcile。复查发现 source-child drain、
响应丢失与成员本地记录/snapshot 分离仍须修正；三副本 round-trip 和 record restart
回归不能单独证明这些边界，完整 HA 保持待完成。

2026-10-21 [Active Map 实际引用 GC 门禁](acceptance/2026-10-21-active-map-gc-reference-gate.md)
新增 v0.2 实际 content_references/head/ref/pending-handoff 检查，删除仍只能由
ContentReclamation.collect 执行；旧 caller boolean v0.1 保留为 blocked。全局跨节点 GC、
archive/catalog/CAS 一致性仍未关闭 F4/F5。

2026-10-21 [Local EAIOS 软件 worker OS quota](acceptance/2026-10-21-local-process-os-quota.md)
原 CPU/address-space quota 入 admission SQL，子进程 exact `getrlimit`/PID proof 在第一次
read permit 前独立提交，并有只读查询与旧 v1 unavailable 迁移；实际 parent-death/restart
gate 直接比较 SQL 与 API 回执，通过且没有 Node/设备。Control 的 revisioned tenant/category
逻辑配额现已接入；divisible allocation、GPU/交通、硬实时和设备安全仍未关闭 F6/F7。

2026-10-21 [Relation target 与 whole-Group stop](acceptance/2026-10-21-relation-whole-group-stop.md)
复核确认原 `admit` 已覆盖全组 peers，原入口选择误述已更正。ADR-0137 恢复 exact target
入口，避免 Completed 但缺少 relation proof 的目标漏掉 stop/hold；独立 generation/receipt/
safe-state gate 保留，物理 stop、hard-realtime 与 regrasp 仍待部署证据。

main 合并前为 `5285a92a6af8bf4a99bb0852b5cdb80728c9144c`；三个集成头分别是：
`25380295a267fb4c5f2f948558460fd1b141f95f`（完整非发布整改分支）、
`b4f1b1275a546f310f61f9b79ac0bc4bb353209a`（MI 校验）、
`d15543f4ec2dc99ebe565e319e141c0bf17b433b`（执行故障闭环）。后两者是祖先关系。
代码集成和完整整改完成是不同状态；本次不宣称 SoftwareComplete。

2026-10-21 [可执行部署 profile](acceptance/2026-10-21-deployment-profile.md)接通 F0 软件
输入、原 binary/schema/file/inode、TLS 与故障/退出矩阵的只读冻结；实际一进程/三监听/
四探测/零 Node 通过。硬件、独立主机和目标 RTO/RPO 不由此升级为已达事实，其他阶段继续。

2026-10-21 [Memory writer 与完整恢复](acceptance/2026-10-21-memory-provider-writer-recovery.md)
补齐四类 SQL 的实际 inode/共享连接生命周期、原数据升级和显式 recovery v0.4。
这修复实际旧 writer 反例；全局引用、在线 HA 及其余必需阶段继续按下表执行。

[原属组 Memory 修订](acceptance/2026-10-20-scoped-memory-plan-revision.md)已接通显式
v0.3 声明、完整原停止/撤权、immutable child、Control 新组与各 Task 独立 import/use。
五种真实软件配置和原 MI/迁移回归通过，共享 wrapper v45 保留原历史与预算。
后续[非空声明 MI 整链](acceptance/2026-10-21-runtime-mi-scoped-memory.md)补齐显式
v0.9 DTO 和真实原 Request/自动故障/中断与响应丢失/独立 Review/新组/独立原目标组合。
完整 Memory 数据面及其余阶段仍按下表继续验收。

再次固定核对 `codex/e1-perception-contract-composition` 的新增头
`983c8810b91ccbd5634dd9b3b7a9901c510d38cc`：四条维护提交保留原历史合入，
有界扫描、原多 agent 参数/完整动作编码和缓存证据在零模型兼容门禁下检查。
见[有界观测集成验收](acceptance/2026-10-20-bounded-observation-integration.md)。
未把实验结果或设备事实纳入该合并。

[原文件进程与接收身份](acceptance/2026-10-20-file-process-memory-ownership.md)补齐实际
Local EAIOS 子进程 wait/崩溃 fence、原预算读取许可和后续 Task 的独立 import。双 Node
停止/交接/两次复算、SQL 显式恢复和负向生命周期已检查；跨 session mixed-fault 停止恢复、
全局 Memory 引用/在线 HA 与完整 scoped Memory/MI 组合继续为必需项。

后续以已验收 `6ca3ebb37fca940626adc0d68755fc38a0534f0f` 为 main 基线，固定核对
`codex/e1-perception-contract-composition` 的 `b50d9e313b0660c9cc4ee1c5bca53538fa0b1de6`。
其六条新增修复通过三方合并保留原提交，不回退整改权威版本，也没有启动实验；组合证据见
[新增执行合同集成验收](acceptance/2026-10-20-current-execution-contract-integration.md)。

后续 [ADR-0116 证书验收](acceptance/2026-10-20-replica-certificates.md)补齐显式 v0.3
同一成员换证、lost-key Abort、实际 CA 迁移与重启。旧 profile/二进制升级和完整 HA 内容
恢复继续 F5；F0/F4/F6–F10 不因证书软件验收关闭。

## 分支处理记录

下表 ahead/behind 均相对合并前 main；`独有补丁` 由 `git cherry` 相对整改分支核对。
0 个独有补丁说明 Git patch 等价，不等于分支提交成为祖先。对已重构的旧实现另外检查代码。
没有共同根历史的 `RoboGuide-Remote`、`guide-assistant*`、`sunweihao` 独立项目不使用 `--allow-unrelated-histories` 强行合入。

| 远程分支 | SHA | main 独有 / 分支独有提交 | 独有补丁 | 处理 |
| --- | --- | ---: | ---: | --- |
| `origin/RoboGuide-Remote` | `40bb5ea2fa68` | 447 / 15 | 无共同历史 | 独立根历史；保留原分支，不能作为 V2 修复直接合并 |
| `origin/codex/authoritative-goal-satisfaction` | `425c29a79b9d` | 204 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/b1-event-archival` | `ad02d5624753` | 276 / 1 | 0 | 补丁已等价吸收；保留历史引用，避免重复导入 |
| `origin/codex/b1-semantic-ingress` | `ccf265d67a77` | 295 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/b1-shared-world-resource` | `3f8845df7373` | 279 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/dual-ai-sync-foundation` | `5b3bde4b33c0` | 99 / 1 | 1 | 旧实验协同工作流；不覆盖现行 AGENTS 或启动同步脚本 |
| `origin/codex/e1-deployment-contract` | `f25daf0c468f` | 237 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/e1-diagnostics-evidence-closure` | `9ea39c207730` | 251 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/e1-failure-closure` | `d15543f4ec2d` | 0 / 18 | 14 | 本次合并：保留提交历史并验收组合合同 |
| `origin/codex/e1-navigation-integration` | `f70979ec64a9` | 240 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/e1-perception-rearrangement-optimization` | `5285a92a6af8` | 0 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/e1-physical-diagnostics-acceptance` | `69669dda3c56` | 270 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/e2-coherent-gpt6-sol` | `9e2f873b6ced` | 94 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/codex/e2-coherent-gpt6-sol-pre-recovery-fix` | `7f5a0bd39884` | 196 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/e2-live-status-sync` | `d4cb44666f3e` | 102 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/codex/ep51-final-repeatability-report` | `7fc894bc2631` | 250 / 7 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/codex/ep51-full-chain-diagnostic-20260921` | `2922d1e48fb9` | 251 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/codex/ep51-full-chain-diagnostic-report` | `5ed8749a156e` | 254 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/codex/ep51-full-chain-readiness` | `cb3eee10d1aa` | 254 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/ep51-initial-state-paired-comparison-report` | `7ad99d1e0dbf` | 250 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/codex/ep51-initialization-parity` | `a24feeba4b84` | 250 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/ep51-mi-prompt-ab-report` | `322bb8d39f83` | 258 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/codex/ep51-repeatable-navigation` | `7dfdf3891487` | 250 / 6 | 0 | 补丁已等价吸收；保留历史引用，避免重复导入 |
| `origin/codex/ep51-reviewer-only-report` | `a2ef7eb0befb` | 257 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/codex/ep51-rgb-evidence` | `710280de5b23` | 250 / 9 | 0 | 补丁已等价吸收；保留历史引用，避免重复导入 |
| `origin/codex/mi-coordination-guidance` | `6623799da538` | 258 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/mi-deployment-validation` | `b4f1b1275a54` | 0 / 7 | 3 | 本次合并：保留提交历史并验收组合合同 |
| `origin/codex/mi-prompt-refinement` | `28a3dcda5eb4` | 257 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/mi-semantic-admission` | `c4258266d9f9` | 252 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/remediation-complete-software-2026-10-15` | `25380295a267` | 0 / 29 | 0 | 本次合并：保留提交历史并验收组合合同 |
| `origin/codex/shared-world-idle-endpoint` | `5589f3d0f007` | 196 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/spatial-feasibility-gate` | `7166b5d81663` | 210 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/codex/stage2-contract-guard` | `543b1683d604` | 251 / 5 | 5 | 旧 guard 已由主线 Stage2ExecutionContract 重构替代；不回灌旧 hook/诊断 |
| `origin/codex/stage2-execution-contract` | `c80d2cfb919f` | 250 / 2 | 0 | 补丁已等价吸收；保留历史引用，避免重复导入 |
| `origin/codex/verifier-evidence-ingress` | `2c9e9d81ac30` | 206 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/dev` | `e5e8e122c32b` | 12 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/feat/guide-assistant` | `998aa52b05b6` | 447 / 31 | 无共同历史 | 独立根历史；保留原分支，不能作为 V2 修复直接合并 |
| `origin/feat/guide-assistant-heading-calibration` | `fef864ff82b5` | 447 / 5 | 无共同历史 | 独立根历史；保留原分支，不能作为 V2 修复直接合并 |
| `origin/feat/guide-assistant-qwen-speech-text` | `f1ed46167d2c` | 447 / 16 | 无共同历史 | 独立根历史；保留原分支，不能作为 V2 修复直接合并 |
| `origin/feat/hcore-heterogeneous-v0.1` | `aa1bf4411ce0` | 210 / 2 | 2 | MuJoCo 场景与适配分支；不启动/合入本轮排除的实验路径 |
| `origin/feat/hcore-homogeneous-v0.2` | `ed0e1f1980ec` | 210 / 4 | 4 | MuJoCo 场景与适配分支；不启动/合入本轮排除的实验路径 |
| `origin/feat/mobile-navigation-android` | `4af7dfabf91c` | 390 / 3 | 2 | 独立 Android/NPU 产品；缺当前构建/原生依赖兼容验收，保留待专项集成 |
| `origin/feat/mobile-navigation-npu` | `6432b7c31442` | 385 / 9 | 9 | 独立 Android/NPU 产品；缺当前构建/原生依赖兼容验收，保留待专项集成 |
| `origin/main` | `5285a92a6af8` | 0 / 0 | 0 | main 合并前基线 |
| `origin/sunweihao` | `317b26ffa398` | 447 / 57 | 无共同历史 | 独立根历史；保留原分支，不能作为 V2 修复直接合并 |
| `origin/zcode/b1-generic-runner` | `da53f9f15f82` | 282 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/zcode/e1-ep51-physical-diagnostics` | `15f48cfa69ae` | 272 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/zcode/e1-fairness-foundation` | `a852e5ea91df` | 295 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/zcode/e1-runner-lifecycle-wait` | `02f164b8831b` | 260 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/zcode/mi-plan-validation-recovery` | `21a5fe8e02de` | 267 / 0 | 0 | 已在 main 祖先中，无需再次合并 |
| `origin/zcode/validate-mission-v08-provider` | `8636f63f1ab7` | 320 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |
| `origin/zcode/validate-mission-v08-reval` | `42807c2577ac` | 319 / 1 | 1 | 实验/报告/Provider 预检归档；本轮按用户范围保留 |

Android/NPU 分支包含独立 Gradle/CMake/native 库构建，当前环境没有配置 Android SDK；
尚未把它们当作 V2 可用 Node 或通用 Core 功能验收。H-CoRE 分支包含固定角色 MuJoCo
场景和历史报告，不把这些场景当作设备或本次通用软件完成证据。
Stage2 旧分支的 guard 已由当前 `stage2_contract.py`、`diagnostics.py` 与相关测试覆盖并继续
演进，旧 hook 不能覆盖当前原始模型调用、严格语义绑定与有界诊断合同。

## 本次补齐的集成缺口

两条整改线独立占用了 Node v0.8、protobuf tag 8、wrapper v32、ADR-0106/0107。
[ADR-0111](../decisions/0111-branch-integration-and-authority-version-fences.md)统一为
Node Contract v0.12/config v0.13/catalog v0.15、独立 generation tag 11、wrapper v41/journal v12。
Memory preparation 与 Execute 固定同一代次；旧 metadata 不自动升级能力；旧 writer 与
含进程退出权威的歧义 fork checkpoint 拒绝。原 MI 校验与 Memory/handoff 权威同时保留。
原进程退出仅支持 SoftwareProcess，不改变 Runtime 的原始 Failed/Completed。
真实交接组合回归另发现取消 outcome 触发自然失败收尾的 ownership 冲突：自动收尾须有
当前原始 Failed attempt，且不能绕过 handoff 保留的 binding；Cancelled 不授权释放源组。
Artifact 的最后一个应用 store/clone/upload 显式释放 writer lease，避免无写入权子进程
继承 descriptor 时合法重启被误阻断；实际子进程保留和上传引用检查覆盖该故障。

[ADR-0121 的实际 MI 链路](acceptance/2026-10-20-runtime-mission-intelligence.md)补齐故障生成、
原 SQL claim/phase nonce、真实 Request/admission、原始回包、独立 Review 和只读对账。
wrapper v44 保留实际 v43 原数据；恢复旧 MI SQL 不能重复 provider phase。协议端点是
明确 fixture，外部模型与设备均为零；自动发现和 scoped Memory 迁移继续必需。

## 仍必须完成的非发布工作

[MI writer 联合恢复](acceptance/2026-10-20-runtime-mi-recovery.md)以显式 v0.3 纳入新 SQL
worker 的 sibling/inode 双锁和原 nonce 格式。实际四个 SQL/46 tables 的 schema/rows
相同；活动与硬链接别名拒绝，旧工具拒绝，新目录不启动服务。完整在线与跨域门禁仍继续。

后续实现与实际证据见[原 provider 回收与静止恢复](acceptance/2026-10-20-content-reclamation-and-recovery.md)。
本地布尔 GC 已改为无删除权；实际 original revoke、tombstone、unlink/SQL 中断对账和
Active Map 全输入门禁已接通。Node journal v13 增加唯一 writer；静止 SQL/CAS 恢复通过，
全局 reference、在线内容恢复和下表其余项继续必需。

[写入禁用与显式恢复](acceptance/2026-10-20-authority-write-recovery.md)接通真实 SQL
save/commit 故障后的最后提交查询、Node unavailable、上传中 chunk fence 与原证据探针。
它保留原复制 watchdog/受控 replay；F10 完整告警/SLO 和写锁 I/O 优化仍未关闭。

[原生监控与通知验收](acceptance/2026-10-20-observability.md)接通真实 Prometheus/
Alertmanager、两次 firing/resolved 与实际 replica metrics。该切片引入 wrapper v42，
Mission authorization v0.2 的专用 metrics-reader 不可业务读写；旧 writer/降级拒绝与
v41 证据迁移均检查。长期 SLO、跨主机/值守路由和写锁 I/O 收敛继续为必需项。

| 阶段 | 剩余必需交付 | 下一项可验收结果 |
| --- | --- | --- |
| F0 | 闭合配置、原 binary/文件/store inode、TLS/支持矩阵、故障/退出输入和实际零 Node 门禁已接通 | 后续总矩阵沿用实际 profile 与固定证据；真实主机独立性、RTO/RPO 实测和硬件仍按各阶段验收 |
| F4 | Controller 与 backend 的完整 active-map/引用生命周期、tombstone/GC、撤销与 snapshot/archive/CAS 一致恢复；跨主机证据 | 先打通原授权、所有引用与恢复门禁下的可审计删除；局部八类引用 GC 不视为全局完成 |
| F5 | 副本证书轮换、兼容升级协议、archive + snapshot + catalog + CAS 数据面恢复 | HA 接管后原内容可读或明确缺失；升级与回退不丢历史/预算/权限 |
| F6 | divisible allocation 与 Local EAIOS 全资源强制、完整时空承诺、真实成本源 | tenant/category 逻辑 quota 已接入 Control Commit；缺 GPU/定位/交通 evidence 为 unavailable，逻辑账本不冒充 OS/物理强制 |
| F7 | 策略授权的运行中抢占、耦合多 Role 恢复、异构 handoff/regrasp 与 hazard 合同 | 原完整 stop 集合、上下文验证、新 Commit/Rebind 和不确定性保留；设备证明独立 |
| F8 | 原目标/版本、MI claim/nonce/发现/原回包对账、显式 v0.9 非空 scoped Memory 和安全新 Group 已通过实际通用文件组合；其余类型/复杂拓扑仍须随 F4/F7 总矩阵完成 | 保留既成事实与原目标，经 Review/Control/stop gate 切换，不重做已完成副作用或未知模型调用；普通 resume/recovery 不绕过待完成修订 |
| F9 | 真正跨控制域 transport、证书/tenant/domain 路由与多域恢复 | prepare/commit session 经真实服务边界消费，不把单进程记录当跨域完成 |
| F10 | write-disabled 只读恢复、写锁内阻塞 I/O 收敛、完整指标/告警/SLO/runbook 和总验收 | 已提交快照仍可查询，恢复入口可审计；实际采集和告警演练后再关闭 |

F1–F3 当前软件切片已验收，但跨阶段消费仍必须通过组合验收；不能由文档、类型、测试替身
或本机进程得出物理安全、硬件身份或跨主机连续性。发布、许可证和生产上线不在用户本轮范围。
后续按 [全部非发布实施计划](roboguide-software-completion-plan-2026-10-15.md) 执行，
下一工作包优先 F4 完整引用/GC 与恢复一致性，再推进 F5 数据面恢复。
