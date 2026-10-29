# E1 结果台账

首份可复算阶段结果包已导入，当前为 **staging：等待人工论文使用审阅**。这不是 E1 最终完成声明，也未启动新实验。

## 从这里阅读

- [四类历史原档清点](inventories/20261030-r0001/REPORT.md)：478 个历史计划 workload，按来源版本分开。
- [首次迁移的冻结批次](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/REPORT.md)：完整 213 个计划条目及所有原始缺口。
- [覆盖与结果 CSV](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/coverage.csv)、[可重算统计](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/summary.json)、[运行/配对/原档分片索引](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/manifest.json)。
- [首批选择及证据缺口](SOURCE_SELECTION_AND_GAPS.md)、[机器索引](index.json)。
- [验证记录](VALIDATION.md)。
- [离线工具与复算命令](../../docs/e1-publication-tools.md)、[发布规范](../../docs/e1-results-publication-v0.1.md)。

## 首次迁移结果

| 类别 | 计划 workload | 已有双方结果 | RG 成功/失败/不可用 | EMOS 成功/失败/不可用 | 原记录可比且双方官方可用 |
| --- | ---: | ---: | --- | --- | ---: |
| perception | 113 | 109 | 84 / 22 / 3 | 17 / 92 / 0 | 106 |
| rearrangement | 100 | 100 | 30 / 60 / 10 | 18 / 77 / 5 | 85 |

清单缺口、重试和工程 smoke 没有填进上述主成绩。逐条运行、配对、调用量、耗时和失败标签都可离线复算；不以最好一次替代原失败。

## 身份和使用边界

- RG 本批冻结于 `3db8816035efa21197a946ee8bcbb4de75983cc1`，并非当前 main。
- 147 个 EMOS arm 复用历史，62 个是本批新鲜执行；13 次重试与 1 次 RG 工程 smoke 独立保留。
- runs 的 `code_sha` 是原 launch 记录的启动器/封装版本，EMOS 行不因此证明原生 vendor Git 提交。冻结原生源码表面以 Population Manifest 的 Stage2 identity 摘要引用；原生仓库完整身份与论文依赖仍有证据缺口。
- 主批次、旧版本历史、失败选择复测和诊断分别登记，不生成混合版本 E1 总成功率。
- `pair_comparable` 与旧 scalar-only reset 是原记录的条件判断，不证明严格仅组织层变化或完整物理初态等价。
- 可用于论文的阶段描述：明确冻结条件、完整清单和局部可比子集后的结果及缺口；不能声称原论文精确复现、新版 RG 成绩或组织层单因素因果。
- 原始材料保持本机原位，当前 `local_only`。本包保存消费元数据与记账的原始摘要，并不表示视频、数据库、所有轨迹均已核验或远端备份。

## 维护状态

已有离线 staged 导出器、原档清点与发布包校验；其他格式先保留来源和缺口，按批迁移。新运行新增 cohort，统计修正新增 revision。
通用缺陷继续维护在 [E1 问题登记](../../../docs/development/e1-baseline-and-execution-issue-register.md)，不另造一套 issue 台账。

## English overview

The first recomputable phase package is available as a staging publication, pending human review for paper use. It preserves all planned workloads, unavailable outcomes, separate retries and reused native runs. The four-family census and selected frozen cohort remain distinct; no mixed-version overall rate is produced. Raw evidence is local only. Offline validation proves projection integrity and reproducible arithmetic, not an independent backup, full physical equivalence, exact paper reproduction or organization-only causality.
