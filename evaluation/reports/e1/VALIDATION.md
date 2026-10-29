# 首份阶段结果包验证记录

本记录验证离线发布实现和脱敏投影；不增加任何模型、物理或 benchmark 运行。
导出器冻结源码：`ae312bc1ec15bd9b841cd82368961debb6eced96`。
历史运行代码独立保留，不能将这个导出器提交当作运行版本。

| 检查 | 实际结果 |
| --- | --- |
| 全仓 Python，`uv run pytest -q -o addopts=''` | 3553 passed，658.34 秒 |
| Evaluation 全部测试 | 980 passed |
| 发布工具针对性测试 | 34 passed |
| Ruff format / check（evaluation） | 135 文件格式通过，check 通过 |
| strict mypy（evaluation/src、evaluation/tests） | 84 source files，通过 |
| function-doc（evaluation） | 通过 |
| 独立 SHA256SUMS、引用、统计和完整性验证 | 主包与历史清点均 PASS |
| 单独简单计数复核 | 213 个 workload、432 条运行、209 对；三态、调用/token、耗时与配对四格一致 |
| 逐行 JSON Schema 与脱敏/文件边界 | 432 个 run、2,823 个主包 artifact；所有发布文件不超过 1 MiB；未加入原始日志或媒体 |
| 文档构建 | sync、strict MkDocs 通过；909 HTML 页面、46 maintained routes 检查通过 |
| git diff --check | 通过 |

首次全量检查曾在未修改的 Memory 测试
`test_suspended_child_cannot_read_with_a_queued_expired_permit` 的三秒子进程读取断言处失败
（其余 3,544 项通过）。未修改该模块、测试或预算；隔离复跑 1 passed，
随后对最终源代码重新完成上表全量检查。记录这一时序现象，不把隔离通过当作已定位根因。

## 原档不变检查

- `roboguide.e1.result-publication/v0.1`：重新读取并核对 2418 份原始长度/摘要；405 个原始缺失引用仍缺失。
- `roboguide.e1.archive-census/v0.1`：重新读取并核对 1789 份原始长度/摘要；201 个原始缺失引用仍缺失。

这些是本轮实际消费的元数据与记账文件；两份检查存在重叠，不相加作为独立材料数。
可选失败日志不存在不自动等于归档失败，availability 始终逐条保留。
未宣称已穷尽核验全部视频、数据库、轨迹、数据集或实现独立远端备份。
机器绝对路径的本机 witness 与完整测试日志保留在导出工作目录，均未进入公开发布包。

## 在其他机器复算

以下两条只读取已提交投影，不需要凭据、原始实验目录、GPU 或原生 Habitat：

```bash
uv run python -m roboguide_eval.e1_publication validate \
  evaluation/reports/e1/cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001
uv run python -m roboguide_eval.e1_publication validate-census \
  evaluation/reports/e1/inventories/20261030-r0001
```

通过表示投影引用、字节与统计闭合，不是重新证明原始物理真值或严格组织层公平性。
测试也不能证明基线与论文配置完全一致；该限制与缺失原档继续列在
[来源选择和缺口](SOURCE_SELECTION_AND_GAPS.md)。
