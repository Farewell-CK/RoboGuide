# Clarification Policy Canary Findings — ADR-0038 验证（main@39bade8）

- Model: `gpt-5.6-luna`（reasoning_effort=xhigh，经中转）；Prompt / mission
  production code / canary fixtures 运行期冻结
- **口径声明：本轮共两次运行，统计只取 Run B（最终 rerun）。**
  Run A 因 evaluation harness 缺陷（`StageTimedPort` 未转发关键字参数）在
  12 个情景的 Planner 入口崩溃，属 harness 事故；其 Interpreter 阶段观察
  与 Run B 方向一致，但不计入任何最终统计。Run A 的问题样本（如 g1 的
  签收判据、g3 的待命持续时长）**不属于**本轮 rerun 数据。

## Run B（最终 rerun）实际数据

- 14 情景完整执行（g1/g2/g5/g6/g7 五组 × 双臂）
- **12 个 Planner attempts**（全部 HTTP 400，见 R-400）
- **2 个 NeedsClarification**：g2-conflict-grounded、g7-gap-only-free
- **g1-grounded / g3-stale-grounded 等其余 10 个情景：0 澄清问题，直接
  前进至 Planner**（g1、g3 明确无过问）

### 两个澄清情景的问题与 ADR-0038 分类

| 情景 | 问题 | 分类判定 |
| --- | --- | --- |
| g2-conflict-grounded | 请确认目标会议室：3 层西侧还是 2 层东侧？（两条 Fresh 证据冲突） | **Blocking ✓**（证据冲突下目标实质不同） |
| g7-gap-only-free | 请提供三个检查点的名称/位置/标识（身份完全缺失） | **Blocking ✓**（scope 实质不同）；同轮记录假设"巡逻按通常含义理解为到访三个检查点" |

其余 10 个情景 Interpreter 均以空 `open_questions` 前进（Defaultable 事实
按政策记入 `assumptions` 或留空），符合 ADR-0038 的 blocking-only 定义。

## UA（Under-Ask 待复核清单）— 不作为政策正面证据

以下三个情景在**证据/指令存在实质缺口**的情况下未提问即前进。它们不是
政策生效的正面样例，而是需要人工复核的 under-ask 候选：

| 情景 | 缺口 | 复核点 |
| --- | --- | --- |
| g6-fresh-vs-stale-grounded | 同一卸货区存在 Fresh 与 Stale 两条冲突证据，且 State fusion policy 未定义 | 未询问即前进——fusion 未定义时默认采信 Fresh 是否可接受，待项目负责人裁定 |
| g7-gap-only-grounded | 三个检查点身份完全缺失（同指令的 context-free 臂提出了 Blocking 问题——同指令跨臂行为不一致） | 缺失的 scope 事实是否实质改变 Task Graph，待裁定 |
| a1-plain（plain suite） | 指令"把桌子上的杯子搬走"缺少目的地 | destination 是否属 Blocking（ materially different target），待裁定 |

（对照：a2 缺目标物体，Interpreter 正确澄清一轮、回答后前进 ✓。）

## R-400（仍为 Front-half production blocker）

到达 Planner 的 **12/12 次调用全部 HTTP 400**：

```text
invalid_json_schema: Invalid schema for response_format 'mission_plan_v0':
In context=('properties', 'execution_intent'), 'required' is required to be
supplied and to be an array including every key in properties. Extra required
key 'parameters' supplied.
```

指向 `contracts/mission/v0.7` 中 `execution_intent.parameters` 的
`"type": [...]` 数组 + `additionalProperties` 对象形式与中转 strict
structured-output 不兼容，叠加 `mission/responses.py::_provider_schema`
归一化不足。**属 mission/contracts 侧 → 交 Codex**；修复前 Reviewer /
Repairer 持续不可达（本轮 rev=0 / rep=0）。

## 证据位置

- Run B（统计口径）：`grounded-rerun/`（10 情景）与 `plain-rerun/`（4 case）
- Run A（harness 事故，仅参考）：不随本证据归档；运行日志见
  `evaluation/results/clarify-policy/grounded/`（Git-ignored 工作区）
