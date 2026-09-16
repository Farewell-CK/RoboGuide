# E1 Workload Taxonomy — RoboGuide Coupling 分类（静态扫描）

来源：`habitat-lab/config/benchmark/multi_agent/pddl/*.yaml` + 对应
`config_*.yaml`（agents/steps）+ episode 数据集。分类依据任务语义（非名字）。

## 分类总表

| Benchmark / task_spec | 机器人 | 官方 goal 谓词 | 任务语义阶段 | RoboGuide coupling | 置信 | Protocol A | Protocol B |
| --- | --- | --- | --- | --- | --- | --- | --- |
| spot_fetch_mobility（**E1-I 现役**） | Spot+Fetch | `any_at(any0)`,`any_at(TARGET_any0)` | 纯导航×2，agent 无关 | **Independent** | 高（已实证 shared-world 2/2） | ✓ | ✓（nav 已接入） |
| height_per | Spot+Fetch | `any_at×2 + is_detected×2` | 导航+感知 | **Independent** | 高 | ✓ | 部分（nav✓，感知未接） |
| spot_drone_per | Spot+Drone | `is_detected×2` | 感知 | **Independent** | 中 | ✓ | 部分 |
| multi_agent_per | 2+ robots | `robot_at(any_i, agent_i)` ×N | agent 绑定导航 | **Independent**（但 assignment 受谓词绑定约束，不可自由交换） | 高 | ✓ | 部分 |
| dist_man | Spot+Fetch | `at(any_i, TARGET_i)`×2 | 每对象 nav→pick→nav→place | **Independent**（任务级独立；单任务内部为顺序操作阶段） | 中高 | ✓ | 需 pick/place 技能接入 |
| height_man | 同上 | 同上 | 同上（跨楼层） | **Independent**（同上） | 中高 | ✓ | 同上 |
| multi_agent_mobility（misnomer：含 pick/place） | 2 | `at×2` | nav→pick→nav→place | **Independent** | 中 | ✓ | 同上 |
| fetch_stretch_man | Fetch+Stretch | `at×2 + not_holding×2` | 双对象操作 | **Independent** | 中 | ✓ | 同上 |
| multi_agent_social_nav | 2 | `at×2 + not_holding×2` | stage 1_1..2_2 双对象 | **Independent** | 中 | ✓ | 同上 |
| multi_agent_tidy_house | 2 | 同上 | 双对象整理 | **Independent** | 中 | ✓ | 同上 |
| drone_spot_rearrange | Drone+Spot | `at(any0,TARGET)+not_holding×2` | nav→pick→nav→place | **Independent**（含 agent 能力差异分配） | 中 | ✓ | 同上 |
| multi_agent_man | 4 agents | `robot_at(TARGET_i, agent_i)` + `at(...)` 混合 | agent 绑定 + 对象操作混合 | **Independent**（assignment 受 robot_at 谓词硬约束） | 中 | ✓ | 同上 |

## 关键结论

1. **官方集没有任何天然的 SequentialHandoff / ConcurrentCooperation /
   TightlyCoupled 任务**：所有官方 goal 谓词都不要求对象在 agent 间转移、
   不要求共享运行时状态或高频 peer 数据面。"cooperative/man" 命名 ≠ RoboGuide
   coupling 语义。
2. **SequentialHandoff**：需 derived workload（例如：agent A pick → 交给 B →
   B place；或 E1 原始 vision 中的"采样递送"链）。
3. **ConcurrentCooperation**：RoboGuide 仓库已有语义样例
   `scenarios/execution-relations-v0.1`（requires-active 安全观察+导航），
   对应 EMOS 的 guidance 任务族；官方 multi_agent 集 **未包含**，需 derived。
4. **TightlyCoupledCooperation**：官方与现有场景均无；需 derived（如双机
   协同搬运，Habitat-MAS 目前无此任务）。
5. **Phase 标注原则（§21）**：dist_man 等操作型任务的每个 Task 内部存在
   nav→pick→nav→place 顺序阶段，但这是 **单 agent 技能序列**，不是跨
   CoordinationContext 的 Mission 耦合；Mission 级仍标 Independent，阶段结构
   记入 expected_phase_structure。
6. **Embodiment/assignment 约束**：`robot_at(target, agent_i)` 类谓词把 goal
   绑到具体 benchmark robot 实例——RoboGuide 侧对应 **Mission Actor/Role
   placement / identity constraint**（deployment-owned），不是 capability
   contract（与 RNS-SEMANTIC-COVERAGE.md R7 一致）。

机器可读版本：`workload-taxonomy.json`。

## Census 修订（2026-09-16）

依据 `DATASET-INVENTORY.md`（dataset-inventory.json）对本表做如下修正：

- task spec 文件共 13 个，但**仅 9 个被实际 config 引用**；
  `multi_agent_social_nav`、`multi_agent_tidy_house`、`drone_spot_rearrange`
  （含 `_default` 变体）无任何 config 使用，不进入 episode 统计。
- 本机存在的 evaluation datasets 共 **8** 个，unique evaluation episodes
  **931**（14 个 config 的 raw sum 为 961——`multi_agent_eval.json.gz` 30 集
  被 multi_agent_man 与 multi_agent_per 共享，只计一次）。
- **dataset 缺失**：`hssd_dist.json.gz`、`hssd_man_fig.json.gz`、
  `hssd_per_fig.json.gz` 本机不存在 → 3 个 reverse 变体与 2 个 fourth 变体
  （共 5 config）本地不可运行。
- Protocol B：直接可跑 **99**（spot_fetch_mobility，已实证）；仅缺 perception
  **297**；仅缺 manipulation（pick/place）**565**。
- 原 "12 个 task spec" 的说法以此修订为准。
