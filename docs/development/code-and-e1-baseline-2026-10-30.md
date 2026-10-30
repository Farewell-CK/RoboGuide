# 代码主线与 E1 进度对账（2026-10-30）

本文是这次代码整合的入口。代码是否包含某项实现、离线检查是否通过、真实运行是否成功
分别记录；历史验收与实验目录保持各自冻结的 SHA，不随分支移动而升级。

## 主线约定

- `main` 保存完成审查和相应门禁的正式代码。
- `dev` 是后续日常维护分支；本次收口时以快进方式同步到 main，不复制一套实现。
- 当前仓库日常入口保持在 `dev`；需要正式基线时读取 main 的完整 SHA。
- 已经合入的专题分支只代表历史开发线，不代表还有一份较新的隐藏代码。
- 测试/实验使用固定提交的独立干净视图。开发时切换分支不会改变那些历史运行代码。
- 原始实验、凭据、未跟踪会议/用户文件不因代码整合入库；旧工作树和 stash 保留。

本次起始 main 是 `0516f930b3aa8f284d3e56900353cdaa5a635119`。
实际交付 SHA 由本次合并/文档提交及 `main`、`dev`、对应 origin refs 确认；文档不写
自身未来提交的虚构 SHA。后续有新开发后，两分支可以再次正常分离。

## 这次实际整合的开发线

| 开发线 | 本次核对与处理 |
| --- | --- |
| `codex/e1-perception-rearrangement-contracts` | 起始与 main 同头；注册/grounding、观测与搬运合同、执行生命周期和性能复用均已在 main |
| `codex/e1-stage-results-publication` 至 `ae312bc1ec15bd9b841cd82368961debb6eced96` | 合入五个离线工具提交，保留原历史；只增加 Evaluation 工具、schema、合成测试和说明 |
| 上述分支的 `2311161236c029bfa133a9282b512a5e949a8d58` | 实验结果发布提交继续保留在原分支，不引入本次主线；不能称整个报告分支已合并 |
| 其余本地命名分支 | 起始已提交历史均在 main；没有另一份待合并的本地生产源码 |
| `dev` | 起始落后 main 260 个提交、没有独有提交；交付时正常快进并核对远端 |

导出工具不属于 MI 或 Control 决策链，不调用模型、仿真、Node，不改变运行、Formal
admission、Pair Validator 或官方 PDDL 判定。文档示例不冒充主线已有实验结果包。

## Git 存量核查范围

本次 fetch 后检查 26 个本地分支、69 个远端分支和 13 条工作树记录：
11 个目录存在，另外两个是已不存在的旧反例工作树记录。存在的其他工作树没有
未提交源码；主目录的未跟踪实验和用户目录保留。检查是本机 origin 的时点快照，
不覆盖其他人的克隆或未配置 remote。

旧远端分支不能一律作为待合并代码：

| 类别 | 处理与依据 |
| --- | --- |
| `ep51-repeatable-navigation`、`ep51-rgb-evidence`、`stage2-execution-contract`、`b1-event-archival` | `git cherry` 确认补丁已吸收；非祖先不等于丢失了功能，不重复回灌 |
| 旧 `stage2-contract-guard` | 保留早期观测实现和报告；现行 Guard 已重构，不能称逐字节等价，也不能用旧 hook 覆盖当前生命周期 |
| Episode51 和 Provider 预检报告分支 | 保留原实验版本与结果边界，不导入本次主线 |
| E2 dataset correction / live-status 工具 | 独立旧实验辅助工具，登记保留；不冒充当前 E1 或通用 OS 整改 |
| H-CoRE、Android/NPU mobile-navigation | 独立原型/Local System，有独有源码；未验收当前部署，不强行混入本次 E1 整合 |
| 五个无共同历史的分支 | 保留，不使用 `--allow-unrelated-histories` 拼接不同产品 |
| `stash@{0}` 的旧 Actor-binding lint 工作 | 只有四个文件的 import 排序改动，不是隐藏功能；不 apply/pop/drop |

完整旧分支用途还可参考[2026-10-22 存量审计](roboguide-branch-audit-2026-10-22.md)。
其旧 SHA、工作树修改和旧实验状态仅代表当时，不替代本次重新核对。

## 当前代码能力与证据状态

| 层次 | 已在统一代码中的能力 | 仍须独立验证的内容 |
| --- | --- | --- |
| 注册、部署与 MI 输入 | 显式能力/资源/执行拓扑、episode-start planning evidence、冻结语义来源 | 真实部署是否完整、当前几何是否支持实际任务，不能从声明推导物理成功 |
| MI | 统一 Planner/Reviewer/Repairer、协作语义指导、原请求观察与有界恢复 | 模型是否忠实遵循目标；结构通过不等于任务语义或物理执行正确 |
| Control / Node | 既有 Matching/Scheduling/Commit、资源承诺、端点复用与失败收尾 | 实际资源/实体、停止和重绑定证据；本次工具整合不改变其 authority |
| Local EAIOS | 调用目标 Guard、搬运/观测条件反馈、独立执行生命周期、有界上下文 | 导航/抓取/放置/观测真实收敛，不以工具 Success 或本地 Completed 替代官方成功 |
| 执行生命周期 | ADR-0226 新搬运导航、ADR-0228 自身 reset 参考、Task inbox 隔离 | 软件反例关闭不证明所有历史物理失败已解决 |
| 性能 | ADR-0229 同一次原始谓词读取内的有界原始画面复用，默认关闭、两臂显式同配 | 短探针等价和缓存命中不保证全程吞吐；剩余耗时继续分解 |
| 归档与 Evaluation | 完整事件分页、原始失败/官方结果分离、配对身份及原始初态比较 | 每条运行实际生产的模块清单必须覆盖校验配置；缺少时保守拒绝 |
| 结果维护 | 本次新增封存 staged 格式离线导入、清点、重算和脱敏检查 | 原始备份、人工论文审阅、历史格式迁移和新的物理验证不能由导出器替代 |

相关实现说明：
[ADR-0226](../decisions/0226-fresh-relocation-navigation.md)、
[ADR-0227](../decisions/0227-explicit-emos-target-identity-compatibility.md)、
[ADR-0228](../decisions/0228-controlled-execution-lifecycle.md)、
[ADR-0229](../decisions/0229-scoped-original-observation-read-cache.md)和
[Evaluation 离线工具](../../evaluation/docs/e1-publication-tools.md)。

## 当前 E1 未关闭项与顺序

整合后的离线更新：[ADR-0230](../decisions/0230-runtime-source-coverage-preflight.md) 已实现
第 1 项的共享清单和启动前覆盖检查。它仍需新版本实际进程/归档的独立验收，不能补造
上一轮缺失的加载证据。后续源码、测试和修订提交单独记录；本页最初的整合 SHA 不变。

1. 修复运行模块证据清单与配对校验配置的覆盖缺口，并在启动真实运行前进行离线检查。
   已核对的问题是部分必需模块没有进入原生采集清单，不能冒充实际来源已验证；
   私有文件摘要相同不能补造历史进程加载证据。
2. 分解长运行剩余时间。原始重复渲染已有明确修复，剩余墙钟时间不能全部归为 Provider，
   也不能凭短探针宣布完整性能问题关闭。
3. 继续读感知和重排失败轨迹，从最早偏离处分清目标理解、物理可达、局部技能、
   传感器条件、模型动作与接入合同问题；不为成功而修改目标、阈值或追试选最好结果。
4. 以上软件问题完成审查和门禁后再冻结新版本；夜间队列另保存清单、配置、模型、
   预算与原始结果，不热改旧运行，白天不启动新的真实 Provider/Habitat/批量任务。

原整合本身不顺带实现上述修复，后续离线更新也不启动实验。代码已合并、软件测试通过和 E1 已完成
是不同结论。旧完整实验、重试、失败选择诊断和未派发样本不能拼成统一成功率。

## 交付检查

- 先对组合代码执行 Evaluation/适配器针对性测试，再执行全量 Python。
- Ruff format/check、两个 strict mypy scope、function-doc、实际 Habitat Python 3.9
  零 Provider runtime gate、严格文档构建和 `git diff --check` 分别保存实际结果。
- 本次没有 Rust、MI Prompt、Core/Node、PDDL 或真实实验配置变更，不冒称重跑了这些
  完整物理/生产验收或整套 Rust 门禁。
- 推送并核对 main/dev 的完整远端 SHA；核对主目录未跟踪文件和旧 stash 仍保留。
- 本机逐 ref/工作树快照及实际检查日志保存独立审计目录，不加入实验结果发布分支。
