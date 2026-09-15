# EMOS Stage2 Local Execution Stack — Source Trace (B1/B2/B5)

全部结论来自 `/data/workspace/code/emos-baseline` 实际 Python 源码（commit 级证据见各条目），
非 README。调用链自顶向下：

## 0. 入口与 eval 循环

| # | File | Class/function | 职责 |
| --- | --- | --- | --- |
| 1 | `habitat-baselines/habitat_baselines/rl/multi_agent/habitat_mas_evaluator.py` | `HabitatMASEvaluator.evaluate_agent` (L34-417) | eval 循环：`envs.reset()` → 反复 `agent.actor_critic.act(batch, envs_text_context=…)` (L175) → `envs.step(step_data)` (L210)。任务文本来自 `envs.call("get_task_text_context")` (L156) + obs `pddl_text_goal` |
| 2 | `habitat-baselines/habitat_baselines/rl/multi_agent/multi_llm_policy.py` | `MultiLLMPolicy.act` (L465-650) | 两阶段调度：Stage 1 全局组织（episode 第一步，`prev_actions` 全零时，L516）；Stage 2 逐 agent 本地执行（L550-595） |

## 1. Stage 1 — Global Organization（"EMOS Leader"）

| # | File | Function | 输入 | 输出 | 职责 |
| --- | --- | --- | --- | --- | --- |
| 3 | `multi_llm_policy.py` | `group_discussion` (L227-420) | `robot_resume`（各机器人 mobility/perception/manipulation 能力 JSON）、`scene_description`、`task_description`(=pddl_text_goal) | `dict[robot_id → AgentArguments{robot_id, robot_type, task_description, subtask_description, chat_history}]` | 一次性任务分解+分配+确认 |
| 4 | `multi_llm_policy.py` L289 | `OpenAIModel(leader_prompt, …)` | leader 系统提示（L42-61：`{robot_id||subtask_description}` 格式） | — | **Leader = 一个 OpenAIModel 实例**，非独立类 |
| 5 | `multi_llm_policy.py` L326-334, `parse_leader_response` L179 | leader.chat → 正则解析 | task+scene | `{robot_id: subtask}` | 任务分解与分配 |
| 6 | `multi_llm_policy.py` L350-398, `parse_agent_response` L195 | 各 robot 反思 `{yes}/{no\|\|reason}`；≤3 轮 leader 重分配 | 分配结果 | 最终分配 + 每个 robot 的讨论 `chat_history` | agent reflection（EMOS 论文的讨论阶段） |

## 2. Stage 2 — Local Embodied Execution（每机器人）

| # | File | Class/function | 输入 | 输出 | 职责 |
| --- | --- | --- | --- | --- | --- |
| 7 | `multi_llm_policy.py` L575-582 | `LLMHighLevelPolicy.llm_agent.init_agent(robot_type, task_description, subtask_description, chat_history)` | **Leader 分配的 subtask + 讨论历史** | 初始化的 CrabAgent | subtask 注入点 |
| 8 | `habitat-mas/habitat_mas/agents/crab_agent.py` | `CrabAgent.init_agent` (L47-107) | 同上 | system prompt（L9-19："You MUST take finish subtask assigned to you"）+ `chat(subtask_to_actions_prompt, crab_planning=True)` 自规划完整动作序列 | 本地任务规划 |
| 9 | `habitat-baselines/rl/hrl/hl/llm_policy.py` | `LLMHighLevelPolicy.get_next_skill` (L72-165) | 每个plan tick 的 `scene_description`（来自 env task context，`habitat-lab/…/rearrange_task.py::get_task_text_context` L430） | `next_skill` id + args | 逐 tick 调 `llm_agent.chat(...)` (L137) 并把 function-call 映射到 skill (`_skill_name_to_idx`) |
| 10 | `habitat-mas/agents/crab_agent.py` | `CrabAgent.chat` (L109-138) | 观察（scene description）+ `message_pipe` 注入消息（类变量 L23，跨 agent `send_request` 文本信道） | `{"name": action, "arguments": …}` | **本地 LLM 决策循环**：动作选择、求助（send_request→wait）、自动带 `robot=self.name` |
| 11 | `habitat-mas/habitat_mas/utils/models.py` | `OpenAIModel` (L21-, `chat` L126) | prompt + tools schema | function-call | LLM 后端：`openai.OpenAI()`，model=`$EMOS_LLM_MODEL`(默认 gpt-4o)，key/base URL 来自环境（`emos.env`：relay + `gpt-5.6-luna`） |
| 12 | `habitat-baselines/rl/hrl/hierarchical_policy.py` + `config/…/defined_skills/oracle_skills_multi_agent.yaml` | skill 分派 | skill id+args | 低层 skill 实例 | `nav_to_obj → OracleNavPolicy(action=base_velocity, max_skill_steps=1000)`、`pick/place → OraclePick/OraclePlacePolicy`、`wait → WaitSkillPolicy` |
| 13 | `habitat-lab/habitat/tasks/rearrange/actions/oracle_nav_action.py` | `OracleNavAction` (L24) | 目标实体 index | base velocity 指令 | navmesh follower（Oracle 导航低层控制器） |
| 14 | habitat sim (`envs.step`) | — | 指令 | 观测/测量/`has_finished_oracle_nav`/pddl_success | 物理执行与成功度量 |

## 3. C1-S0 使用的 mobility 配置事实

- `habitat-baselines/config/multi_rearrange/llm_spot_fetch_mobility.yaml`：
  - evaluator=`HabitatMASEvaluator`；双 agent（Spot=agent_0、Fetch=agent_1）均 `hl_llm` 高层策略 + `oracle_skills_multi_agent`；
  - `allowed_actions`: wait/nav_to_goal/nav_to_obj/nav_to_goal_dog/pick/place/nav_to_receptacle_by_name；
  - `use_llm_agent: True`；显式注释 "use the oracle navigation skill (which will actually execute navigation)"；
  - 附加 measure `has_finished_oracle_nav`（Oracle 导航完成判据）。
- `habitat-mas/agents/actions/base_actions.py::nav_to_obj` docstring：目标格式 `any_targets|x`，
  "can NOT be a region name" —— 与 C1-S0 `destination="any_targets|0"` 同一实体引用语义。

## 4. EMOS Mobility 中 Spot（agent 0）的真实路径（B5）

```
pddl_text_goal + robot_resume + scene_description
→ MultiLLMPolicy.act [Stage1] group_discussion: Leader 分解 → Spot 收到 subtask（自然语言）
→ [Stage2] CrabAgent("agent_0").init_agent(subtask, chat_history)  # 自规划动作序列
→ 每 plan tick: CrabAgent.chat(scene_description) → e.g. {"name":"nav_to_obj","arguments":{target_obj:"any_targets|0"}}
→ HierarchicalPolicy → OracleNavPolicy → OracleNavAction(base_velocity, navmesh follower)
→ env.step() × N → has_finished_oracle_nav / pddl_success
```

**结论（B5）**：EMOS mobility baseline 的 Spot 导航**不是**单一抽象层调用：
底层与 RoboGuide C1-S0 相同（同一 `OracleNavAction`），其上叠了两层 LLM——
Leader（全局，episode 一次）与 CrabAgent（本地，逐 tick）。C1-S0 直接驱动 Oracle
动作，跳过的正是这两层。
