# C1-S0B Design Trace — CrabAgent-backed Local How（PHASE 1 输出）

> Superseded implementation trace: Formal E1 preflight removed this copied
> driver loop. The current adapter replaces only Stage1 assignment and runs
> original EMOS Stage2 plus its configured skills unchanged.

Baseline `c29f384`。全部条目为本轮源码重读确认（file:line），非沿用旧 FINDINGS。

## EMOS 关键事实（wrapper 设计输入）

| 项 | 结论 | 证据 |
| --- | --- | --- |
| CrabAgent.init_agent 输入 | `robot_type, task_description, subtask_description, chat_history=None, enable_logging, logging_file`；system prompt 固定 "You MUST take finish subtask assigned to you"；subtask 非空走 assigned 分支 | `habitat-mas/agents/crab_agent.py:47-107` |
| CrabAgent.chat 输入 | 一条观察文本（EMOS：`start/step_action_prompt.format(scene_description=…)`） | `crab_agent.py:109-115`、`habitat-baselines/rl/hrl/hl/llm_policy.py:98-122` |
| CrabAgent.chat 输出 | `(action_name, parameters)`（OpenAI tool_calls[0]，`tool_choice="required"`）；send_request→内部路由并返回 wait；自请求→None；nav/pick/place 自动带 `robot=self.name` | `models.py:220-246`、`crab_agent.py:109-138` |
| chat_history lifecycle | init 时注入讨论历史（可选）；每次 chat 追加 user/assistant/tool 消息；episode 重启用 `initialized=False`→重新 init_agent | `crab_agent.py:77-79`、`multi_llm_policy.py:546-582` |
| scene_description 来源 | `envs.call("get_task_text_context")` → `RearrangeTask.get_task_text_context()`（rearrange_task.py:430）→ `get_text_context(sim, robot_configs)`（habitat_mas_sensors.py:305）→ `{"scene_description": …, "robot_resume": …}`。**adapter 可直接调 `habitat_env.task.get_task_text_context()`——同一函数、同一状态源** | 本轮确认 |
| allowed actions 来源 | `llm_policy.py:18 ACTION_POOL = [send_request, nav_to_obj, pick, place, reset_arm, wait]`（构造 CrabAgent 的 tool schema）；策略侧另受 config `allowed_actions` 限定 | `llm_policy.py:18,42` |
| skill-name → skill-object | `LLMHighLevelPolicy._skill_name_to_idx` + `HierarchicalPolicy` + `defined_skills` config；`nav_to_obj → OracleNavPolicy(action=base_velocity, max 1000)`；OracleNavPolicy.on_enter 解析 entity index → `oracle_nav_action`；done 由 `has_finished_oracle_nav` 判 | `oracle_nav.py:21-110`、`oracle_skills_multi_agent.yaml` |
| skill termination | nav：finished measure / max_skill_steps；wait：解析等待步数（`wait.py:24`，动作键 `agent_<i>_wait`，布尔参） | 本轮确认 |
| wait/invalid 行为 | LLM 输出非法/None → `wait(500)`（llm_policy.py:144-147,155-158）；即 EMOS 用"等待"消化无效输出，不重问 | `llm_policy.py:144-158` |
| send_request 行为 | 写入类级 `message_pipe[target]`，返回 wait(500)；下次该 agent chat 时注入 pipe 内容；**类变量=进程级全局态，跨执行残留**（wrapper 需逐 tick 快照观测） | `crab_agent.py:23,110-125` |
| token usage 获取 | `OpenAIModel.token_usage` 累计；`_record_usage`（models.py:96-116）**每调用**写 `token_usage_details.jsonl`（与 logging_file 同目录，含 model/latency/usage）——wrapper 直接启用 logging 即获逐调用记账 | 本轮确认 |
| logging_file 约束 | `save_on_each_chat=True` 默认开 → logging_file 必须是可写路径（空串→FileNotFoundError） | `models.py:72-73,79`、probe 实证 |

## 最小 wrapper 设计（不重写 EMOS，只驱动）

```
CanonicalInvocation(operation=mobility.navigate@v1, objective, parameters.destination)
  ↓ (deployment-selected backend = emos-crabagent; Local How，Node/Core 不可见)
CrabAgentMobilityBackend (extends HabitatMobilityBackend: 同一 env init/entity/position/step)
  ↓ subtask_mode:
  │   natural-objective  → subtask_description = invocation.objective   (Protocol B 主候选)
  │   entity-grounded     → subtask_description = "Navigate to {destination}." (ablation)
  │   task_description    = invocation.objective（两模式相同）
  ↓ CrabAgent.init_agent(robot_type=<deployment>, chat_history=None, logging_file=<evidence>/…)
  ↓ scene_description = habitat_env.task.get_task_text_context()["scene_description"]  ← EMOS 同源
  ↓ loop（预算 max_steps，取消逐步检查）:
  │   chat(EMOS start/step prompt ⊕ scene_description)        ← 逐 tick LLM 决策
  │   dispatcher:
  │     nav_to_obj(target)   → get_entity 校验 → 复用直接后端的 oracle-nav step 循环
  │                            （=OracleNavPolicy 语义：entity+finished measure）→ COMPLETED/FAILED
  │     wait(n)              → step agent_<i>_wait n 次（≤剩余预算）
  │     send_request         → 观测记录（chat 已内部转 wait）；message_pipe 逐 tick 快照
  │     其他合法 skill 名（pick/place/reset_arm/get_agents）→ FAIL-CLOSED（unsupported）
  │     None/畸形/未知实体    → invalid_output++；连续 >5 → FAILED；否则按 1 步 wait（EMOS 同型）
  ↓ terminal → LocalExecutionOutcome（与直接后端同 schema）+ 侧车证据
```

侧车证据（evidence dir，供 PHASE 15 归档）：`scene_description.txt`、
`action_trace.jsonl`（call_index/stage=local-agent/action/args/token_delta/latency_ms/
pipe_size）、`token_usage_details.jsonl`（EMOS 原生逐调用）、`token_summary.json`。

## 边界遵守

- 不 copy CrabAgent/OpenAIModel 源码；仅 import 并驱动。
- backend 由 deployment CLI（`--backend`/`--subtask-mode`）选择；MissionPlan/ExecutionIntent/
  HTTP 请求均无 backend 字段；canonical operation 仍 `mobility.navigate@v1`。
- Node/Core 不出现 CrabAgent/nav_to_obj/PDDL index/Habitat action 名/scene_description。
- prompt 模板逐字取自 EMOS llm_policy（对齐必需），其 sha256 记入 PHASE 27 freeze 证据。
