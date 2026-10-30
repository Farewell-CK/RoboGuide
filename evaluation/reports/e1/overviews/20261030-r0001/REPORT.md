# E1 四类阶段结果总表

四类均已有逐项计划、逐臂运行、配对引用、代码/模型证据和可复算统计。导航与单层搬运来自较早历史批次，感知和重排来自后续冻结批次；不能将它们称为同一版本的完整评测。

| 类别 | 计划 | 双方结果 | RG 成/败/未知 | EMOS 成/败/未知 | 原可比且真值可用 |
| --- | ---: | ---: | --- | --- | --- |
| 导航 | 99 | 99 | 50 / 0 / 49 | 28 / 71 / 0 | 资格未知 |
| 单层搬运 | 166 | 166 | 119 / 40 / 7 | 19 / 143 / 4 | 156 |
| 感知 | 113 | 109 | 84 / 22 / 3 | 17 / 92 / 0 | 106 |
| 多机器人重排 | 100 | 100 | 30 / 60 / 10 | 18 / 77 / 5 | 85 |

四类计划覆盖合计 478 项。合计只用于覆盖清点，不生成四类混合版本总成功率。

## 每类的来源、版本、模型与证据

### 导航

- 发布修订：`cohorts/e1-batch-99-seed40-continuation-20261007T014435Z/revisions/r0001`；从该目录的 workloads.csv、runs/pairs 分片和 summary.json 可追到原档摘要。
- emos：原始代码计数 `{"4d7c0c0a26eaaf5b599b8165a04c6f6a018f0db5": 4, "d07a74895998bd4a058fa9ff67e95b428cfad763": 95}`；请求模型 `["gpt-6.1-sol"]`，返回模型 `["gpt-6.1-sol"]`。
- emos：归档调用计数 1568（99 项有计数），归档 token 4089987；逐臂 wall time 累计 53474.600s，0 项耗时未知。
- roboguide：原始代码计数 `{"4d7c0c0a26eaaf5b599b8165a04c6f6a018f0db5": 4, "d07a74895998bd4a058fa9ff67e95b428cfad763": 95}`；请求模型 `["gpt-6.1-sol"]`，返回模型 `["gpt-6.1-sol"]`。
- roboguide：归档调用计数 597（99 项有计数），归档 token 3555524；逐臂 wall time 累计 28556.600s，0 项耗时未知。
- 主结果之外另有 retry 0、diagnostic 0 条运行，均不回填主成绩。
### 单层搬运

- 发布修订：`cohorts/roboguide-e1-observer-continuation-20261010T093849Z/revisions/r0001`；从该目录的 workloads.csv、runs/pairs 分片和 summary.json 可追到原档摘要。
- emos：原始代码计数 `{"11cbcf757083b677891c7952357ef2207493ad1d": 154, "179fc50e82d94f001e70afc7ec6ab03eb5bb4abf": 11, "89fe7e0d1d762a84b758da1e7d25d32ca03403a6": 1}`；请求模型 `["gpt-6.1-sol"]`，返回模型 `["gpt-6.1-sol"]`。
- emos：归档调用计数 4658（166 项有计数），归档 token 19342898；逐臂 wall time 累计 266126.275s，0 项耗时未知。
- roboguide：原始代码计数 `{"11cbcf757083b677891c7952357ef2207493ad1d": 154, "179fc50e82d94f001e70afc7ec6ab03eb5bb4abf": 11, "89fe7e0d1d762a84b758da1e7d25d32ca03403a6": 1}`；请求模型 `["gpt-6.1-sol"]`，返回模型 `["gpt-6.1-sol"]`。
- roboguide：归档调用计数 2182（166 项有计数），归档 token 15214206；逐臂 wall time 累计 89221.335s，0 项耗时未知。
- 主结果之外另有 retry 0、diagnostic 0 条运行，均不回填主成绩。
### 感知

- 发布修订：`cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001`；从该目录的 workloads.csv、runs/pairs 分片和 summary.json 可追到原档摘要。
- emos：原始代码计数 `{"3db8816035efa21197a946ee8bcbb4de75983cc1": 52, "88e0da17984364e0286f1665e3d0e827962927e3": 1, "9193f78e8660f4d80da91bb013bd178a03a22ad9": 56}`；请求模型 `["gpt-6.1-sol"]`，返回模型 `["gpt-6.1-sol"]`。
- emos：归档调用计数 2305（109 项有计数），归档 token 6062262；逐臂 wall time 累计 124396.545s，0 项耗时未知。
- roboguide：原始代码计数 `{"3db8816035efa21197a946ee8bcbb4de75983cc1": 109}`；请求模型 `["gpt-6.1-sol"]`，返回模型 `["gpt-6.1-sol"]`。
- roboguide：归档调用计数 2949（109 项有计数），归档 token 370095062；逐臂 wall time 累计 55434.898s，0 项耗时未知。
- 主结果之外另有 retry 3、diagnostic 0 条运行，均不回填主成绩。
### 多机器人重排

- 发布修订：`cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001`；从该目录的 workloads.csv、runs/pairs 分片和 summary.json 可追到原档摘要。
- emos：原始代码计数 `{"3db8816035efa21197a946ee8bcbb4de75983cc1": 10, "88e0da17984364e0286f1665e3d0e827962927e3": 1, "9193f78e8660f4d80da91bb013bd178a03a22ad9": 89}`；请求模型 `["gpt-6.1-sol"]`，返回模型 `["gpt-6.1-sol"]`。
- emos：归档调用计数 3517（100 项有计数），归档 token 12812349；逐臂 wall time 累计 309296.650s，0 项耗时未知。
- roboguide：原始代码计数 `{"3db8816035efa21197a946ee8bcbb4de75983cc1": 100}`；请求模型 `["gpt-6.1-sol"]`，返回模型 `["gpt-6.1-sol"]`。
- roboguide：归档调用计数 1358（100 项有计数），归档 token 13864376；逐臂 wall time 累计 107341.899s，0 项耗时未知。
- 主结果之外另有 retry 10、diagnostic 1 条运行，均不回填主成绩。

## 口径与论文使用

- 导航原报告没有独立配对资格，保留 unknown；各臂完整原计划范围内的官方 true 计数不能因此称为严格配对收益。
- 搬运原生原始结构化结果 `{"false": 143, "true": 19, "unavailable": 4}` 与旧 sidecar `{"false": 143, "true": 21, "unavailable": 2}` 分别保留，没有替换。
- 搬运原逐对可比标志为 160；旧 continuation 汇总排除 pair-0000 且要求 fatal_failure=false，得到 159。按各自原始口径记录。
- 感知计划 113 项，有 1 项 pre-arm failure、3 项未派发，不能记为官方 false。
- 代码、模型和配置属于不同历史条件；成本为已归档调用/usage 与逐臂耗时，不能等同账单或批量并行耗时。
- 可以用来描述已执行的覆盖、原始官方结果、已归档调用/耗时、失败模式和证据缺口。尚不支持当前 main 性能、原论文精确复现或仅组织层单一因果结论。
- 旧感知/重排快照仍在原四类清点中独立保留；本表没有将它们与后续结果相加，也没有按成绩挑选每个 episode 的最好一次。
- 原档仍 local_only，完整物理初态等价、已加载原生模块身份和独立备份缺口未因四类整理完成而消失。
