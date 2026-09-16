# E1-I Multi-Node Shared-World Integration — FINDINGS

- Baseline `7690348f5ca681125e64cd48ff6741e492fc6f30`；本轮**零 Core 改动**
  （新增仅 `integrations/habitat-local-eaios`、`scenarios/e1-shared-world-episode-51/`、
  `evaluation/e1/shared_world/`）。`experiments/` 未触碰。

## Part A — Shared-world architecture

1. **HEAD**：见提交链（本 FINDINGS 所在 commit）。
2. **world 拥有者**：一个**子进程**（`ProcessWorldService` → `_child_world_process`）
   独占 1 个 Habitat simulator world + 1 个原版 EMOS Stage2
   `MultiAgentAccessMgr`（双 agent policy）。HTTP 父进程永不 import Habitat。
   3.1 单世界证据：`evidence/shared-world-summary.json` 的
   `identity.simulator_worlds=1, episode_reset_count=1, pid`（同一 pid 同一 episode
   承载两个 agent 的 outcome）。
   *工程教训（如实记录）*：第一版在父进程线程直接跑 sim，episode 启动期
   （reset+双 agent LLM 初始化 ~16s）GIL 饥饿导致 node 心跳断 >15s → lease 过期 →
   node-a 重连 → 原 attempt 进 recovery（失败证据已丢弃于迭代中，结论记录于此）。
   子进程化后心跳全程不断（run1/run2 events 无 recovery）。
3. **Node→agent 映射**：deployment Local How——node-a 端口(28100)→agent_0、
   node-b(28102)→agent_1（node config endpoint + `--agent-id/--agent-b-id`）。
   **Task→Node 由 RoboGuide Matching/Scheduling 决定**：run1 实际发生交叉分配
   （task-A→node-b、task-B→node-a），MissionPlan 无任何 NodeId。
4. **单 simulator/episode state**：是（见 2）；两个 agent 的 action 经原版
   `actor.act`→`gym_env.step` 作用于同一世界（双侧 position 均变化）。
5. **assignment 进入原版 Stage2**：`_pair_arguments` 把两个 committed invocation
   翻译成 EMOS Stage1 的 `AgentArguments{robot_id: agent_0/1, subtask_description:
   各自的 semantic subtask}`，`_install_assignment` 仅替换
   `multi_llm_policy.group_discussion` 符号（每次执行后恢复）——与单节点
   C1-S0B 相同的注入点，双 assignment 版本。
6. **EMOS Stage1 Leader**：从未运行（无 leader LLM 调用；token 证据仅两 agent
   的 local 决策）。
7. **Core change**：无。

## Part B — Production evidence（全部经 POST /v1/missions 真实链）

| 判据 | run1 | run2 |
| --- | --- | --- |
| two Nodes registered | ✓ | ✓ |
| two Tasks bound（Match→Schedule→Proposal→Commit→Bind） | ✓ | ✓ |
| two stable distinct local handles | ✓ | ✓ |
| both RUNNING（bridge stores ACCEPTED→RUNNING） | ✓ | ✓ |
| one world / one episode reset | ✓ | ✓ |
| both agents acted（双侧位移） | ✓ | ✓ |
| canonical intents preserved（按 task_id 对账 plan） | ✓ | ✓ |
| original Stage2 active（双侧 local_llm_calls≥1） | ✓ | ✓ |
| no Stage1 reassignment（arrivals=plan destinations） | ✓ | ✓ |
| Node A/B dispatch authorization = 1 each | ✓ | ✓ |
| official goal predicates both satisfied | ✓ | ✓ |
| **official pddl_success = true** | ✓ | ✓ |
| RoboGuide Task A/B/Mission = Completed（另行记录，不替代 pddl） | ✓ | ✓ |

- **exactly-once（共享层）**：assignment admitted once each（arrival log 恰 2 条）、
  episode started once；deterministic tests 覆盖 duplicate accept/dispatch/
  consumed-guard。
- **S1（单 assignment）**：fail-closed——lone execution FAILED "pair never
  assembled"、无 episode、无伪造 pddl、mission Failed。
- **S2（重复 assignment）**：deterministic test 证明 same handle / 不重复执行 /
  不二次 reset（tests/test_shared_world.py）。
- **S3（单侧取消→整 mission cancel）**：在双端真 RUNNING 后 cancel 202 →
  共享 episode 停止 → 双 endpoint CANCELLED（basis=cancellation）→ mission
  Cancelled。**Limitation（如实）**：当前实现取消任一 Node 即终止整个共享
  episode，另一 Node 也终态 CANCELLED——非 per-agent 独立取消；未发明新
  recovery 语义。
- **peer communication（§10）**：run1/run2 `send_request=0, message_pipe=0`
  （逐 agent 记录于 summary.json）——episode 51 Independent 工作负载未触发；
  未禁用任何原生机制。

## Part C — Semantic equivalence

- **同一 episode**：同一 dataset（`mobility_episodes_1.json.gz`，digest 见
  workload yaml）、episode 51、seed 40、scene `mp3d/pRbA3pwrgk9`。
- **完整 goal 谓词集**：RoboGuide 侧两 Task 的 canonical destination 恰为
  `any_targets|0` + `TARGET_any_targets|0`（=官方 task_spec goal 集）；EMOS 侧
  由官方 runner 天然覆盖。
- **同 world 语义**：同一 Habitat 构建与 sim 步进语义、原版 Stage2
  policy/skills/OracleNav。
- **benchmark success 权威**：双侧均只有官方 `habitat.pddl_success`；
  negative run 证明单 goal 完成时 pddl_success=false 且 Eval 不计成功（Task
  Completed 不越权）。
- **assignment 可交换性（§6 判定）**：goal 谓词为 `any_at`（agent 无关），
  oracle `solution` 仅为参考；Mission 未 hard-bind，RoboGuide 自行
  Matching/Scheduling（run1 交叉分配为实证）。

## Part D — Formal E1 admission

**READY**（`evaluation/e1/controlled-workload-v0.1.yaml` 已更新：
`admission.status: ready`，blocker 移除，evidence anchor 指向本目录 summary.json）。
依据：§18 全部条件满足——EMOS runner 官方完整语义任务；RoboGuide runner 同
episode/同谓词/同 world 语义/原版 Stage2/production Control+Node 路径
（本目录 5 runs）。

**PAIRED E1 PIPELINE**：本目录证明了 RoboGuide Controlled 双 Node 全链与
official success 权威对齐；统一 harness 的 EMOS×1+RoboGuide×1 双臂同跑
（evaluation specs `mobility-smoke.yaml` 路径）尚未在本轮执行——列为
paired smoke 待办，不阻塞 admission（其所需的所有身份/digest/episode/谓词
匹配字段已在 workload yaml 冻结）。

## Part E — Coupling taxonomy

见 `../WORKLOAD-TAXONOMY.md` + `../workload-taxonomy.json`（如已生成）。

## Metrics（§16）

`runs/*/evidence/shared-world-summary.json` 含 per-run：simulator_steps、
per-agent local_llm_calls/local_tokens/send_request/message_pipe、pddl_success、
identity；`runs/*/verdict.json` context 含 mission/task 状态与 peer 通信计数。
Global（RoboGuide 侧 0 LLM）与 local（EMOS CrabAgent）成本分离记录。

## 已知限制（如实）

1. per-agent 取消不独立（取消即终止共享 episode）。
2. shared-world 每 bridge 进程只能消费一个 episode（fresh-run 契约；
   `episode_consumed` guard）。
3. entity-grounded subtask 模式为主（与 EMOS Leader 分配语句的信息含量对齐）；
   natural-objective 变体未在本轮 paired 跑（列为后续 fairness 消融）。
4. `both_tasks_completed→pddl_success` 的强耦合依赖 agent 正确解析实体；
   negative run 展示了失败侧的正确行为。
