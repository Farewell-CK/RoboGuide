# RoboGuide 全分支与整改存量复核（2026-10-22）

状态：已完成本次 Git 存量与当前源码核对；不据此关闭全部整改。
冻结代码：`main = origin/main = fc5d2ba0bbe060666c645c8c895efc2369ba097e`。
核对日期：2026-10-22（Asia/Shanghai）；统计冻结于上述基线，文档提交不在此代码基线中。

## 本次结论

18 个本地分支的已提交历史全部在 main。60 个远端分支中，35 个是 main 祖先或 main
本身，4 个仅补丁等价，16 个仍有独有补丁，5 个没有共同历史。逐条检查 16 个分支中
去重后的 31 个独有提交，没有发现遗漏在分支里的通用非发布整改实现。这里的“未发现”
覆盖可见 refs、10 个工作树、1 个 stash，以及仍可由 reflog 访问的 10 个额外提交；不覆盖
其他克隆、未 fetch 的其他 remote、已被 Git 清理的对象或工作区外文件。

最近整改中的 Memory、资源配额、leader transfer、恢复工具，以及 MI 校验与执行故障
闭环均已经在 main。此前清单仍用“F4/F5/F10 未完成”统称整个阶段，容易把已经实现的
功能重复算成未做；本次把已有代码、真正缺接线、组合验收和部署证据分开记录。

当前原工作树 `codex/live-session-failure-closure` 还有另一会话正在修改的 Habitat
执行会话失败收尾。它在本次起始检查时与 main 同头，没有独有提交，但有未提交源码；
审计中修改数从 5 增至 9，说明工作仍在进行。该内容尚不能计入 main，也不能把它当作
一条可直接合并的已验收分支。本次保留其全部文件和实验目录。

## 核对方法与证据边界

同步所有已配置远端，另以 `git ls-remote --heads origin` 核对远端头。对每个本地和
远端 ref 执行共同祖先、`main...branch` 左右提交计数与 ancestry 检查；非祖先但同根的
分支再用 `git cherry` 区分等价补丁和独有补丁。逐提交检查独有补丁的路径及其实际用途，
而不是根据分支名称判断。补丁等价不称为保留了分支历史；已重构功能也不称为字节等价。

另检查 `git worktree list --porcelain`、各工作树 tracked/untracked 状态、stash patch
和 `git rev-list --reflog --not --all`。没有 apply/pop stash，没有 reset/clean 工作树，
没有修改运行代码、启动实验、调用模型或创建设备事实。下文引用的验收是 main 内既有
记录，不改写为本次重新执行成功。

## 整改开发线已经进入 main

| 开发线/实现 | 已在 main 的提交 | 核对结果 |
| --- | --- | --- |
| 原完整非发布整改分支 | `25380295a267` | main 祖先；不是待合并的隐藏完成分支 |
| MI 部署计划校验 | `b4f1b1275a54` | main 祖先；发布计划预检/MI 接线已经集成 |
| 执行故障闭环 | `d15543f4ec2d` | main 祖先；与整改冲突在 `008aa8a203c8` 中组合 |
| 后续持续整改分支 | `645416a57674` | main 祖先；不是尚未同步的较新实现 |
| perception/执行合同组合 | `5989dd5872c9` | main 祖先；`87c7fcc` 保留合并历史 |
| tenant/category quota | `57c134f`、`3cc5d7f` | Commit/release 和未设限 usage view 已在 main |
| 原 Controller child 排空 | `1f94029` | V3 transfer 的原 spawn/PID/wait 和预算 fence 已在 main |
| use-lease 当前引用投影 | `39c00b8` | 紧凑投影、旧代清理和保守 GC gate 已在 main |
| FileJobs CAS execution 引用 | `7456e24`、`30781bf` | 实际 worker 引用和有界恢复 scan 已在 main |
| Unknown restart 回归 | `64603ff` | 回归与确定性时钟修正已在 main |
| 恢复包活动引用一致性 | `eab4fb4`、`fc5d2ba` | 原 provider CAS 对账、拒绝冲突与验收记录已在 main |

## 全部本地分支

下表仅描述已提交历史。分支不是清单中独立的待实现功能；工作树修改另见后文。

| 本地分支 | 冻结 SHA | 已提交状态 |
| --- | --- | --- |
| `codex/authoritative-goal-satisfaction` | `425c29a79b9d` | 已在 main；分支工作树的未提交内容另查 |
| `codex/e1-failure-closure` | `d15543f4ec2d` | 已在 main；分支工作树的未提交内容另查 |
| `codex/e1-perception-contract-composition` | `5989dd5872c9` | 已在 main；分支工作树的未提交内容另查 |
| `codex/e1-perception-rearrangement-optimization` | `5285a92a6af8` | 已在 main；分支工作树的未提交内容另查 |
| `codex/file-job-content-references-20261021` | `7456e242ea9b` | 已在 main；分支工作树的未提交内容另查 |
| `codex/file-job-reference-reconcile-20261021` | `30781bfa8ada` | 已在 main；分支工作树的未提交内容另查 |
| `codex/live-session-failure-closure` | `fc5d2ba0bbe0` | 已在 main；分支工作树的未提交内容另查 |
| `codex/main-integration-2026-10-20` | `008aa8a203c8` | 已在 main；分支工作树的未提交内容另查 |
| `codex/mi-deployment-validation` | `b4f1b1275a54` | 已在 main；分支工作树的未提交内容另查 |
| `codex/process-reference-unknown-restart-20261021` | `64603ffad13d` | 已在 main；分支工作树的未提交内容另查 |
| `codex/recovery-reference-consistency-20261021` | `fc5d2ba0bbe0` | 已在 main；分支工作树的未提交内容另查 |
| `codex/remediation-complete-software-2026-10-15` | `25380295a267` | 已在 main；分支工作树的未提交内容另查 |
| `codex/software-completion-2026-10-20` | `645416a57674` | 已在 main；分支工作树的未提交内容另查 |
| `codex/spatial-feasibility-gate` | `7166b5d81663` | 已在 main；分支工作树的未提交内容另查 |
| `codex/use-lease-reference-projection-20261021` | `39c00b8672bf` | 已在 main；分支工作树的未提交内容另查 |
| `codex/verifier-evidence-ingress` | `2c9e9d81ac30` | 已在 main；分支工作树的未提交内容另查 |
| `dev` | `e5e8e122c32b` | 已在 main；分支工作树的未提交内容另查 |
| `main` | `fc5d2ba0bbe0` | 已在 main；分支工作树的未提交内容另查 |

## 全部远端分支

`main 独有 / 分支独有` 是提交图计数；`独有补丁` 仅对存在共同历史的分支有意义。
等价的 4 个分支共有 18 个非祖先提交；16 个独有分支共享部分提交，去重后是 31 个
独有提交，不能把每个分支的 ahead 数直接相加当工作量。

| origin 分支 | 冻结 SHA | main 独有 / 分支独有 | 独有补丁 | 结论/用途 |
| --- | --- | ---: | ---: | --- |
| `RoboGuide-Remote` | `40bb5ea2fa68` | 无共同历史 | — | 无共同历史 |
| `codex/authoritative-goal-satisfaction` | `425c29a79b9d` | 309 / 0 | 0 | 已在 main 祖先中 |
| `codex/b1-event-archival` | `ad02d5624753` | 381 / 1 | 0 | 补丁等价吸收，非祖先 |
| `codex/b1-semantic-ingress` | `ccf265d67a77` | 400 / 0 | 0 | 已在 main 祖先中 |
| `codex/b1-shared-world-resource` | `3f8845df7373` | 384 / 0 | 0 | 已在 main 祖先中 |
| `codex/dual-ai-sync-foundation` | `5b3bde4b33c0` | 204 / 1 | 1 | 旧实验协同约定；不覆盖现行 AGENTS |
| `codex/e1-deployment-contract` | `f25daf0c468f` | 342 / 0 | 0 | 已在 main 祖先中 |
| `codex/e1-diagnostics-evidence-closure` | `9ea39c207730` | 356 / 0 | 0 | 已在 main 祖先中 |
| `codex/e1-failure-closure` | `d15543f4ec2d` | 87 / 0 | 0 | 已在 main 祖先中 |
| `codex/e1-navigation-integration` | `f70979ec64a9` | 345 / 0 | 0 | 已在 main 祖先中 |
| `codex/e1-perception-contract-composition` | `5989dd5872c9` | 45 / 0 | 0 | 已在 main 祖先中 |
| `codex/e1-perception-rearrangement-optimization` | `5285a92a6af8` | 105 / 0 | 0 | 已在 main 祖先中 |
| `codex/e1-physical-diagnostics-acceptance` | `69669dda3c56` | 375 / 0 | 0 | 已在 main 祖先中 |
| `codex/e2-coherent-gpt6-sol` | `9e2f873b6ced` | 199 / 1 | 1 | E2 数据集修正工具，非 OS 整改 |
| `codex/e2-coherent-gpt6-sol-pre-recovery-fix` | `7f5a0bd39884` | 301 / 0 | 0 | 已在 main 祖先中 |
| `codex/e2-live-status-sync` | `d4cb44666f3e` | 207 / 1 | 1 | 实验结果状态发布工具，非服务恢复入口 |
| `codex/ep51-final-repeatability-report` | `7fc894bc2631` | 355 / 7 | 1 | 历史实验报告/证据 |
| `codex/ep51-full-chain-diagnostic-20260921` | `2922d1e48fb9` | 356 / 1 | 1 | 历史实验报告/证据 |
| `codex/ep51-full-chain-diagnostic-report` | `5ed8749a156e` | 359 / 1 | 1 | 历史实验报告/证据 |
| `codex/ep51-full-chain-readiness` | `cb3eee10d1aa` | 359 / 0 | 0 | 已在 main 祖先中 |
| `codex/ep51-initial-state-paired-comparison-report` | `7ad99d1e0dbf` | 355 / 1 | 1 | 历史实验报告/证据 |
| `codex/ep51-initialization-parity` | `a24feeba4b84` | 355 / 0 | 0 | 已在 main 祖先中 |
| `codex/ep51-mi-prompt-ab-report` | `322bb8d39f83` | 363 / 1 | 1 | 历史实验报告/证据 |
| `codex/ep51-repeatable-navigation` | `7dfdf3891487` | 355 / 6 | 0 | 补丁等价吸收，非祖先 |
| `codex/ep51-reviewer-only-report` | `a2ef7eb0befb` | 362 / 1 | 1 | 历史实验报告/证据 |
| `codex/ep51-rgb-evidence` | `710280de5b23` | 355 / 9 | 0 | 补丁等价吸收，非祖先 |
| `codex/file-job-content-references-20261021` | `7456e242ea9b` | 13 / 0 | 0 | 已在 main 祖先中 |
| `codex/file-job-reference-reconcile-20261021` | `30781bfa8ada` | 12 / 0 | 0 | 已在 main 祖先中 |
| `codex/main-integration-2026-10-20` | `008aa8a203c8` | 61 / 0 | 0 | 已在 main 祖先中 |
| `codex/mi-coordination-guidance` | `6623799da538` | 363 / 0 | 0 | 已在 main 祖先中 |
| `codex/mi-deployment-validation` | `b4f1b1275a54` | 98 / 0 | 0 | 已在 main 祖先中 |
| `codex/mi-prompt-refinement` | `28a3dcda5eb4` | 362 / 0 | 0 | 已在 main 祖先中 |
| `codex/mi-semantic-admission` | `c4258266d9f9` | 357 / 0 | 0 | 已在 main 祖先中 |
| `codex/process-reference-unknown-restart-20261021` | `64603ffad13d` | 10 / 0 | 0 | 已在 main 祖先中 |
| `codex/recovery-reference-consistency-20261021` | `fc5d2ba0bbe0` | 0 / 0 | 0 | 已在 main 祖先中 |
| `codex/remediation-complete-software-2026-10-15` | `25380295a267` | 76 / 0 | 0 | 已在 main 祖先中 |
| `codex/shared-world-idle-endpoint` | `5589f3d0f007` | 301 / 0 | 0 | 已在 main 祖先中 |
| `codex/software-completion-2026-10-20` | `645416a57674` | 15 / 0 | 0 | 已在 main 祖先中 |
| `codex/spatial-feasibility-gate` | `7166b5d81663` | 315 / 0 | 0 | 已在 main 祖先中 |
| `codex/stage2-contract-guard` | `543b1683d604` | 356 / 5 | 5 | 旧 Habitat guard/观测分支；见下文语义差异 |
| `codex/stage2-execution-contract` | `c80d2cfb919f` | 355 / 2 | 0 | 补丁等价吸收，非祖先 |
| `codex/use-lease-reference-projection-20261021` | `39c00b8672bf` | 14 / 0 | 0 | 已在 main 祖先中 |
| `codex/verifier-evidence-ingress` | `2c9e9d81ac30` | 311 / 0 | 0 | 已在 main 祖先中 |
| `dev` | `e5e8e122c32b` | 117 / 0 | 0 | 已在 main 祖先中 |
| `feat/guide-assistant` | `998aa52b05b6` | 无共同历史 | — | 无共同历史 |
| `feat/guide-assistant-heading-calibration` | `fef864ff82b5` | 无共同历史 | — | 无共同历史 |
| `feat/guide-assistant-qwen-speech-text` | `f1ed46167d2c` | 无共同历史 | — | 无共同历史 |
| `feat/hcore-heterogeneous-v0.1` | `aa1bf4411ce0` | 315 / 2 | 2 | MuJoCo 固定角色适配/场景，保留实验范围 |
| `feat/hcore-homogeneous-v0.2` | `ed0e1f1980ec` | 315 / 4 | 4 | MuJoCo 固定角色适配/场景，保留实验范围 |
| `feat/mobile-navigation-android` | `4af7dfabf91c` | 495 / 3 | 2 | 独立 Android Local System，未接通当前 V2 Node 合同 |
| `feat/mobile-navigation-npu` | `6432b7c31442` | 490 / 9 | 9 | 独立 NPU Android Local System，未接通当前 V2 Node 合同 |
| `main` | `fc5d2ba0bbe0` | 0 / 0 | 0 | 已在 main 祖先中 |
| `sunweihao` | `317b26ffa398` | 无共同历史 | — | 无共同历史 |
| `zcode/b1-generic-runner` | `da53f9f15f82` | 387 / 0 | 0 | 已在 main 祖先中 |
| `zcode/e1-ep51-physical-diagnostics` | `15f48cfa69ae` | 377 / 0 | 0 | 已在 main 祖先中 |
| `zcode/e1-fairness-foundation` | `a852e5ea91df` | 400 / 0 | 0 | 已在 main 祖先中 |
| `zcode/e1-runner-lifecycle-wait` | `02f164b8831b` | 365 / 0 | 0 | 已在 main 祖先中 |
| `zcode/mi-plan-validation-recovery` | `21a5fe8e02de` | 372 / 0 | 0 | 已在 main 祖先中 |
| `zcode/validate-mission-v08-provider` | `8636f63f1ab7` | 425 / 1 | 1 | 历史外部 Provider 预检记录 |
| `zcode/validate-mission-v08-reval` | `42807c2577ac` | 424 / 1 | 1 | 历史外部 Provider 预检记录 |

### 不能笼统称为“已经合并”的内容

- `stage2-contract-guard`：当前主线已有不同实现的 `Stage2ExecutionContract`、原始
  tool-call 数检查、严格参数绑定和有界观测。旧分支并非全部语义等价：例如它单独记录
  `model_client_error`，当前 guard 对原模型异常原样抛出，没有同名的独立观测记录。
  这是保留的历史适配器观测差异，不是 F4–F10 通用整改藏在旧分支的证据；不回灌旧 hook
  覆盖当前合同。也不据该分支的实验报告宣称设备执行成功。
- 两个 H-CoRE 分支：新增内容在 MuJoCo Local EAIOS、固定角色场景、SLAM 场景和脚本，
  没有新增通用 Control/Runtime/State/Memory authority。按用户排除实验的范围保留。
- 两个 mobile-navigation 分支：是 Android/NPU Local System，包含 Gradle、CMake 和原生
  依赖。它们有独有源码，但没有当前 V2 注册/身份/执行合同的通用整改接线；本次未运行
  Android 构建或把它们注册成 Node，也不把独立应用源码计为当前 OS 功能完成。
- 五个无共同历史的分支：是另一个 Flutter/Android 等产品根历史。保留原分支，不能用
  `--allow-unrelated-histories` 将不同产品历史强行并入 V2。
- E2 live-status 工具：读历史实验结果、生成/发布实验状态，不是 Controller 的
  write-disabled 恢复、HA leader transfer 或生产告警链路。

## 工作树、stash 与 reflog

本次初始检查的 10 个工作树 HEAD 均是冻结 main 的祖先或 main 本身，其中 7 个干净，
3 个有修改；不将本次后写入的审计文档计入该初始状态。

| 工作树 | 本次发现 | 处理 |
| --- | --- | --- |
| `/data/workspace/code/RoboGuide` | 活动 `codex/live-session-failure-closure`；源码/测试/文档尚未提交；另有历史实验与会议目录 | 保留另一会话工作；当前 main 不包含这些修改 |
| `/tmp/rg-worker-old-source-20261020` | 原 worker-isolation 和 leader-transfer 两个回归的旧源码反例副本 | 对照 main：测试已存在；主线还包含后续 schema/格式调整；无需重复合并 |
| `/tmp/rg-operator-old-source-20261020` | operator worker-isolation 的旧源码反例副本 | 原测试主体已在 main，当前 helper 增加后续 runtime revision 字段 |
| 其余 7 个工作树 | 无 tracked/untracked 修改；含隔离整改、集成、MI、verifier 与冻结实验 checkout | 没有待提交的遗漏整改代码 |

唯一 stash 是 `c491471fa51d`，其工作 patch 只是 4 个 Mission Python 文件中的 import
顺序修正；当前文件均已有正确顺序。stash 的旧 base 另保留 8 个 actor-binding 历史提交，
`git cherry` 不是等价；当前 `1ce01e6` 及后续主线实现了重新组合后的 actor/physical-entity
语义、Control 检查、部署 registry、v0.8 schema/MI 及对应测试。不能把这 8 个旧提交当作
8 项缺失功能，也不能说它们的提交历史已经合并。stash 保留原样。

reflog 中额外 10 个提交逐一核对如下；它们不是当前分支头。

| 旧提交 | main 对照 | 结论 |
| --- | --- | --- |
| `359b075` | `eab4fb4` | Git patch 等价；恢复修复已在 main |
| `bda8778` | main 等价补丁 | `git cherry` 判为等价；大事件前缀修复未遗漏 |
| `425dd7b` | `a8f6aec` | 原提交所改 4 个文件在两头完全一致；后续已升级合同 |
| `edd8b4e` | `6dc732d` | 功能相同，main 对照多一处测试类型声明 |
| `01a337f`、`d1af10e` | `7aed122` | 主线在相同 live endpoint 实现上增加初始化幂等/失败 fence；旧版无新增功能 |
| `d42a79b` | `209c2ab` | relocation 文档修订已由主线更新，非缺失执行实现 |
| `80a9f53` | `00d901e` | continuation 代码仅 docstring 不同；功能已在 main |
| `a27e6d5` | `ac3c844` | matching 文件完全一致；physical-binding applicability 修复已在 main |
| `f0e705e` | 4 个旧根目录审计草稿 | 历史文档未纳入当前根目录，非通用整改实现；以现行 V2/ADR/验收为准 |

## 修正后的剩余工作口径

以下是代码核对后的任务类型，不能把某一阶段“仍开放”解释为该阶段完全没有实现。
设备与独立主机事实保持单独记录；缺少设备不妨碍实现通用软件协议，也不允许编造设备。

| 工作包 | 已在 main 的实现/既有证据 | 真正还缺的接线或软件验收 | 部署证据单列 |
| --- | --- | --- | --- |
| F4 Memory/Spatial | import/Unknown 查询、Group use/revoke/handoff、Active Map、tombstone、局部 GC、真实 CAS execution/use-lease 引用、冷恢复引用对账；FPFH/RANSAC/ICP/pose-graph/3D tiles 与平面融合/增量算法均已有 | Replica/Archive/Snapshot/Catalog 等实际引用生产/释放与跨服务对账；typed-map 生命周期、授权撤销、融合/增量结果消费和恢复的完整组合矩阵 | 真实地图来源、物理定位与多主机内容连续性 |
| F5 HA/恢复 | learner/joint-consensus join/remove、transfer、证书轮换/Abort、原 child wait、SQL/CAS 静止恢复 | 在线 Archive/Snapshot/Catalog/CAS 内容一致性；缺原进程 journal 的旧部署显式排空迁移；滚动版本升级/回退与复合故障验收 | 独立故障域切换与真实 RTO/RPO |
| F6 资源/调度 | 确定性搜索、来源化成本输入、未来预约、tenant/category quota Commit/release、POSIX CPU/address-space worker proof | divisible allocation、真实 load/GPU/queue/traffic 成本生产与消费、完整空间/交通承诺及其他本地资源强制 | GPU/交通和目标负载实测 |
| F7 执行协调 | relation whole-Group stop、每个原 attempt 的独立确认、hold/release gate、未来未激活区间抢占 | 运行中抢占的策略/停止/恢复接线、耦合多 Role 迁移、异构 hazard/handoff/regrasp 合同与组合验收 | 实际 safe-state/stop certificate、硬实时和物理交接 |
| F8 运行期计划 | 版本化修订、原目标、自动 MI claim/nonce/发现、原回包对账、Review、新 Group 与非空 v0.9 scoped Memory 文件链路 | 其他 Memory 类型、typed-map、复杂拓扑及 F4/F7 复合故障矩阵 | 真实场景效果与设备连续性 |
| F9 Federation | Control 内持久 prepare/commit/abort session 和恢复校验 | 真正跨控制域 transport、证书/tenant/domain 授权、路由与双域恢复；当前 `apps/` 没有消费 session 的跨域服务入口 | 跨主机域/网络分区演练 |
| F10 运维/总验收 | write-disabled、最后提交查询、显式恢复探针、Prometheus/Alertmanager 与实际短窗口 firing/resolved、部分 worker/连接隔离 | 其余 listener/阻塞 I/O 收敛、版本升级/回退组合、完整 SLO/runbook/告警覆盖与全部非发布验收矩阵 | 长期负载、人工响应、外部通知和跨主机 SLO |

可核对的当前源码入口：
[空间算法](../../integrations/spatial-provider/roboguide_spatial_provider/geometry.py)、
[平面融合](../../integrations/spatial-provider/roboguide_spatial_provider/occupancy.py)、
[内容引用](../../integrations/memory-provider/roboguide_memory_provider/content_references.py)、
[恢复包对账](../../apps/recovery-tool/roboguide_recovery/bundle.py)、
[Federation session](../../core/control/src/federation.rs)、
[未来区间抢占边界](../../core/control/src/calendar.rs)。

既有验收对应
[Group Memory handoff](acceptance/2026-10-19-f4-group-memory-handoff.md)、
[地图文件算法](acceptance/2026-10-15-f4-spatial-files.md)、
[副本证书](acceptance/2026-10-20-replica-certificates.md)、
[资源配额](acceptance/2026-10-21-control-resource-quotas.md)、
[非空 scoped MI](acceptance/2026-10-21-runtime-mi-scoped-memory.md)、
[写入恢复](acceptance/2026-10-20-authority-write-recovery.md)、
[实际监控](acceptance/2026-10-20-observability.md)和
[活动引用恢复](acceptance/2026-10-21-recovery-active-content-reference.md)。

下一实现包仍应接续 F4 的实际引用生产者与完整生命周期，对照现有实现补接线和组合验收，
而不是重写已完成算法、轮换、MI 修订或恢复入口。完整约束以
[全部非发布整改计划](roboguide-software-completion-plan-2026-10-15.md)为准；发布不在范围内。
