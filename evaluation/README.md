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
- **Canonical metrics**（`roboguide-eval.metrics/v0.1`）：注册表当前包含 E1
  核心指标（success、subgoal_success、simulation_steps、token_usage、
  wall_time、coordination_latency、invalid_assignment_count）与后续实验预留
  （scheduling/recovery latency、resource conflicts、deadline miss、state
  freshness、control-plane overhead）。runner 产出的
  `metrics.json` 保留 raw evidence 引用；未知指标名拒绝入库。

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
episode 字段语义是"确定性选择方案的标签"。EMOS 的 `pddl_success` 由
`EmosRunner` 从 stdout 的 `Average episode ...` 日志行转换（均值 ≥ 0.5 记为
success），其余 EMOS 指标原样保留在 `details`；`wall_time` 在系统未自报时由
harness 用实测进程时长回填，并在 `details.wall_time_source` 标注来源。

> ⚠️ 数据阻塞：mobility 任务的 99 个 episode 分布在 18 个 MP3D 场景，而当前
> 只下载了免申请示例场景（且不在该 episode 集内）。此任务正式运行需等待
> Matterport ToU 批复并下载全量 MP3D（约 130GB）；spec 的 dataset digest 已
> 按 `mobility_episodes_1.json.gz` 固定。

## 当前状态与停止边界

- 真实实现：进程管理（超时/终止/日志持久化/进程组清理）、spec 解析与校验、
  digest、manifest/metrics/trace 基础设施、CLI（含 doctor 凭据检查）、
  canonical metric 合同、EMOS `Average episode` 日志行到 canonical metrics 的
  转换、`wall_time` 回填、环境变量值的 `{model}` 占位符替换。
- Fixture / 待接（按任务边界有意保留）：EMOS 官方命令的正式参数映射
  （现由 local.yaml 模板承载）、RoboGuide Controller 路径驱动、Habitat
  bridge。命令映射只需编辑 `local.yaml`，不需要改生命周期与合同。
