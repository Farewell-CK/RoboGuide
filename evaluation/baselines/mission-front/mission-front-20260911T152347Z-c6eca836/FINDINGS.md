# Mission Front-half Eval — Baseline Evidence Report

- Suite: `mission-front-20260911T152347Z-c6eca836`（本目录）
- Model: `gpt-5.6-luna`（reasoning_effort=xhigh，经中转 101.43.45.215:8080）
- Cases: 29（normal 4 / ambiguity 5 / heterogeneous 4 / integrated 4 / cross_node 3 /
  resource_timing 5 / multi_role 4）
- 结果: **0/29 通过；29/29 终态 = NeedsClarification；0 个 case 到达 Planner**
- 成本: 34 次 LLM 调用，51,766 tokens（全部消耗在 Interpreter 阶段）
- Core baseline: `main@0402234`；未修改 Core / mission 包

## 总判断

**Front half 在第一阶段（Dialogue → Interpreter）即被系统性阻塞。**
Planner、Validator、Catalog 校验、Reviewer、Repair 五个下游阶段在本 baseline 中
完全未被 exercised——不是它们没有问题，而是没有任何输入能穿过澄清关卡。

## F-1（系统性）: Interpreter 对所有指令过度澄清，29/29 被阻断

每个 case 都产生 1–11 个澄清问题，包括指令信息完整、直接可执行的 case：

- `n2-mapping`（"让具备建图能力的机器狗在仓库区域建立地图并发布"——机器人类型、任务、
  区域三要素齐全）仍被问 5 个问题：哪个仓库、发布到哪里、完成判据、选哪只狗、安全限制。
- `n4-edge-inference`（点云推理，要素齐全）被问 5 个问题。
- 全套 case 问题数分布：1–11 个，中位数 5。

根因链（三因素叠加，均有源码证据）：

1. **Schema 强制产出**：interpreter 输出 schema
   `required: ["objective","constraints","assumptions","open_questions"]`
   （`mission/src/mission/responses.py:419`）——模型每轮必须填 `open_questions`；
2. **Prompt 无反问约束**（`mission/prompts/v0/interpreter.md`）：只有
   "missing X would materially change the Task Graph 就问"的单向规则；xhigh 档模型
   可以把任何细节论证成 material（哪个仓库、哪只狗、完成判据、安全区……）。
   **没有**"指令可执行时用 `assumptions` 前进""只问真正阻断性事实""问题数上限"；
3. **Engine 零阈值阻断**（`mission/src/mission/request_engine.py:246`）：
   `if assessment.open_questions:` 任意非空问题列表即停 → 整个管线无法前进。

`GroundedIntent.assumptions` 字段正是为"可见的前进前提"设计的，但 29 个 case 中
模型产出 assumptions 极少且从未以此替代提问——prompt/schema 均未引导。

## F-2（行为缺陷）: 澄清循环不收敛

5 个带 follow-up 的 case（a1–a5）在用户回答后**再次进入澄清**，且问题与第一轮
高度重叠。以 a1 为例（完整对话见 `cases/a1-missing-destination.json`）：

```text
User:  把桌子上的杯子搬走
MI:    哪张桌子？搬到哪？液体/易碎？完成条件？
User:  搬到卧室的床头柜上          ← 已明确回答目的地
MI:    哪张桌子哪些杯子？液体/易碎？完成条件？哪个卧室床头柜？   ← 重复+已答问题再现
```

Interpreter 对每一轮对话**重新完整提问**，没有"已回答的问题视为已解决"的收敛指令；
用户回答"搬到卧室床头柜"后连"搬到哪里"都被换形式重问。

## F-3（语义正确性观察，正面）: 未发现 Local How 泄漏与越权

被阻断前的 assessment 输出未发现 vendor skill / ROS / 坐标 / NodeId 级泄漏；
interpreter 遵守了"不猜 NodeId / 不消费 inventory"的边界（该结论仅覆盖
Interpreter 阶段输出；Planner/Reviewer 未被 exercise）。

## F-4（评测侧待改进，非 Core 问题）

1. Runner 当前只支持按 case 预设的固定轮数 follow-up；需要真正的多轮澄清协议
   （读每轮问题 → 动态应答或判定不收敛）；
2. Case 分类需要校准：部分标为 normal 的 case（如 n1 的"哪个杯子"）在
   严格解读下确有歧义空间——模型行为与 case 分类的分歧本身是数据，但需要
   人工仲裁轮次；
3. 下游 invariant（decomposition / capability / integrated operation /
   timing / reviewer 有效性）在澄清关卡修复前处于 vacuous pass——本 baseline
   对它们**无证据**。

## 修复建议（供 review，未实施）

交 Codex 前建议逐条确认：

| # | 建议 | 涉及 | 对应发现 |
| --- | --- | --- | --- |
| R1 | Interpreter prompt 增加**前进规则**：指令含可执行要素时用 `assumptions` 记录前提并返回空 `open_questions`；仅当缺失事实会实质改变 Task Graph 结构（任务数/角色数/目标对象）才提问；问题 ≤3 且每问必须对应一个阻断性决策 | `mission/prompts/v0/interpreter.md` | F-1 |
| R2 | 明确澄清**收敛语义**：回答轮只允许提出**新**阻断性问题；已答或可从回答推出的事实不得重问 | 同上（+可能 interpreter schema 描述） | F-2 |
| R3 | 评估 schema 是否将 `open_questions` 从 required 降级 / 加 `maxItems`，或 engine 侧区分"阻断性问题"与"可选偏好问题" | `mission/src/mission/responses.py` / `request_engine.py` | F-1 |

## 复现

```bash
set -a; source /data/workspace/code/emos-baseline/emos.env
export ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP=1
uv run roboguide-eval mission-front --out evaluation/results/mission-front
# 单 case: … mission-front --only n1-single-relocate
```

逐 case 完整证据（instruction / dialogue / assessment / lifecycle / 每次调用的
token 与延迟 / 失败原因）在本目录 `cases/*.json` 与 `cases.jsonl`。
