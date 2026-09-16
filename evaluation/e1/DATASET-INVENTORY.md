# E1 Dataset Inventory — EMOS/Habitat-MAS Multi-Agent Census

Census method: 每个 benchmark config 的 defaults 链 + **inline
`habitat.dataset.data_path` override** 逐条解析到实际 `.json.gz`，episode/scene
数量直接从文件内容统计（含 sha256），不依据 README/命名。审计日数据见
`dataset-inventory.json`（机器可读，experiment planner 直接消费）。

## 关键计数结论

| 项 | 数 |
| --- | --- |
| PDDL task spec 文件 | 13（`benchmark/multi_agent/pddl/`） |
| 被实际 config 引用的 spec | **9**（4 个未被任何 config 引用：social_nav、tidy_house、drone_spot_rearrange ×2） |
| benchmark config（含 reverse/fourth 变体） | 14 |
| 本机存在的 evaluation datasets | **8** |
| raw episode 总和（14 config 口径） | **961** |
| unique evaluation episodes（去重 multi_agent_eval 共享） | **931** |
| Protocol B 直接可跑 | **99**（仅 spot_fetch_mobility） |
| 仅缺 perception | **297**（height_per 113 + spot_drone_per 154 + multi_agent_per 30） |
| 仅缺 manipulation（pick/place） | **565**（dist_man 89 + height_man 166 + multi_agent_mobility 100 + fetch_stretch_man 180 + multi_agent_man 30） |
| dataset 缺失阻断的 config | **5**（3×reverse + 2×fourth；本机 0 episode 可数） |

## Dataset × Config 矩阵

| dataset file | eps | scenes | split | 使用方 |
| --- | --- | --- | --- | --- |
| mp3d/mobility_episodes_1.json.gz | 99 | 18 | eval | **spot_fetch_mobility**（E1-I 现役，episode 51 已实证） |
| mp3d/mp3d_episodes_1.json.gz | 100 | 27 | eval | multi_agent_mobility（Spot+Fetch+Drone，3 agents，5000 步） |
| hssd/0/hssd_dist_man.json.gz | 89 | 32 | eval | dist_man（Fetch+Stretch，4000 步） |
| hssd/0/hssd_height_man.json.gz | 166 | 34 | eval | height_man（Fetch+Stretch，4000 步，跨楼层） |
| hssd/0/hssd_height_per.json.gz | 113 | 31 | eval | height_per（Spot+Drone，4000 步） |
| replica_cad/two_agent_manipulation_eval.json.gz | 180 | 79 | eval | fetch_stretch_man（750 步） |
| replica_cad/two_agent_perception_eval.json.gz | 154 | 69 | eval | spot_drone_per（750 步） |
| replica_cad/multi_agent_eval.json.gz | 30 | 26 | eval | multi_agent_man + multi_agent_per（**共享，4 agents，勿重复计数**） |
| ~~hssd_dist.json.gz~~ | — | — | — | 缺失：dist_man_reverse / height_man_reverse / height_per_reverse |
| ~~hssd_man_fig.json.gz~~ | — | — | — | 缺失：fourth_man |
| ~~hssd_per_fig.json.gz~~ | — | — | — | 缺失：fourth_per |

## Splits 与未使用数据说明

- 全部 8 个在用 dataset 的 split 均为 **eval**（group 默认或 alias 声明），
  无 train/val 混算。
- `mp3d/mp3d_episodes.json.gz`（500 eps）是 habitat-lab `dataset_mobility`
  alias 的默认目标，但 **14 个 config 全部 inline override**，该文件不参与
  任何统计（避免 99 vs 500 的口径混淆；spot_fetch_mobility 的 episode 51 在
  两个文件中语义不同——mobility_episodes_1 的 51 才是 pRbA3pwrgk9 场景）。
- `replica_cad/single_agent_eval.json.gz` 仅被 single_agent 基准使用，
  不在本轮 multi-agent 范围。

## assignment constraint 明细

- **swappable**：spot_fetch_mobility、height_per、spot_drone_per（`any_at`/
  `is_detected` agent 无关）
- **swappable per object**：dist_man、height_man、multi_agent_mobility、
  fetch_stretch_man（`at(obj, goal)` 任何可搬运 robot 皆可）
- **agent-bound**：multi_agent_man、multi_agent_per（`robot_at(..., agent_i)`
  谓词把目标钉死到具体 agent；RoboGuide 侧需以 capability contract 表达）

## Protocol B 缺口映射

| 缺口 | episodes | 需要的 Local How 集成 |
| --- | --- | --- |
| 仅 navigation（现役） | 99 | 已就绪（shared-world 2/2 实证） |
| + perception | 297 | 感知 workflow（`is_detected`/robot sensing 的 Local EAIOS 观察与判定） |
| + manipulation | 565 | pick/place 技能的 declarative workflow 接入（arm 动作 + not_holding 判定） |

同一 episode 不会同时落在两个缺口集（`at()` 类归 manipulation，
`is_detected` 类归 perception；multi_agent_per 的 `robot_at` 记 perception）。
