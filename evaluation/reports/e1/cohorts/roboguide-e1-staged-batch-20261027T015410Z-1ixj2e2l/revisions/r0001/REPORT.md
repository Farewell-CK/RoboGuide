# E1 首次阶段结果包

本包是历史冻结条件下的阶段描述，完整保留计划缺口、原始失败与单独重试。
来源选择依据是完整清单、封存状态、逐臂原档、配对和模型记账的可访问性，不按成绩选择。
本次只读导出，未启动 Provider、仿真或新 Mission；原始证据的独立备份尚未落实。

## 主实验覆盖与结果

| 类别 | 计划 | 已记录 RG | RG 真/假/不可用 | EMOS 真/假/不可用 | 可用可比对 |
| --- | ---: | ---: | --- | --- | ---: |
| perception | 113 | 109 | 84 / 22 / 3 | 17 / 92 / 0 | 106 |
| rearrangement | 100 | 100 | 30 / 60 / 10 | 18 / 77 / 5 | 85 |

计数为官方 pddl_success；Mission Completed 不替代官方 true。未执行/执行状态未知不填成 false。
原配对资格与官方可用子集分别保留；旧 reset 为角度标量比较，不升级成完整姿态证明。

## 配对结果与重试

- perception：原记录可比 106 对；双方官方可用 106 对，四格计数 {"both_true": 16, "rg_only": 68, "emos_only": 0, "both_false": 22}。
- perception / emos 重试：0 次，{"true": 0, "false": 0, "unavailable": 0}，不替换主结果。
- perception / roboguide 重试：3 次，{"true": 3, "false": 0, "unavailable": 0}，不替换主结果。
- rearrangement：原记录可比 90 对；双方官方可用 85 对，四格计数 {"both_true": 13, "rg_only": 16, "emos_only": 2, "both_false": 54}。
- rearrangement / emos 重试：1 次，{"true": 0, "false": 0, "unavailable": 1}，不替换主结果。
- rearrangement / roboguide 重试：9 次，{"true": 4, "false": 3, "unavailable": 2}，不替换主结果。

## 调用、耗时与失败

所有数字由 runs 记录重算；完整分布和缺失数量见 summary.json。
耗时为逐臂 wall time 累加，含历史复用，不是本批并行耗时；token 是已归档 usage，缺失不能按零解释。

- perception/emos：调用 2305（109 次运行有计数）；归档 token 6062262；逐臂耗时累计 124396.545s；新鲜/复用 52/57。
  原始失败标签（可重叠）：{}。
- perception/roboguide：调用 2949（109 次运行有计数）；归档 token 370095062；逐臂耗时累计 55434.898s；新鲜/复用 109/0。
  原始失败标签（可重叠）：{"SUT_SYSTEM": 27, "SYSTEM_FAILURE": 27, "original-skill-bad-terminate": 2, "provider_http_502": 4}。
- rearrangement/emos：调用 3517（100 次运行有计数）；归档 token 12812349；逐臂耗时累计 309296.650s；新鲜/复用 10/90。
  原始失败标签（可重叠）：{"provider_http_502": 2}。
- rearrangement/roboguide：调用 1358（100 次运行有计数）；归档 token 13864376；逐臂耗时累计 107341.899s；新鲜/复用 100/0。
  原始失败标签（可重叠）：{"MODEL": 1, "MODEL_FAILURE": 1, "SUT_SYSTEM": 69, "SYSTEM_FAILURE": 69, "original-skill-bad-terminate": 48, "provider_http_502": 9}。

## 数据支持的结论

- 可以描述这一冻结条件和完整计划范围内的覆盖、官方成败、调用成本及原记录配对子集差异。
- 原始 SUT/模型失败、官方 unavailable 和未完成计划均可见；重试没有回填主成绩。
- 不能据此证明只改变任务组织导致性能差异，也不等同于原论文精确复现。

## 限制、假设与待补实验

- 本批是在兼容修复和后续 Local How 改动之前运行。公开 HSSD 目标身份覆盖可能影响原生分工；不能把其低分直接归因于组织架构。
- 模型与论文、原生历史复用、Local How/Guard/feedback 差异和旧 scalar-only 初态证据须披露。
- 索引摘要可在任意机器重算；原档仍为 local_only，缺少独立备份时不能宣称完整原始结果远端可复现。
- 抓取、复位或检测等失败标签是观察，不自动证明根因；未定位原因保留 unknown。
- 待用户审阅后再确定新冻结版本、完整队列和对照配置，不因本次分析自动重跑或启动批量。
