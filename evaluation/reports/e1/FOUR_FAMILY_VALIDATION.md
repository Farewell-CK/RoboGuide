# 四类补充发布验证记录

导航与单层搬运现已完成逐项迁移，和已有感知/重排形成四类 478 项的可复算入口。本轮未调用 Provider、仿真或硬件，没有修改原始实验材料、benchmark 或准入规则。

导出器源码：`dd703d8fd5d0713988396d5624e737100b1549f1`。运行版本来自原始逐臂记录，不能用导出器或当前 main 补写。

| 检查 | 结果 |
| --- | --- |
| 全仓 Python | 3562 passed，587.71 秒 |
| Evaluation 全部 | 989 passed |
| 历史导入 / 四类总表 / 既有发布回归 | 43 passed |
| Ruff format / check | 通过 |
| strict mypy | evaluation/src、evaluation/tests，87 source files 通过 |
| function-doc | evaluation 通过 |
| 发布包复算 | 导航、搬运、原 staged、四类总表与历史清点均通过 |
| 额外独立计数 | 未复用发布统计函数，逐行复核三态、调用/token、耗时和配对四格，全部一致 |
| 文档 | sync、strict MkDocs 通过，909 HTML 页面、46 maintained routes 通过 |
| diff-check / 公开文件扫描 | 通过；单文件均小于 1 MiB，无原始日志、视频、数据库或凭据 |

## 完整性与条件保护

- 导航原始计划的 99 项全部保留，原始 4 项与续跑 95 项按代码分组；198 条逐臂记录、99 个配对引用。
- 单层搬运原始 selector 的 166 项全部保留；三个代码条件、历史保留与新续跑分开，332 条逐臂记录、166 个配对引用。
- 既有感知/重排 r0001 与历史清点 r0001 的原字节未改变；重试、诊断及历史原生复用没有回填原失败。
- 四类入口缺任一类别、重复选择同类别、源 manifest 改变或统计不一致均拒绝。
- 新旧历史官方不可用仍是 unavailable；导航缺少独立配对资格保持 unknown，未伪造 comparable。
- 搬运的原始 160 个可比标志，与原 continuation 汇总的 159 个条件通过分别复算；原报告还排除 pair-0000 并检查 fatal_failure=false。
- 搬运旧 sidecar 的 21/143/2 作为原独立复核保留，原始 19/143/4 不被修改。

## 新消费原档的复核

- `e1-batch-99-seed40-continuation-20261007T014435Z`：198 条运行投影通过 Schema；995 份消费来源长度/摘要未变，988 个缺失引用仍显式不可用。
- `roboguide-e1-observer-continuation-20261010T093849Z`：332 条运行投影通过 Schema；1822 份消费来源长度/摘要未变，343 个缺失引用仍显式不可用。

缺失引用含可选失败文件和旧布局未采用的文件名，不代表这些数量的任务未执行。此检查只覆盖消费的元数据/记账，不宣称视频、数据库、全轨迹均已穷尽核验或独立备份。

## 复算命令

```bash
uv run python -m roboguide_eval.e1_publication validate-overview \
  --base evaluation/reports/e1 evaluation/reports/e1/overviews/20261030-r0001
uv run pytest -q evaluation/tests/test_e1_publication.py evaluation/tests/test_e1_history_publication.py
```

发布状态仍为 staging，等待论文使用审阅。四类整理完成不证明原论文配置精确复现、严格初态公平性、当前 main 成绩或组织层单一因果。
