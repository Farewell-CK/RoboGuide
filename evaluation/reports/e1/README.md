# E1 四类结果台账

四类结果已整理为可离线复算的阶段发布包，覆盖全部 478 项历史计划。发布状态为 **staging，等待论文使用审阅**；整理完成不等于所有实验已满足正式论文比较条件。

完整入口：[四类阶段结果总表](overviews/20261030-r0001/REPORT.md)、[四类 CSV](overviews/20261030-r0001/coverage.csv)、[四类机器统计](overviews/20261030-r0001/summary.json)。

| 类别 | 计划 / 双方结果记录 | RG 成功 / 失败 / 不可用 | EMOS 成功 / 失败 / 不可用 |
| --- | ---: | --- | --- |
| 导航 | 99 / 99 | 50 / 0 / 49 | 28 / 71 / 0 |
| 单层搬运 | 166 / 166 | 119 / 40 / 7 | 19 / 143 / 4 |
| 感知 | 113 / 109 | 84 / 22 / 3 | 17 / 92 / 0 |
| 多机器人重排 | 100 / 100 | 30 / 60 / 10 | 18 / 77 / 5 |

每行来自明确的历史集合，全部保留失败与未知。不同代码、续跑、重试和诊断不拼成一个同版本总成功率。

## 逐类完整材料

- 导航：[结果报告](cohorts/e1-batch-99-seed40-continuation-20261007T014435Z/revisions/r0001/REPORT.md)、[逐项计划](cohorts/e1-batch-99-seed40-continuation-20261007T014435Z/revisions/r0001/workloads.csv)、[运行、配对和证据索引](cohorts/e1-batch-99-seed40-continuation-20261007T014435Z/revisions/r0001/manifest.json)。
- 单层搬运：[结果报告](cohorts/roboguide-e1-observer-continuation-20261010T093849Z/revisions/r0001/REPORT.md)、[逐项计划](cohorts/roboguide-e1-observer-continuation-20261010T093849Z/revisions/r0001/workloads.csv)、[运行、配对和证据索引](cohorts/roboguide-e1-observer-continuation-20261010T093849Z/revisions/r0001/manifest.json)。
- 感知：[结果报告](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/REPORT.md)、[逐项计划](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/workloads.csv)、[运行、配对和证据索引](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/manifest.json)。
- 多机器人重排：[结果报告](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/REPORT.md)、[逐项计划](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/workloads.csv)、[运行、配对和证据索引](cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001/manifest.json)。

- [更早四类原档清点](inventories/20261030-r0001/REPORT.md)：保留旧感知 58 项与旧重排版本，不由新结果覆盖。
- [来源选择、论文限制和证据缺口](SOURCE_SELECTION_AND_GAPS.md)。
- [首次验证记录](VALIDATION.md)、[四类补充验证记录](FOUR_FAMILY_VALIDATION.md)。
- [离线工具和复算命令](../../docs/e1-publication-tools.md)、[发布规范](../../docs/e1-results-publication-v0.1.md)、[机器索引](index.json)。

## 口径说明

- 导航由原始 4 项和续跑 95 项组成；搬运由三个原始代码条件组成。各条件分别保留完整 SHA，不能把导出器或当前 main 写成历史运行版本。
- 搬运 EMOS 原始结构化值为 19/143/4；旧独立终态复核为 21/143/2，保存在历史 assessment 中，不替换主表原始值。
- 搬运原逐对可比标志 160 与旧 continuation 汇总 159 的差异已定位：后者还排除 pair-0000，并要求原 fatal_failure=false。两个口径及各自四格表分别保留。
- 导航独立配对资格未知；双方都有运行记录不自动变成严格 comparable。其他旧 scalar-only reset 也不证明完整初态等价。
- 感知和重排 staged 修订原样保留：147 个 EMOS 历史复用、62 个本批执行、13 次独立重试和 1 次工程诊断；重试未回填主成绩。
- 原始材料仍 local_only。发布包可独立重算统计，尚不等于原档、视频和全轨迹已有独立备份。
- 当前数据支持各冻结条件下的描述性结果与已记录配对子集；不支持统一版本四类总成功率、当前 main 成绩、原论文精确复现或组织层单一因果优势。

## English overview

All four E1 families now have portable workload, run, pair, cost and evidence records, covering 478 planned workloads. Historical conditions and continuation code versions remain explicit. The two-arm staged results, retries, diagnostics, reused native runs and older snapshots remain distinct. This staging publication supports descriptive historical results, not a mixed-version overall score, strict organization-only causality, exact paper reproduction or independent raw-evidence backup.
