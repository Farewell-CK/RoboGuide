# RoboGuide Eval Harness

独立实验评估基础设施（experimental evaluation infrastructure），用于在
Habitat-MAS / EMOS 等外部系统与 RoboGuide 之间进行可复现、可追溯的对比实验。
当前是第一版骨架：**实验编排 + 进程边界 + 可复现结果基础设施**，并以
E1（Habitat-MAS Mobility，EMOS vs RoboGuide）作为第一条 workload。

## 边界（必须遵守）

- Eval Harness 不属于 RoboGuide Core、Runtime、Control Plane、State & Memory
  Plane 或任何 Local EAIOS；它不修改 Proposal / Commit / Binding / Runtime 语义。
- Harness 不 import、不复制、不重实现 EMOS / Habitat-MAS / RoboGuide 的内部
  逻辑；外部系统只通过进程边界（subprocess）访问。
- 不把 Habitat/EMOS 依赖加入 RoboGuide Python 环境；Harness 与 EMOS 各自
  保留独立环境。
- `RoboGuideRunner` 后续必须经由真实 Controller / Node Protocol / Runtime
  路径执行，不允许绕过 RoboGuide 直接调用 Habitat skill 拿结果。
- Harness 不定义 Habitat-MAS 如何判定 task success；各系统 runner 只把各自
  原始输出转换为 canonical metrics，并保留 raw evidence。
- 真实 results、本机 `local.yaml`、数据集与模型文件不提交 Git。

## 目录结构

```text
evaluation/
├── specs/e1/mobility-smoke.yaml   # E1 smoke 实验定义（可移植、无机器信息）
├── local.yaml.example             # 本机配置模板（复制为 local.yaml，不入库）
├── src/roboguide_eval/
│   ├── models.py                  # ExperimentSpec 领域模型与 canonical 序列化
│   ├── config.py                  # spec 加载/校验、digest、本机配置、ProcessSpec 解析
│   ├── metrics.py                 # canonical metric 注册表与 MetricsPayload 合同
│   ├── process.py                 # 进程管理器（argv、env、timeout、terminate/kill）
│   ├── runner.py                  # SystemRunner 生命周期与实验编排
│   ├── results.py                 # RunManifest、run 目录、trace、汇总
│   ├── cli.py                     # doctor / run / summarize
│   └── systems/                   # emos.py / roboguide.py 命名 runner
└── tests/                         # 离线、确定性测试（fixture 子进程）
```

## 核心概念

- **ExperimentSpec**（`specs/**/*.yaml`，`roboguide-eval.experiment-spec/v0.1`）：
  描述实验本身 —— experiment id、benchmark、task、episode 选择、systems under
  test、LLM provider/model/reasoning、seeds、metrics、timeout、数据集身份与
  机器人上下文元数据。canonical JSON 序列化产出稳定 digest，写入每次 run 的
  manifest。
- **EnvironmentSpec / ProcessSpec**：spec 只携带可移植期望（期望产物、超时、
  metrics 来源路径）；工作目录、可执行文件、参数、Conda 环境、凭据全部来自
  `evaluation/local.yaml` 或 `ROBOGUIDE_EVAL_*` 环境变量，解析时合并
  （spec < local config < 环境变量）为最终 ProcessSpec。配置了 Conda 环境时，
  argv 自动包装为 `conda run --no-capture-output -n <env> ...`，不需要交互式
  `conda activate`。
- **SystemRunner**：统一生命周期 `prepare -> run_episode -> collect_result ->
  cleanup`。当前 `EmosRunner` / `RoboGuideRunner` 是真实进程执行 +
  fixture 命令映射：启动/观察由本机配置提供的命令，尚未接官方 EMOS 命令映射
  与 RoboGuide Controller 路径。
- **RunManifest**（`roboguide-eval.run-manifest/v0.1`）：每个 run 的唯一完整
  身份 —— experiment/system/benchmark/task/episode/seed、LLM 配置
  （含预留的 `reported_model`，用于记录 API 实际返回的 model identifier）、
  run id、RoboGuide git SHA、外部系统版本、数据集身份/digest、config digest、
  起止时间、完整命令、退出状态、失败原因与环境信息。
- **Canonical metrics**（`roboguide-eval.metrics/v0.2`）：注册表当前包含 E1
  核心指标（success、subgoal_success_rate、simulation_steps、token_usage、
  wall_time、coordination_latency）与预留指标（assignment 三项、scheduling/
  recovery latency、resource conflicts、deadline miss、state freshness、
  control-plane overhead）。runner 产出的 `metrics.json` 保留 raw evidence
  引用；未知指标名拒绝入库。**v0.1 → v0.2 迁移**（尚无正式论文数据）：
  `subgoal_success`（boolean）改为 `subgoal_success_rate`（number，[0,1]，
  带范围校验）——布尔无法表达部分 subgoal 完成；`invalid_assignment_count`
  移除，由 reserved 的 assignment 三指标替代（定义见下）。

## Episode identity（selector ≠ resolved）

E1 是 paired comparison：同一个 Habitat-MAS episode 必须分别由 EMOS 和
RoboGuide 执行。因此 manifest 的 `episode_selection` 区分两层：

- **selector**：spec 的 episode 标签（如 `seed-pinned-sample`）+ seed ——
  描述"如何选取"，不是 episode id；
- **resolution**：从官方输出解析出的真实身份——`resolved_episode_id`、
  `resolved_scene_id`、`dataset_index`、`dataset_record`、`evidence_source`
  列表与 `status`（`resolved` / `multiple-episodes` / `unresolved` + reason）。

回答"EMOS run X 和 RoboGuide run Y 是否同一 benchmark episode"看
`resolved_episode_id` + `dataset_record`（配合 spec 的 dataset digest）。
**不伪造**：解析不到就 unresolved/partial 并记录原因。

**scene/index 的来源（pinned dataset resolver）**：在 local.yaml 为系统配置
`dataset_path` 指向 pinned episodes 文件后，runner 只读解析（gzip+JSON，不
import Habitat/EMOS），并先校验文件 SHA-256 与 spec 的 dataset digest 一致；
episode id 在 dataset 内唯一时给出 `resolved_scene_id` / `dataset_index` /
`dataset_record`，digest 不匹配、id 缺失或重复时给出显式 `dataset_status`
（`digest-mismatch` / `not-found` / `ambiguous`），不猜。未配置 `dataset_path`
时 `dataset_status: not-configured`，scene 保持 null。

## EMOS 官方输出与 metric 来源

| canonical metric | 官方来源 | 说明 |
| --- | --- | --- |
| success | evaluator 摘要 `Average episode pddl_success:`（**stderr**，logger 格式） | stdout+stderr 双流合并解析；仅单 episode run 映射为布尔（均值≥0.5）；批次保留均值在 details，不布尔化 |
| subgoal_success_rate | 官方 `pddl_stage_goals.<stage>_success` 聚合的均值 | mobility 的 stage-goal measurement（经 `composite_stage_goals` 配置节点注册）在 evaluator 摘要中输出 per-stage 成功率；其均值为 rate，原始 stage 值保留在 `details.emos_stage_goal_success` |
| simulation_steps | stdout `Episode ID: <id>, Num Steps: <n>` 官方横幅 | habitat `Env.log_episode_steps` 在 env reset 时打印 |
| token_usage | `chat_history_output/<date>/<config>/<ablation>/<episode_id>/token_usage.json` | EMOS 自带 per-agent 实际 usage total（`MultiLLMPolicy.act` 构造的嵌套布局，默认开启）；按 prepare 快照差异定位本次新写的文件，复制进 run 目录 `raw-evidence/` 并记录 source path |
| wall_time | Harness 实测进程时长 | `details.wall_time_source = harness_process_duration` 标注来源 |

> **evaluator 摘要在 stderr**：habitat-baselines 的聚合摘要走 Python
> `logging`（默认 stderr，带时间戳前缀），横幅走 `print`（stdout）——
> 两者都已对真实 run 验证，解析器合并双流。

> **token 成本细分是 harness 侧记账职责**：EMOS 只记录 per-agent
> `total_tokens`；input/output/cached/reasoning 的细分由 **EMOS 侧最小记账
> 插桩**提供——`habitat_mas/utils/models.py` 中每个 LLM 调用通过
> `_record_usage` 把完整 `usage` 对象 + 时间戳 + 延迟追加写入 episode 目录
> 的 `token_usage_details.jsonl`（instrumentation only：不触碰 task
> assignment / reflection / execution 任何算法路径，且 RoboGuide 臂将来复用
> 同一 CrabAgent 栈时测量自动对称）。EmosRunner 按快照差异定位本次新写的
> 明细文件，复制进 run 目录 `raw-evidence/` 并记录 source path，聚合成
> canonical 细分（`token_input_usage` / `token_output_usage` 直接可得；
> `cached_prompt_tokens` / `reasoning_tokens` 在上游 usage 携带明细时可得，
> 否则 unavailable 并说明原因）。
>
> 本地记账代理（`roboguide-eval proxy`）保留为**诊断工具**：不改 baseline
> 的透明转发 + 落盘，用于排查中转问题或交叉核对——`--upstream` 必须是不带
> `/v1` 的根地址，`OPENAI_BASE_URL` 指向 `http://127.0.0.1:<port>/v1`。
> TTFT 在 baseline 非流式调用下不可观测，如实不记录（插桩记录的是每次调用
> 的请求/响应时间戳与总延迟）。
>
> **成本分析语义**（`cached ⊆ input`）：上游语义中 `cached_tokens` 是
> `prompt_tokens` 的**子集**（canonical `token_input_usage` 包含缓存命中
> 部分）。有效输入成本 = `(input − cached) + cached × 缓存单价`；直接拿
> `token_input_usage` 乘全价会重复计费缓存部分。实测参考：mobility smoke
> 一次 run 的 input 41436 中 18432（44.5%）为缓存命中——两臂/多 run 对比
> 时必须披露缓存命中差异或按有效成本归一。批次 run（一次进程跑多个
> episode）的 per-episode token 归因暂缓，五个 token 字段显式 unavailable。

episode identity 的解析顺序：stdout 官方横幅（首选）→ `episode_log/**/
*_steps_log.json` 相对上一 run 后刷新的 baseline 的新增记录（回退，每个 run
只归属自己新增的记录）→ scene/index 由 digest 校验后的 pinned dataset 补全。
横幅时机已对源码验证：habitat `VectorEnv` 默认 `auto_reset_done=True`，
episode done 的那次 step 自动 reset 并打印刚结束 episode 的横幅——**包括
最后一个/唯一一个 episode**；回退路径保留作非标准执行栈的纵深防御。

token 定位防串场：同一 episode id 的历史 token 记录（不同日期目录）永远
不作为本次证据——只有相对 prepare 快照新建或变更的文件才算，多于一处变
更时显式 ambiguous，找不到时 unavailable 并说明原因。

**可用性策略**：没有可靠官方来源的 metric 一律缺失，不填 0、不猜测，原因
记录在 `details.unavailable_metrics`。此外 runner 写入前强制执行 canonical
合同校验（名称/类型/bounds）：违反合同的 payload 不会落盘为不可读数据，而是
降级为空 values + `details.metrics_validation_error` + trace `metrics_invalid`
事件，run 的 manifest/日志/证据全部保留。

## coordination_latency 测量边界（冻结，采集前需协议确认）

E1 controlled comparison 的协调延迟**只**测量全局协调阶段：

- **EMOS**：从开始 global coordination / group discussion 到 final
  assignment 确定；
- **RoboGuide**：从一个已 ready 的 Task/Role requirements 进入全局协调，到
  assignment 完成 Commit。

不计入：simulator reset、本地机器人执行、navigation、Habitat skill runtime。
Mission Intelligence 是否计入该指标**未决**——正式采集前必须先冻结协议；
在此之前该 metric 保持 unavailable（runner 自动标注）。

## Assignment 指标定义（reserved，测量要求）

原 `invalid_assignment_count` 定义过模糊，已移除。替代三项均为 reserved，
在能可靠提取事件前不采集：

- `initial_infeasible_assignment_count`：初始 coordinator proposal 中被目标
  robot/local agent 判定 capability-infeasible 的 assignment 数；
- `final_infeasible_assignment_count`：最终进入执行但 capability 上不可执行
  的 assignment 数；
- `reassignment_count`：因 assignment 拒绝 / feasibility conflict 发生的重新
 分配次数。

**测量要求**：需要从 EMOS 的 Leader Assignment → Agent Reflection →
Reject/Reassign 环节可靠提取带 identity 的结构化事件。当前审计结论：这些
环节没有结构化日志输出（仅散落在对话文本中），无法可靠提取——若要采集，
需要在 benchmark integration 层加最小 instrumentation（带 assignment id 与
拒绝原因的事件输出），且不得改动 EMOS 算法逻辑。

## 结果目录

每次 run 生成独立目录（真实 results 不提交 Git）：

```text
results/<experiment-id>/<run-id>/
├── manifest.json    # 完整可复现身份
├── metrics.json     # canonical metrics + raw evidence 引用
├── trace.jsonl      # 结构化事件轨迹
├── stdout.log       # 外部进程 stdout（失败也保留）
└── stderr.log       # 外部进程 stderr（失败也保留）
```

外部进程失败（非零退出、超时、无法启动）不会销毁证据：manifest 记录退出码与
failure_reason，stdout/stderr 持久化，metrics 允许为空。

## 使用

```bash
# 环境体检（不运行 episode；含 ${VAR} 凭据可解析性检查）
uv run roboguide-eval doctor --spec evaluation/specs/e1/mobility-smoke.yaml

# 运行一个系统（命令映射由本机配置提供）
uv run roboguide-eval run --spec evaluation/specs/e1/mobility-smoke.yaml --system emos

# 覆盖 episode / seed
uv run roboguide-eval run --spec ... --system emos --episode seed-pinned-sample --seed 7

# 汇总
uv run roboguide-eval summarize --results evaluation/results
uv run roboguide-eval summarize --results evaluation/results --json
```

### 配置现有 EMOS Conda 环境

1. `cp evaluation/local.yaml.example evaluation/local.yaml`（模板已按
   emos-baseline 交接文档填好结构：workdir、`habitat` 环境、GPU 1、中转
   endpoint、`EMOS_LLM_MODEL: "{model}"` 跟随 spec）；
2. 运行前导出凭据：`set -a; source /data/workspace/code/emos-baseline/emos.env; set +a`
   （或在 shell 中 export `OPENAI_API_KEY`）；doctor 会提前发现缺失的引用；
3. 凭据使用 `${OPENAI_API_KEY}` 引用，spawn 时从环境解析，manifest 只保留
   占位符，不会写入真实 key；
4. 也可用环境变量临时覆盖：
   `ROBOGUIDE_EVAL_EMOS_WORKDIR` / `ROBOGUIDE_EVAL_EMOS_EXECUTABLE` /
   `ROBOGUIDE_EVAL_EMOS_CONDA_ENV`。

### EMOS episode 固定（已对 habitat-lab 源码验证）

当前 habitat-lab 版本**没有 episode_ids 过滤**，确定性来自两个手段：

- `habitat.seed={seed}` + `habitat.environment.iterator_options.num_episode_sample=1`
  ——固定 seed 确定性地采样出恰好一个 episode（注意 seed 必须非零，iterator
  忽略 seed 0）；这是 smoke spec 的默认映射，episode 多样性来自 seed 网格；
- `habitat.dataset.content_scenes=["<scene_id>"]` ——场景级过滤，一个进程跑该
  场景的全部 episodes 作为一批。

一个 EMOS 评估进程总是跑完整个（过滤后的）episode 集合，所以 harness 的
episode 字段语义是"确定性选择方案的标签"（selector）；run 实际执行的
episode 身份由 `EmosRunner` 从官方输出解析后写入 manifest 的
`episode_selection.resolution`（见上文 Episode identity 一节）。EMOS 的
`pddl_success` 由 `EmosRunner` 从 stdout 的 `Average episode ...` 日志行转换
（仅单 episode run 映射为布尔，均值 ≥ 0.5；批次不布尔化），其余 EMOS 指标
原样保留在 `details`；`wall_time` 在系统未自报时由 harness 用实测进程时长
回填，并在 `details.wall_time_source` 标注来源。metric 来源总表见上文
"EMOS 官方输出与 metric 来源"。

> ✅ 数据就绪（2026-09-09 验证）：MP3D ToU 已批复、90 栋全量场景在位
> （21GB，habitat 仅需 glb 子集），mobility 99 集引用的 **18 个场景全部
> 就绪**（目录 + glb 双确认），`mp3d.scene_dataset_config.json` 在位；
> smoke 已实际跑通 episode 20。robot 配置说明：`robot_configs/mp3d/
> mobility_episodes_1.json` 为手工补的 alias（作者包只有 9 集，alias 补至
> 10 集，其余 89 集无条目）。已对源码确认其影响：mobility 配置
> `randomize_agent_start: 1`，agent 每集由 simulator RNG（受
> `habitat.seed` 驱动）随机放置——缺失条目**不会崩溃**（直接索引分支在
> randomize 分支的 else 里，不会到达），配置条目仅影响 LLM 的场景文字
> 描述；同 seed 可复现同一初始状态，两臂同 seed 对比不受影响。

## 当前状态与停止边界

- 真实实现：进程管理（超时/终止/日志持久化/进程组清理）、spec 解析与校验、
  digest、manifest/metrics/trace 基础设施、CLI（含 doctor 凭据检查）、
  canonical metric 合同 v0.2（含范围校验与 unavailable 机制）、episode
  identity 解析（官方横幅 + episode_log 回退）、EMOS 官方输出的 canonical
  转换（success/simulation_steps/token_usage + unavailable 标注）、
  `wall_time` 回填、环境变量值的 `{model}` 占位符替换。
- Fixture / 待接（按任务边界有意保留）：EMOS 官方命令的正式参数映射
  （现由 local.yaml 模板承载）、RoboGuide Controller 路径驱动（保持
  placeholder，等 Task/Role/ExecutionIntent 语义人工冻结后接 Habitat
  bridge）、Habitat bridge。
- Benchmark 侧观察（已对源码证伪的旧担忧，记录结论防止复发）：此前怀疑
  `Env.log_episode_steps` 访问 `measures['pddl_stage_goals']` 会在 mobility
  上 KeyError。实际验证：mobility measurements 列表中的
  `composite_stage_goals` **就是** `PddlStageGoals` measurement（config-store
  名、package 键 `pddl_stage_goals` 与 measure uuid 三者不同名），已注册
  且被 HANDOFF 验证过的 social_rearrange 使用同一列表并成功写出
  subgoals 文件——**无 KeyError，无需任何 override**。mobility 的 pddl
  spec 也定义了 `stage_goals`，per-episode stage 数据会正常落
  `episode_log/**/*_subgoals.json`（subgoal metric 接入留待后续轮次）。
