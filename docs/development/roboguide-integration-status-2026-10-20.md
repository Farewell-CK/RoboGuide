# RoboGuide 分支同步与剩余整改核对（2026-10-20）

本次先同步远程并核对所有远程分支；在隔离工作树组合整改、MI 部署校验和执行故障闭环，
保留其他工作树和未跟踪实验文件。没有启动实验、E6、模型、模拟器或设备。

main 合并前为 `5285a92a6af8bf4a99bb0852b5cdb80728c9144c`；三个集成头分别是：
`25380295a267fb4c5f2f948558460fd1b141f95f`（完整非发布整改分支）、
`b4f1b1275a546f310f61f9b79ac0bc4bb353209a`（MI 校验）、
`d15543f4ec2dc99ebe565e319e141c0bf17b433b`（执行故障闭环）。后两者是祖先关系。
代码集成和完整整改完成是不同状态；本次不宣称 SoftwareComplete。

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

## 仍必须完成的非发布工作

| 阶段 | 剩余必需交付 | 下一项可验收结果 |
| --- | --- | --- |
| F0 | 将部署 profile 的拓扑、目录、故障域、身份/协议支持矩阵与退出条件做成可验证输入 | 配置驱动的零 Node 启动和软件部署门禁；真实地址/设备证据缺失保持显式 |
| F4 | Controller 与 backend 的完整 active-map/引用生命周期、tombstone/GC、撤销与 snapshot/archive/CAS 一致恢复；跨主机证据 | 先打通原授权、所有引用与恢复门禁下的可审计删除；局部八类引用 GC 不视为全局完成 |
| F5 | 副本证书轮换、兼容升级协议、archive + snapshot + catalog + CAS 数据面恢复 | HA 接管后原内容可读或明确缺失；升级与回退不丢历史/预算/权限 |
| F6 | divisible quota 的精确单位与 Local EAIOS 强制、完整时空承诺、真实成本源 | 缺 GPU/定位/交通 evidence 为 unavailable；逻辑账本不冒充 OS/物理强制 |
| F7 | 策略授权的运行中抢占、耦合多 Role 恢复、异构 handoff/regrasp 与 hazard 合同 | 原完整 stop 集合、上下文验证、新 Commit/Rebind 和不确定性保留；设备证明独立 |
| F8 | 运行中计划 revision/候选与替代计划、预授权、持久预算、去重与安全切换 | 保留既成事实与原目标，经 Review/Control/stop gate 切换，不重做已完成副作用 |
| F9 | 真正跨控制域 transport、证书/tenant/domain 路由与多域恢复 | prepare/commit session 经真实服务边界消费，不把单进程记录当跨域完成 |
| F10 | write-disabled 只读恢复、写锁内阻塞 I/O 收敛、完整指标/告警/SLO/runbook 和总验收 | 已提交快照仍可查询，恢复入口可审计；实际采集和告警演练后再关闭 |

F1–F3 当前软件切片已验收，但跨阶段消费仍必须通过组合验收；不能由文档、类型、测试替身
或本机进程得出物理安全、硬件身份或跨主机连续性。发布、许可证和生产上线不在用户本轮范围。
后续按 [全部非发布实施计划](roboguide-software-completion-plan-2026-10-15.md) 执行，
下一工作包优先 F4 完整引用/GC 与恢复一致性，再推进 F5 数据面恢复。
