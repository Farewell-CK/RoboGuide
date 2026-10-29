# E1 结果台账

这是 RoboGuide E1 经审阅结果的固定维护入口。当前状态：**设计已记录；离线导出器、
历史迁移和首个结果发布包尚未完成。** 本目录当前不包含真实实验成绩。

## 维护约定

- 设计与字段要求：[结果发布与维护 v0.1](../../docs/e1-results-publication-v0.1.md)。
- 原始输入、模型调用、视频、轨迹和运行数据库留在独立归档；仓库发布脱敏投影、
  逐次运行记录、统计、分析报告和实际证据引用。
- 每个 cohort 保留实际代码、模型、数据集、选择方法和预算；primary、diagnostic、
  retry 及历史导入分别标识，复制/复用原生运行不会增加实际执行次数。
- 每个发布修订冻结保存，统计修正新增修订；不覆盖原始失败，不挑最好一次替换主结果。
- 实际 benchmark true、false、unavailable、未执行和执行状态 unknown 分别记录。
  Mission、局部技能、provenance、admission 与配对可比性保持各自含义。
- 初次真实发布完成后才生成 `index.json` 和实际 cohort 目录，当前不创建空成绩文件。

## 首次迁移范围

| 范围 | 应保留的区别 | 当前发布状态 |
| --- | --- | --- |
| 历史四类任务 | 原始清单、不同代码/配置、证据缺口和附加变体 | 待只读导入与复核 |
| 最近感知/重排完整批次 | primary、未运行条目、重试、历史 EMOS arm 复用 | 待只读导入与复核 |
| 固定小样本工程诊断 | 选择理由、成功控制、单臂与真实配对，不生成总体成功率 | 待只读导入与复核 |

具体历史数字须从封存来源重算后发布。本入口不手工维护第二份统计，不把历史
覆盖数量当作统一代码/模型下的总实验成绩。

## 权威与后续工作

现有 [Fairness Foundation](../../docs/e1-fairness-foundation.md) 和
[B1 evidence/admission](../../docs/evaluation-evidence-validity.md) 继续拥有原判定。
发布层只消费既有结果，不修改协议、runner、Prompt、官方目标或任务初始状态。

接下来按设计实现离线导出/校验、核对完整来源和独立备份，再审阅首个阶段发布包。
只存在本机的原始证据标记 `local_only`，不能因为索引入库就声称原档已远端备份。
通用缺陷和修复状态维护在
[E1 问题登记](../../../docs/development/e1-baseline-and-execution-issue-register.md)。
新实验和批量执行不属于发布操作；批量前的用户审阅门禁继续保留。

## English overview

This is the maintained entry point for reviewed E1 result publications. The design is documented;
the offline exporter, historical import and first real publication are pending. No experimental
scores are published here yet. See the [v0.1 design](../../docs/e1-results-publication-v0.1.md).

Publications retain exact execution identities, selection policies, source evidence and immutable
revisions. Primary runs, retries, diagnostics and reused native runs remain distinct. Raw evidence
is archived separately; a repository index does not prove an independent backup. Existing
admission, pairing and benchmark authorities retain their meaning. Publication never launches
experiments or changes historical outcomes.
