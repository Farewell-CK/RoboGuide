# Episode51 / seed40 一致初始状态双臂对照报告

## 1. 结论

初始化修复验收 **PASS**。`codex/ep51-initialization-parity@a24feeba4b84336d870a235e56c46fb91b2d6d67` 已快进合并并推送到 `main`；本地 `main` 与 `origin/main` 均为该 SHA。

在同一 Episode51、seed40、场景、数据集和实际 reset 采样状态下，原生 EMOS 与 RoboGuide 各执行了一次。两臂均在 108 simulator steps 获得 Habitat 官方 `pddl_success=True`，两个官方 stage goal 均为 1.0。RoboGuide Mission 为 `Completed`，B1 provenance、Formal population admission 和 benchmark population admission 均有效。

这次成功同时暴露了一个独立的执行契约问题：RoboGuide 给 agent-0 的 canonical destination 是 `any_targets|0`，原始 EMOS Stage2 实际调用却是 `nav_to_obj(TARGET_any_targets|0)`。本次仍获官方成功，是因为 Episode51 两个目标的官方距离为约 1.6754 m，而 `any_at` 阈值为 2.0 m；两个谓词的可接受区域存在重叠。官方 benchmark 成功因此不能证明 agent-0 忠实执行了其本地 assignment。

本轮是一次诊断性单样本对照，不构成 E1 性能结论。

## 2. 代码验收与合并

### 2.1 已确认的缺陷

旧的 RoboGuide Stage2 初始化先用 Habitat iterator 的首个 episode 构造 Gym/Habitat-Sim，再把 `habitat_env.episodes` 替换为 Episode51。Habitat-Sim 的场景相关状态和第一次随机初始化已经在构造阶段发生，因而同样的 `episode_id=51` 和 `seed=40` 不能复现原生 EMOS 的实际 reset 状态。

修复在创建 Gym 之前加载数据集、唯一解析冻结 episode，并把数据集缩减为该 episode；Gym 构造后再次验证只保留这个 episode。实现位于：

- `integrations/habitat-local-eaios/habitat_local_eaios/emos_stage2.py:16`
- `integrations/habitat-local-eaios/habitat_local_eaios/emos_stage2.py:99`
- `integrations/habitat-local-eaios/tests/test_crabagent_backend.py:137`

缺失 episode、重复 episode，以及 Gym 工厂丢失已固定 episode 均 fail closed。该修改没有改变 MissionPlan、Control、Stage2 决策逻辑、Habitat 目标或官方判定。

### 2.2 合并结果

| 项目 | 结果 |
|---|---|
| 合并前 `origin/main` | `9ea39c2077308291569e67fd84c3fa40b7640599` |
| 验收分支 | `codex/ep51-initialization-parity` |
| 验收与合并 SHA | `a24feeba4b84336d870a235e56c46fb91b2d6d67` |
| 合并方式 | fast-forward |
| `origin/main` | `a24feeba4b84336d870a235e56c46fb91b2d6d67` |
| 文件冲突 | 无 |

### 2.3 合并后质量门禁

- Habitat adapter targeted tests：通过。
- Ruff format/check：20 files，全部通过。
- strict mypy：19 source files，全部通过。
- Python function-doc check：通过。
- `git diff --check`：通过。
- Rust `integration-server` 与 `roboguide-node`：当前 main 重新构建通过。
- 开发分支合并前还执行过仓库全量 `uv run pytest -q`，通过。

## 3. 对照设计与冻结配置

原始运行目录：

- 对照根目录：`/data/workspace/code/roboguide-ep51-paired-20260921T084406Z`
- 原生 EMOS：`/data/workspace/code/roboguide-ep51-paired-20260921T084406Z/emos-native`
- RoboGuide：`/data/workspace/code/roboguide-ep51-paired-20260921T084406Z/roboguide-execution`

冻结条件：

| 条件 | 值 |
|---|---|
| Episode | `51` |
| Habitat seed | `40` |
| Scene | `data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb` |
| Dataset revision | `mobility_episodes_1` |
| Dataset SHA256 | `5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca` |
| EMOS checkout | `e9501db45d634b087bf5d1a14228266685e8feeb` |
| EMOS tracked diff SHA256 | `172268dd4cf44e8e50ba1d51de2f9f7a4ff5377b32d7aa96bfa21f1b77c0ee78` |
| RoboGuide checkout | `a24feeba4b84336d870a235e56c46fb91b2d6d67` |
| Stage2 model | `gpt-5.6-luna` |
| GPU | index 1 |
| 顺序 | 原生 EMOS → RoboGuide |
| 完整执行预算 | 每臂一次，不因结果追加运行 |

RoboGuide 首次 wrapper 启动在任何 Provider、Mission Request 或 Habitat 执行前因独立 worktree 的默认 EMOS 相邻路径不存在而停止，归档为 `EXTERNAL_INFRA/emos_checkout_missing`。随后显式设置已核验的 `ROBOGUIDE_EMOS_ROOT=/data/workspace/code/emos-baseline`，在新目录执行唯一一次实际 RoboGuide arm。预检失败证据保留在 `evidence/setup-preflight/`，没有被记为 benchmark 尝试。

## 4. 初始状态一致性

实际执行 episode 的 reset 采样如下：

| Agent | 原生 EMOS position | RoboGuide position | 原生/RoboGuide sampled rotation |
|---|---|---|---|
| Spot / agent-0 | `[1.5805046558380127, -2.566195011138916, -4.83552360534668]` | 相同 | `5.681678493074564` / 相同 |
| Fetch / agent-1 | `[-0.08676141500473022, 2.309159994125366, 3.6870832443237305]` | 相同 | `3.4487898183762367` / 相同 |

RoboGuide 的 `diagnostics-initial.json` 还从实体 quaternion 读取 rotation。Spot 被序列化为 quaternion angle magnitude `0.6015070676803589`，而 reset sampler 写的是绕正 Y 的 `5.681678493074564`；这是表示方式差异，raw reset sampler 在两臂记录的采样角相同。Fetch 的实体角为 `3.4487898349761963`。

原生 evaluator 会在 episode 终止后再 reset 一次，并覆盖配置的 `w2j` 文件。因此 `initial-agent-state-after-terminal-reset.json` 是收尾 reset，不是被执行 episode 的初始状态。执行开始时、进度仍为 0% 时读取的原始文件值保存在 `evidence/emos-native/executed-reset-live-observation.json`；RoboGuide 的同源 sampler 记录和独立 physical diagnostics 均确认相同位置。这个覆盖行为是本次原生证据归档的能力限制，报告没有把终止后的文件当成执行初态。

## 5. 原生 EMOS 结果

### 5.1 任务组织

Stage1 Leader 实际分配：

- Spot / agent-0：`Navigate across floors using the stairs and reach both objects: TARGET_any_targets|0 and any_targets|0.`
- Fetch / agent-1：`Nothing to do`

原始 Stage2 的实际工具调用：

- Spot：`nav_to_obj({"target_obj":"TARGET_any_targets|0"})`
- Fetch：`wait({})`

Spot 的前置文本生成曾提到 `any_targets|0`，但真实送入物理动作路径的结构化工具调用只有上述 `TARGET_any_targets|0`；本报告以工具调用为实际执行证据。

### 5.2 官方结果

| 指标 | 结果 |
|---|---|
| 进程退出码 | 0 |
| Simulator steps | 108 |
| `pddl_stage_goals.robot_at_object_0_success` | 1.0 |
| `pddl_stage_goals.robot_at_receptacle_0_success` | 1.0 |
| `pddl_success` | 1.0 |
| LLM calls | 7 |
| Total tokens | 11,997 |

原生 arm 没有启用 RoboGuide physical diagnostics，因此无法从本次原生证据精确给出每个谓词首次成立的 simulator step，也没有精确终态位姿；这些字段标记为 unavailable，不从官方终态指标反推。

## 6. RoboGuide 结果

### 6.1 MI 与 Control

本次只创建了一个 Mission Request：`request-e84e0cd2bbd54847b2a10ebf78fafce3`。

MI 第一个草案即通过 Reviewer：revision 1、`approved=true`、无 rejected draft、无 Repair。计划结构为：

- Context `shared_scene` 使用 `independent`；`relations=[]`，无 `shared_view`。
- `task_reach_any_target`：destination `any_targets|0`，resource `space:1`。
- `task_reach_goal_receptacle`：destination `TARGET_any_targets|0`，resource `space:1`。
- 两个 Task 均无 DAG dependency。

Control 实际 assignment：

- node-a / `e1-shared-node-a` → `task_reach_any_target` → `any_targets|0`
- node-b / `e1-shared-node-b` → `task_reach_goal_receptacle` → `TARGET_any_targets|0`

这证明 `independent` 没有被解释成串行；两个无执行期依赖的任务在资源允许时并发进入 shared-world。

### 6.2 Stage2 实际执行

传给 Stage2 的 canonical subtasks 与 MissionPlan 一致，但实际工具调用为：

- agent-0：收到 `Reach semantic destination any_targets|0.`，却调用 `nav_to_obj(TARGET_any_targets|0)`。
- agent-1：收到 `Reach the goal receptacle position TARGET_any_targets|0.`，调用 `nav_to_obj(TARGET_any_targets|0)`。

两台 agent 全程保持 `nav_to_obj`，没有 pick、place、reset_arm、High-level replan 或 1000-step skill budget termination。两个 local outcome 的 `local_skill_completed=false`，终止依据均为 `habitat-pddl-success`。

### 6.3 官方谓词时间线

RoboGuide physical diagnostics 逐 step 调用官方 `Predicate.is_true(sim_info)` 路径：

- reset：两个谓词均为 false。
- step 26：`any_at(TARGET_any_targets|0)=true`；`any_at(any_targets|0)=false`。
- step 108：两个谓词均为 true；官方 `pddl_success=true`，episode 因联合目标成立终止。

诊断采集 108 个连续 step，丢样 0，write failure 0；总 capture 时间约 0.0919 s，最大单次约 0.00301 s。

### 6.4 Mission 与 B1 判定

| 项目 | 结果 |
|---|---|
| Mission | `Completed` |
| node-a execution | `Completed` |
| node-b execution | `Completed` |
| Simulator steps | 108 |
| Official `pddl_success` | `true` |
| Provenance | VALID |
| Formal population | valid |
| Benchmark population | valid |
| Benchmark outcome | `BENCHMARK_TRUE` |
| Controller event archive | complete，130 events，last sequence 130 |

## 7. 对照分析

| 维度 | 原生 EMOS | RoboGuide |
|---|---|---|
| 实际 reset 初态 | 与 RoboGuide 相同 | 与原生相同 |
| 上层组织 | Spot 承担两个目标；Fetch 无任务 | 两个 independent navigation Task |
| 物理执行 | Spot→TARGET；Fetch wait | agent-0→TARGET；agent-1→TARGET |
| 官方 steps | 108 | 108 |
| 两个 stage goals | 均成功 | 均成功 |
| 官方 PDDL | true | true |

两个组织方案在 Episode51 的官方语义下都可以成功。`any_at` 只要求“任意机器人当前位于目标阈值内”，没有规定 distinct executor，也没有规定每个目标必须由不同机器人到达。目标间约 1.6754 m，小于本部署 `robot_at_thresh=2.0`，所以一个机器人到达重叠区域即可同时满足联合终态。原生成功样本直接证明“一台执行、另一台 wait”合法；本次 RoboGuide 则证明“两任务并发”也可以获得官方成功。

不能据此判断哪种组织普遍更好。两臂只各运行一次，真实模型输出具有随机性；RoboGuide 还包含完整 MI、Control、Node 和归档路径，墙钟时间及 token 使用边界与原生 EMOS 不同。

## 8. 新发现与裁决

### 已证实

1. 初始化差异的根因已修复；本次实际初态一致。
2. 新版 MI 在真实完整链路生成了合法 `independent` 计划，并保留两个官方目标。
3. Control 按 `space:1` 把两个任务分配到两个 endpoint，并发执行。
4. 两臂均获得官方 `pddl_success=True`。
5. RoboGuide agent-0 的真实 Stage2 参数与 canonical destination 不一致。
6. 当前 shared-world 在全局 `pddl_success` 终止时把两个 endpoint 都报告为 `Completed`，尽管两个 endpoint 的 `local_skill_completed` 都是 false。

### 尚不能证明

1. 这一个样本不能证明 RoboGuide 的任务组织优于或劣于原生 EMOS。
2. 原生 arm 未记录逐 step physical diagnostics，不能比较两臂完整轨迹或谓词首次成立时间。
3. 本次成功不能证明 Stage2 未来会稳定遵守 canonical destination。
4. Benchmark 成功不能单独证明每个 Mission Task 的局部 expected effect 分别成立。

## 9. 后续建议

下一步应独立验收 `origin/codex/stage2-contract-guard`，用本次真实反例验证它会在 `gym_env.step()` 前拒绝 agent-0 的 destination mismatch，并保留原始模型输出。Guard 不能替模型选择正确动作，也不能改变官方任务。

同时需要审查 `EmosStage2Runtime._policy_loop()` 在全局 `pddl_success` 时将尚未完成本地 assigned skill 的 execution 标记为 `Completed` 的语义。代码位置为 `integrations/habitat-local-eaios/habitat_local_eaios/emos_stage2.py:310`。应继续保持 Habitat benchmark outcome、Local skill completion 和 RoboGuide Task satisfaction 为不同事实；在这项边界明确前，不应把本次 `Mission Completed` 当成两个 canonical navigation assignment 均被忠实执行的证明。

本轮不修改该语义，也不重写已经归档的成功结果。

## 10. 证据完整性

- 原始运行目录保持不变。
- 报告分支只纳入脱敏的必要证据；没有凭据、Authorization header 或敏感环境变量。
- 发布前敏感模式扫描结果为 0 match。
- `SHA256SUMS` 覆盖报告分支内全部证据文件。

