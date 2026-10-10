# E1 单个配对进程入口

`roboguide-eval e1-pair` 消费 Population Manifest 中一行，将原生 EMOS 和生产
RoboGuide B1 各执行一次。它是 `e1-batch` 的 process driver，不属于 Core、MI 或
Local EAIOS 的决策权威。实际结果和本机配置不提交 Git。

```bash
uv run roboguide-eval e1-pair \
  --population /absolute/frozen/population.json \
  --config /absolute/frozen/worker.json \
  --pair-id pair-0000 --output /absolute/new/pair-0000
```

输出目录必须不存在。偶数 population ordinal 先 EMOS，奇数先 RoboGuide；顺序在
调用前确定。两个 arm 在一个 pair 内串行，分别拥有 `vendor-cwd`、日志、Provider
body archive 和结果目录。每臂复制冻结的三个 vendor 代码目录，不使用指向外部配置的
目录符号链接，因而遵守生产 B1 的 checkout 内配置约束。dataset/资产保持读取引用。
启动前后重算冻结源与私有代码副本摘要；runtime origin 必须指向该臂准确的副本路径
并匹配冻结字节，外部库仍绑定原始解释器/路径。入口不提供操作系统沙箱或对外部
程序的文件写权限隔离保证。
受控 Stage2 的基础清单始终记录 Habitat-Sim 编译扩展的真实路径和文件摘要；
导航与搬运均不依赖可选 reset-route diagnostics 才能获得这项身份。缺失或读取失败
继续 fail closed，不能以包版本或父进程预检替代实际子进程证据。
原生 CWD 下的日志、视频、Hydra/TensorBoard 输出与其他 worker 隔离。

## 冻结 worker 配置 v0.1

`schema_version=roboguide.e1.pair-worker/v0.1`。必填字段如下：

| 字段 | 含义 |
| --- | --- |
| `population_digest` | 完整 Population Manifest 的 canonical digest |
| `code_root`, `code_sha` | 干净代码视图绝对路径与完整提交；发布者先核对源码树并在该视图构建 binaries |
| `vendor_root` | 未修改的原生 EMOS 代码/资产视图，已存在的 vendor patches 必须披露并冻结 |
| `habitat_python` | 独立 Habitat Conda 的准确 Python 可执行文件 |
| `dataset_path`, `input_directory` | 原始 dataset 文件和每个 `<pair_id>.json` 冻结 B1 input |
| `native_config` | 原生 `habitat-baselines/.../config/` 下相对配置路径 |
| `b1_runner`, `mission_config` | RoboGuide 代码视图内的相对路径 |
| `gpu_device`, `ports` | GPU 编号；七个不同端口：proxy/grpc/controller/artifact/endpoint_a/endpoint_b/mission |
| `provider_upstream` | 无凭据、query、fragment 的原授权 Provider base URL |
| `arm_timeout_seconds` | 单 arm 墙钟预算；到限后尽力归档并清理拥有的进程 |
| `mi_observation_seconds`, `mission_observation_seconds` | Runner 既有单请求观察和 Mission 观察预算 |
| `provider_timeout_seconds` | 转发器的 per-call 上界，冻结时与原始 client 预算一起记录 |
| `source_sha256` | 所有冻结公共文件的绝对路径到原始字节 SHA256；禁止 `.env` |
| `identity_files` | task_spec/habitat_config/benchmark_authority/stage2 四组公共文件标识到绝对路径 |
| `runtime_modules` | 实际 runtime module 名称到冻结源文件路径；检查子进程解释器、origin 和 digest |

必须 gate dataset、Python、Controller/Node binary、Runner、两类配置、B1 workload；
发布者还应冻结全部 Python/Rust/Prompt/schema/部署配置及 vendor 依赖面。前三组
identity 使用对应实际文件摘要映射的 canonical digest；Stage2 使用逐文件 digest。
这些值必须与 Population Manifest 一致，不能把声明直接冒充运行观测。

凭据仅由已授权 `OPENAI_API_KEY` 环境传入。入口清除继承的 B1/Habitat/MI/OpenAI
实验开关，再从冻结配置生成本轮环境。MI base URL 只替换为本轮原字节转发器，
模型仍来自 population，生产 MI 的 reasoning/Prompt/预算来自冻结 mission config。
原生 Leader/Stage2 参数和 SDK retry 保持原代码；Responses 与 Chat API 独立留证。

## 原生 workload 与观测

独立 Conda 进程调用 `habitat_local_eaios.native_workload`。可序列化 factory 在原生
worker 中核对原始 dataset 文件、episode/scene 唯一性，使用原生 `filter_episodes`
在 `Env.__init__` **之前**保留准确的原始 episode 对象。原构造函数仅调用一次。
不通过 seed 选择 episode，不先加载错误场景，不编辑 dataset、起始位姿或 vendor。

调用原生 evaluator 的一个 environment/episode；其既有自动 reset 可以继续发生，
但第一次真实返回的初始化和第一 episode 终态不可被后续 reset 覆盖。观察器不添加
reset/action/step/RNG 调用。异常仍原样传播；提前关闭或未执行的 episode 官方结果
保持 unavailable。终态值来自原 `Env.step` 后已计算的 `Env.get_metrics`。
原生 RL wrapper 可以在 base `Env.episode_over` 仍为 false 时因官方成功返回 done。
观察器同时读取原始 `RLEnv.step` 已返回的 done/info，在 VectorEnv 原有自动 reset
之前保存首个 episode 终态；不再次调用 `get_done`、不追加 step/reset，也不把 metric
为 true 单独当作 episode 已结束。后续 reset 不能覆盖首 episode 的证据。
每 32 次成功返回的原始 step 保存一个小型 `native-progress.json`，reset/终态也保存。
原生父进程异常可能直接终止 VectorEnv worker，不能假设其 `close` 必然调用。终态
文件缺失时，进度只证明已执行步数下界；最终步数、终态和官方结果保持 unavailable。
缺失或身份不匹配的进度不会被解释成“从未执行”。诊断/metric 读取异常分别隔离，
不会阻止保存其他可获得的边界事实或改变原始异常与物理执行结果。
物理 reader 没有批准的逐谓词读取能力时仍 unavailable；不能用该缺口替代官方 metric。

## 证据与结果

每个 arm 保存完整、脱敏的 Provider request/response、调用元数据、accounting、
launch、process result、run pairing evidence 和 arm summary。body observer 最多
2048 calls、每 body 8 MiB、总 512 MiB。原 HTTP 字节不变；只脱敏证据副本，不记录
headers。关闭、请求响应数量、drop/write failure、in-flight 一起决定归档完整性。
原生/受控日志流式脱敏；缺失或损坏的已有 JSON 不会作为成功证据。

`pair-manifest.json` 复用现有 Pair Validator。`reset_comparison` 另核对两个实际
post-reset episode/scene/seed、机器人 position/rotation、目标位置与 goal 标识；
equal seed 不能证明 equal start。可选 semantic-region gaps 不会伪装成 pose，也不
影响已观测 pose 的比较。缺少真实字段为 unavailable，真实差异暂停新派发。

Protocol A 必须披露 RoboGuide Guard/tool binding、反馈、completion/idle 等差异。
`local_execution_profile` 与 actual reset 额外事实进入 pair evidence；现有 validator
不把额外 reset 自动升级成其正式维度。driver 的 `comparison_eligible` 要求 validator
通过、actual reset matched，且不存在致命 source/配置/归档错误。它不修改 B1
provenance、Formal 或 benchmark admission；配对不可比较不会擦除真实官方结果。

MI 拒绝、技能失败、官方 false 都保留原始结果并继续。官方值缺失不会转换成 false。
原生链路在已核实 reset 后的策略异常记为 SUT Failed；非零进程退出本身不能证明
Provider 或 harness 失败。真实 HTTP 失败和独立采集/身份错误仍按各自证据归因。
配置/模型/source 漂移、归档损坏、listener 清理失败暂停；单个 502/transport failure
归为 infrastructure 并留证，由 batch 既定连续基础设施失败政策决定暂停。
出口 `0` 仅表示 driver 已保留一次结果，不表示 SUT/benchmark 成功。

GPU 采样每五秒读取所选 GPU 的计算和图形进程内存，只累计本 arm 的准确 session；
Graphics-only Habitat 不能因为未出现在 compute-apps 列表中而被记作零。
同 PID 的相同条目去重，未知、矛盾或超出响应预算的读取记录 unavailable。
这些读数仅作为 batch capacity 证据；它不
调度或终止其他 GPU owner。原生视频仍使用原 evaluator 的缓存和步数上界，其 CPU
内存及时长需在首 pair 测量后评估。不能仅用显存充裕推断四个 worker 已安全。
启动与重启互斥来自 `e1-batch` durable claim，不通过重新调用已消费 pair 获得成功。
