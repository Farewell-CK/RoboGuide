# Clarification Policy Canary Findings — ADR-0038 验证（main@39bade8）

- 运行: 2026-09-14，两套 suite（grounded 10 情景 + plain 4 case），一次性串行
- Model: `gpt-5.6-luna` @ xhigh（配置未变）
- 冻结: Prompt / mission production code / canary fixtures / Core（本轮零修改）
- 结果口径: observe-only（clarify 不判失败）

## 核心结论

**CP-1（主要正面）: ADR-0038 blocking-only 政策生效。**
上一轮 29/29 与 14/14 全停在澄清；本轮 grounded 臂 12 情景中 **11 个以空
`open_questions` 前进至 Planner**（Defaultable 事实记入 `assumptions`），
仅在 ADR-0038 定义的 Blocking 场景提问。plain 侧 n2（此前 5 问）与 h2
同样 **0 澄清直达 Planner**。

**CP-2（新生产缺陷，阻塞 Front-half 全链）: Planner 结构化输出被中转拒绝。**
到达 Planner 的 16 个 case 全部在 Planner 调用处 HTTP 400：

```
invalid_json_schema: Invalid schema for response_format 'mission_plan_v0':
In context=('properties', 'execution_intent'), 'required' is required to be
supplied and to be an array including every key in properties. Extra required
key 'parameters' supplied.
```

根因指向 `contracts/mission/v0.7/mission-plan.schema.json` 的
`execution_intent.parameters`（`"type": ["boolean","integer","number","string"]`
数组 + `additionalProperties` 对象形式）与中转 strict structured-output 的
兼容性，叠加 `mission/responses.py::_provider_schema` 的归一化不足。
**属 mission/contracts 侧问题 → 交 Codex**；修复前 Reviewer/Repairer 无法被
真实 exercise（本轮 rev=0/rep=0）。

**CP-3（正面）: Gap ≠ 用户问题的语义落地。**
g7 grounded（双源 gap、零证据）Interpreter 直接以假设前进（"巡逻按通常含义
理解为到访三个检查点"），未把 gap 转嫁给用户 ✓；gap-only-free 臂问了检查点
身份（属 Blocking 边界，见下分类表）。

## 逐问题 ADR-0038 分类审计（仅澄清发生的 3 个情景）

| 情景 | 问题 | 建议分类 | 模型归类 | 判定 |
| --- | --- | --- | --- | --- |
| g2-grounded | 请确认目标会议室：3F 西还是 2F 东？（两条 Fresh 证据冲突） | **Blocking**（冲突证据下目标实质不同） | Blocking | ✓ 正确 |
| g2-grounded | "拿过来"的目的地是您当前位置吗？ | Blocking（定义交付效果）——边界可辩 | Blocking | ✓ 可辩护 |
| g7-free | 三个检查点分别是什么？（身份完全缺失） | **Blocking**（scope 实质不同） | Blocking | ✓ 正确 |
| g7-free | 巡逻完成标准：到访即可还是需检查/拍照？ | 多数解读下 **Defaultable**（常规巡逻解读保持核心目标） | Blocking | ⚠️ 过问候选 |
| g7-free | "今天上午"指执行时间还是检查点指定时间？ | **Blocking**（修饰对象歧义改变 scope） | Blocking | ✓ 正确（质量好） |
| g1-grounded | 到达前台即可，还是需签收/交接？ | 多数解读下 **Defaultable**（到达即完成常规解读） | Blocking | ⚠️ 过问候选 |
| g3-free/grounded | 待命是停靠还是接入充电？ | **Blocking**（物理动作实质不同） | Blocking | ✓ 正确 |
| g3-free/grounded | 待命持续到何时？ | 多数解读下 **Defaultable**（"直到后续指令"为常规默认） | Blocking | ⚠️ 过问候选 |

**分类审计小结**：
- **Control-owned / Local-EAIOS-owned 违例：0 例**（全程无 provider 选择、
  ROS、vendor skill 类问题泄漏给用户——ADR-0038 边界生效）✓
- Blocking 判定：7 个正确（含 2 个高质量歧义捕捉）✓
- 过问候选：3 个（均为"完成判据/持续时长"类——可用 Defaultable 常规解读
  + assumptions 前进），属 prompt 微调空间，非结构性缺陷
- 欠问候选：1 个（a1 首轮未问目的地即前进——destination 是否 Blocking 存在
  解释空间，样本量 1，记录待更多样本）
- Grounding 效应复现：g1/g6 grounded 臂问题数 2→1、3→1，目的地被证据消解 ✓

## 运行事故记录（Harness bug，已修复后重跑）

首轮 grounded 运行 12 个 case 在 Planner 入口崩溃：`StageTimedPort` 代理未
转发 **keyword 参数**（grounding 变更后 engine 以
`plan(mission_id=..., ...)` 关键字形式调用端口）。属 evaluation harness
缺陷（非模型/配置/production 问题），修复为 args+kwargs 全转发后重跑。
该事故不影响 Interpreter 阶段证据（首轮已完整捕获）。

## 数据

| suite | 情景 | interp | plan | rev | rep | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| grounded-rerun（10） | g1/g2/g5/g6/g7 双臂 | 14 | 12 | 0 | 0 | 2 NeedsClarification + 12 Planner-400 Failed |
| plain-rerun（4） | n2/h2/a1/a2 | 5 | 4 | 0 | 0 | 4 Planner-400 Failed |
| tokens | plain: in 6,488 / out 2,375；grounded-rerun 见 cases/*.json |

（planner 400 意味着 planner 调用发生过但被上游拒绝——plan=12 表示调用计数。）

## 证据位置

- grounded：`cases/*.json`（10）+ summary.json
- plain：`cases/*.json`（4）+ summary.json
- 每份含 instruction / 完整 dialogue / assessment（open_questions+assumptions+
  constraints）/ lifecycle / issues / stage timings / 逐调用 token
