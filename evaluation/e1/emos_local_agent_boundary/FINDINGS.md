# EMOS Local-Agent Boundary Alignment Audit — FINDINGS

- Baseline: RoboGuide `a2089ca`（Phase A 之后），EMOS checkout `/data/workspace/code/emos-baseline`
- 方法：源码 tracing（TRACE.md）+ 隔离 feasibility probe（`probe/`，2 次 LLM 调用，
  2398 tokens）。不改 RoboGuide Core，不改 EMOS baseline，不跑 benchmark。
- 证据等级：全部结论有 file:line 级源码证据；B3 另有可执行 probe 实证。

## 1. EMOS 真实 call graph

见 [TRACE.md](TRACE.md)。一句话版：

```
task → HabitatMASEvaluator 循环 → MultiLLMPolicy.act
     ├ Stage1: group_discussion（Leader OpenAIModel 分解+分配，robot reflection ≤3 轮）
     └ Stage2: CrabAgent.init_agent(subtask, chat_history)
              → 每 tick CrabAgent.chat(scene_description) → skill 选择
              → HierarchicalPolicy → defined_skills（nav_to_obj→OracleNavPolicy）
              → OracleNavAction(navmesh follower) → env.step()
```

## 2. Leader / CrabAgent / Skill / Habitat 边界（B2）

| 问题 | 答案 |
| --- | --- |
| Leader 做什么 | 一次性 task→subtask 分解与跨机器人分配（`{robot_id\|\|subtask}`），吸收 robot reflection 重分配 ≤3 轮。本质是一个 leader-prompt 的 `OpenAIModel`（multi_llm_policy.py L289），**不是类** |
| CrabAgent 做什么 | 单机器人本地执行策略：subtask→动作序列规划（init_agent）+ 逐 tick 动作选择（chat）+ `send_request` 跨机器人求助（类级 `message_pipe`）+ 自动标注 `robot=self.name` |
| 跨机器人 task assignment | Leader（Stage 1） |
| 单机器人 local planning | CrabAgent（Stage 2） |
| 调 LLM/VLM 的层 | Leader、discussion robots、CrabAgent 三者都经 `OpenAIModel`；skill 层无 LLM |
| 直接调 Oracle Skill 的层 | `HierarchicalPolicy` + `defined_skills`（在 CrabAgent **之下**）；CrabAgent 只产出 skill 名+参数 |
| episode-local observation/history | CrabAgent 的 `chat_history`（含 Leader 讨论历史注入）+ `message_pipe` + `scene_description`（env task context） |

**关键区分**：EMOS 的 "global organization" = Stage 1（Leader）；"local embodied
execution" = Stage 2（CrabAgent + skills）。RoboGuide E1 要替换的是前者。

## 3. CrabAgent 能否脱离 Leader 接受已分配 subtask？（B3）

**能，且有函数级入口 + 实证。**

- 入口：`CrabAgent.init_agent(robot_type, task_description, subtask_description,
  chat_history=None, enable_logging=…, logging_file=…)`（crab_agent.py L47）。
  system prompt 即 "You MUST take finish subtask assigned to you"；`subtask_description`
  非空时走"已分配 subtask→完整动作序列"分支；`chat_history=None` 合法（跳过讨论历史注入）。
- **Probe 实证**（`probe/probe_crab_standalone.py`、`probe/probe-output.txt`）：
  无 Leader、无 Habitat env、无讨论历史，仅固定 subtask `"Navigate to any_targets|0."`
  + 合成 scene description：
  - `init_agent` 成功（注：crab_planning 自规划轮返回 None，EMOS 生产代码对此容忍，
    后续逐步 chat 才是决策入口）；
  - `chat()` 返回结构化决策 `{"name": "nav_to_obj", "arguments": {"target_obj":
    "any_targets|0", "robot": "agent_0"}}` —— 与 C1-S0 同一实体引用、同一动作。
- **边界与限制**（同样来自代码）：
  1. CrabAgent 仍需外层逐 tick 驱动（`LLMHighLevelPolicy.get_next_skill` 的角色：
     喂 scene_description、把 skill 名映射到可执行 skill、处理 wait/无效输出）；
  2. `message_pipe` 是**类变量**（crab_agent.py L23）——跨 agent 消息是进程内全局态，
     多实例隔离时需注意；
  3. `init_agent` 每 episode 都会被 EMOS 重置（`initialized=False` → 重新 init，
     multi_llm_policy.py L547）；probe 中还发现 `save_on_each_chat` 要求
     `logging_file` 必须是合法路径（否则 FileNotFoundError），即本地执行层隐含
     依赖日志目录布局；
  4. 独立调用不依赖 Leader，但**依赖 scene_description 文本**的获取方式
     （EMOS 从 env task context 取；RoboGuide 侧需等价提取）。

## 4. local LLM loop 是否属于 local execution policy？（B4）

**是。** CrabAgent 的 LLM 循环做四件事：动作选择、（可选）send_request 求助、
失败/无效输出的自恢复（输出非法→wait）、以及 subtask 级重规划。这些都是
episode 内、单机器人视角的执行策略——不是任务分配。它属于 EMOS 的
"local agent policy"，与 Oracle skill（确定性的低层）以 skill 名为界。

## 5. Option A vs Option B（B4 要求的对比）

| 维度 | Option A: RoboGuide→CrabAgent local loop→Oracle Skills | Option B: RoboGuide→deterministic adapter→Oracle Skills（现状 C1-S0） |
| --- | --- | --- |
| fairness（local 层对齐） | ✅ 与 EMOS local 层同构 | ❌ 少了 EMOS 的 local LLM 层 |
| determinism | ❌ LLM 引入方差 | ✅ 完全确定（Phase A 两次 happy run 轨迹逐位相同） |
| model/token cost | 每 tick 1+ 次 LLM 调用（probe 单次决策 2398 tokens 的量级×episode 长度） | 0 |
| local intelligence preservation | ✅ 保留 EMOS 的本地规划/求助 | ❌ 丢失（RoboGuide 语义由 MissionPlan 承担） |
| confounding factors | LLM 输出质量方差会污染系统对比（需固定 model/温度/seed 才可解释） | 方差来源只剩 mission organization 本身 |
| implementation difficulty | 中：adapter 需实现 get_next_skill 等价的驱动循环 + scene_description 提取 + skill 分派 | 已完成（C1-S0） |

## 6. 当前 Bridge 与 EMOS Stage2 逐项对照（B6）

| Component | EMOS baseline | RoboGuide C1-S0 | Same? | Difference | Fairness impact |
| --- | --- | --- | --- | --- | --- |
| simulator | habitat-lab sim（同一 checkout/conda env） | 同 | ✅ | — | — |
| scene / episode | MP3D `pRbA3pwrgk9`，episode 51（同 yaml 数据集） | 同（pin 同一 episode） | ✅ | — | — |
| robot embodiment | Spot（agent 0），同 URDF/配置 | 同 | ✅ | — | — |
| observations（文本） | `scene_description`（env task context）喂给 CrabAgent | 无（不产生/消费 scene 文本） | ❌ | EMOS local 层消费语义观察 | Protocol B 有影响 |
| high-level LLM（全局） | Leader group discussion（episode 一次） | 无对应物；RoboGuide 由 Mission Intelligence+Control 承担 | ❌ | **这是 E1 的独立变量，属预期差异** | — |
| local planner（LLM） | CrabAgent 逐 tick 决策 | 无（intent 已是实体级） | ❌ | local 层不等同 | Protocol B 有影响 |
| skill selection | LLM 从 allowed_actions 选 | 固定 nav（intent.operation 决定） | ❌ | 同上 | Protocol B 有影响 |
| Oracle navigation action | `OracleNavPolicy→OracleNavAction(base_velocity)` | **直接** `agent_0_oracle_nav_action`（同一 action 类） | ✅（同一低层） | 只是绕过了上层分派 | 低 |
| action arguments | skill 从 LLM 参数构造（entity index 等） | `destination→pddl_problem.get_entity→index` | ✅ 语义等价 | 路径不同 | 低 |
| termination | `has_finished_oracle_nav` measure / skill postcond | 同一 measure key（`agent_0_has_finished_oracle_nav`） | ✅ | — | — |
| success metric | pddl_success / measure 组合 | local COMPLETED=measure；RoboGuide 侧 execution-report | ✅（local 层） | RoboGuide 侧另有 satisfaction 语义层 | 低 |
| retry/replan | CrabAgent 可重选 skill/求助 | 无（FAILED 直报） | ❌ | EMOS 有 local 恢复行为 | Protocol B 有影响 |
| token usage | Leader 讨论 + 每 tick 决策 | 0 | ❌ | 成本口径不同 | 论文成本表需注明 |

## 7. Protocol A / Protocol B 判定（B8）

**Protocol A — Native：当前 Bridge 够用。**
Native 的定义就是各系统以自己的完整栈参赛：EMOS=Leader+CrabAgent+Oracle，
RoboGuide=Mission Intelligence+Control+Node+direct Oracle adapter。独立变量是
"整个系统"，结论解释为整体能力差异。当前 bridge 无需改动；需在论文中明确
RoboGuide 的 local 层不含 LLM（成本与行为差异已如实进对照表）。

**Protocol B — Controlled：需要对齐 local-agent stack（建议 Option A 形态）。**
Controlled 要 hold constant：simulator、scene、episode、embodiment、**Oracle skills
（同一低层动作）**、termination、success metric、LLM model/endpoint 条件。此时
local execution policy 必须等同，否则独立变量被污染（一个系统的导航成功率差异
可能来自 CrabAgent 的 LLM 决策而非 mission organization）。对齐方式：
RoboGuide 的 Habitat Local EAIOS 增加 CrabAgent-backed workflow——
`init_agent(subtask=RoboGuide 已提交的语义任务, chat_history=None)` + 逐 tick
`chat(scene_description)` + skill 分派（即把 `LLMHighLevelPolicy.get_next_skill`
的最小驱动循环搬进 adapter）。

**Protocol B 的一个 sub-decision（如实列出，建议明确化）**：给 CrabAgent 的
subtask 文本用哪一层？
- (i) RoboGuide 的 `objective` 自然语言文本（等价 EMOS 的自然语言 subtask，
  CrabAgent 自己完成"语言→实体"推理——最公平但引入实体选择失败风险）；
- (ii) 直接给 `any_targets|0`（probe 显示 CrabAgent 接受实体级 subtask 并正确
  产出 `nav_to_obj(any_targets|0)`，但等于替它做了实体解析这一步推理）。
建议 Protocol B 采用 (i) 为主判定、(ii) 作为消融，显式报告两者差异。

## 8. E1 fairness 推荐边界（B7）

**推荐 = Recommendation B（复用 CrabAgent 为 RoboGuide 的 Local EAIOS 执行层），
按协议分两步落地；当前 direct Oracle adapter（Recommendation C 形态）保留为
Protocol A 的 native 形态与 Protocol B 的对照消融。**

- What is held constant（Protocol B）: simulator/scene/episode/embodiment、
  Oracle skill 层（同一 `OracleNavAction`）、termination（`has_finished_oracle_nav`）、
  success metric、LLM model/endpoint（同一 relay + 同一模型名）、local policy
  （CrabAgent，喂等价 scene_description 与等价自然语言 subtask）。
- What is intentionally changed: 只换 mission organization——EMOS Leader
  （LLM 讨论式分配）vs RoboGuide（MissionPlan v0.7 + Matching/Scheduling/
  Proposal/Commit/Bind + 任务级取消/恢复语义）。
- Independent variable: distributed mission organization / coordination system。
- Remaining confounders（如实）: ① CrabAgent 的 subtask 文本措辞（(i)/(ii) sub-decision）；
  ② `message_pipe` 求助机制在单任务单机器人场景是否触发；③ LLM 温度/版本漂移；
  ④ RoboGuide 侧 satisfaction/取消语义改变了 episode 级终止条件，需在协议中固定
  （如统一以 pddl_success 为 episode 终止）。

## 9. 若实施 Protocol B 对齐，改哪里（integration adapter 层面）

1. `integrations/habitat-local-eaios`：新增第二种 backend（如 `CrabAgentBackend`），
   复用现有 HTTP/store/process 骨架：execute() 内
   `init_agent(...)`（subtask ← invocation.objective+parameters 的语义文本）→
   循环 `chat(scene_description)` → 解释返回动作（nav_to_obj→现有
   `_entity_index`+oracle nav step 路径；wait→空转；其余→FAILED+原因）→ 终态判定
   沿用现有 measure key；
2. `scene_description` 提取：调用 env 的 `get_task_text_context()` 等价路径
   （rearrange_task.py L430）或从 pddl problem 渲染，需在 adapter 内固定一种；
3. Node config：同一 capability profile 不变（mobility.navigate@v1），可选增加
   第二个 operation（如 mobility.navigate_llm@v1）以区分两种 workflow，Catalog
   是否扩列由 Codex 决策；
4. 评估协议记录 token usage（`CrabAgent.get_token_usage()`）进入 run evidence。

## 10. 是否需要 RoboGuide Core 改动？

**不需要。** crab agent 接入点全部位于 Local EAIOS 侧（deployment-owned adapter）；
RoboGuide 对 Local How 不可见（canonical invocation 只到 operation+params），
`mobility.navigate@v1` 语义不变。无 **E1 LOCAL-BOUNDARY BLOCKER**。

## 附：feasibility probe 记录（B10）

- 脚本 `probe/probe_crab_standalone.py`；输出 `probe/probe-output.txt`；
  环境 habitat conda env + EMOS emos.env（relay 端点，模型 `gpt-5.6-luna`）。
- 结果：`init_agent` 成功；`chat` 返回
  `{"name": "nav_to_obj", "arguments": {"target_obj": "any_targets|0", "robot": "agent_0"}}`；
  2 次调用合计 2398 tokens。无 Leader、无 sim、无讨论历史。
- 发现的工程细节：`save_on_each_chat` 使 `logging_file` 成为事实必填（空串即
  FileNotFoundError）；crab_planning 轮可能返回 None 但不影响后续逐步决策。
