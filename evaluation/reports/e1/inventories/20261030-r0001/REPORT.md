# E1 四类历史原档清点

四类历史清单共 478 个 workload。下表按来源集合分别计数；历史覆盖与近期复测不能相加当独立样本，不生成跨版本成功率。

| 来源/类别 | 计划 | 已定位 | 原记录可比对 | RG 真/假/不可用 | EMOS 真/假/不可用 |
| --- | ---: | ---: | --- | --- | --- |
| historical-retained/height_man | 166 | 166 | 160 | 119 / 40 / 7 | 19 / 143 / 4 |
| historical-retained/navigation | 99 | 99 | unknown | 50 / 0 / 49 | 28 / 71 / 0 |
| historical-retained/perception | 113 | 58 | 49 | 0 / 50 / 8 | 14 / 43 / 1 |
| historical-retained/rearrangement | 100 | 100 | 92 | 20 / 31 / 49 | 17 / 73 / 10 |
| staged-primary/perception | 113 | 109 | 106 | 84 / 22 / 3 | 17 / 92 / 0 |
| staged-primary/rearrangement | 100 | 100 | 90 | 30 / 60 / 10 | 18 / 77 / 5 |

## 访问缺口与解释

- 历史清点以已保存 locator 找原始 JSON 后重新读取，不仅复制之前汇总。未定位的原档是访问/覆盖缺口，不证明实验未做过。
- 模型声明来自原 launch 或原报告，返回模型身份未在清点阶段逐次复核；已迁移批次的记账另有完整投影。代码未知不使用当前 main 补写。
- 导航旧报告缺独立 Pair Validator 资格，保留 unknown；其他旧 raw eligibility 也不能证明严格组织层等价或完整初态相同。
- height_man 的原始 EMOS 结构化结果与独立复核不同：本表保持原始值，独立复核声明保留于逐行 assessment 字段，未覆盖原值。
- 额外 distance 变体不在四类范围内；小样本诊断、后续失败选择队列均不混入。
- 原档仍为 local_only；本次未建立独立远端备份。
