# E1 后台对照进程监督

`roboguide-eval e1-batch` 只管理外部进程和证据，不导入 EMOS/Habitat，不提交 Mission，
不裁决官方成功，也不替代 Pair Validator。运行程序负责保存原始 arm 结果和真实 reset 对照。
本机路径、凭据来源及实验结果仅放在 Git 忽略目录；不要上传密钥或 Authorization header。

```bash
uv run roboguide-eval e1-batch start --manifest /absolute/local/queue.json --output /absolute/local/batch
uv run roboguide-eval e1-batch status --output /absolute/local/batch
uv run roboguide-eval e1-batch stop --output /absolute/local/batch
uv run roboguide-eval e1-batch resume --output /absolute/local/batch
```

`start` 要求新的输出目录，冻结完整 manifest。`stop` 暂停新派发，允许已运行的 pair 结束并归档；
`resume` 观察原有进程，不重试已认领的 pair，也不清除严重错误。每个 worker 在启动命令前
创建独占 claim 并持久化 Linux boot/start 身份。丢失的已认领 worker 明确记为 harness failure，
不自动重跑。突然断电或无法保存 receipt 时保持 fail closed，需人工检查遗留进程。

manifest schema 为 `roboguide.e1.batch/v0.1`，字段如下：

| 字段 | 含义 |
| --- | --- |
| `jobs[]` | 按预注册顺序的 `job_id`、`argv`、绝对 `cwd`、独立绝对 `outcome_path` |
| `jobs[].environment` | 公共配置；敏感变量只能引用 `${ENV_NAME}`，不保存实际值 |
| `jobs[].ports` | 各 job 互不重叠的端口列表 |
| `jobs[].timeout_seconds` | 每 pair 外层 wall budget，默认 36060 秒，最多 172800 秒 |
| `source_sha256` | 公共文件绝对路径到 SHA256；启动前后核对，不包含凭据文件 |
| `capacity` | `max_workers` 为 1 或 2，以及 GPU 总容量、基线占用、可用比例 |

外部命令只执行一次，结果 JSON 必须有同一 `job_id`、布尔
`infrastructure_failure` / `fatal_failure` 和 `arms` 原始判定。
`resource_usage.both_arms_measured=true` 且有限、正值 `peak_job_gpu_mib` 才允许考虑双 worker：
`gpu_baseline_mib + 2 * peak_job_gpu_mib <= gpu_fraction * gpu_total_mib`。
第一个 pair 始终串行。缺失 GPU 观测保持串行，不从成功率推导容量。

普通 MI 拒绝、部署 hold、Guard 拒绝、导航失败及官方 false/unavailable 均继续；
单次外部基础设施失败归档后继续，连续三个不同 pair 的基础设施失败暂停。
身份、reset 或共用归档缺陷由 driver 标记 fatal，暂停新派发，保留全部原始文件。
队列不把 unavailable 转成 false，不把 Mission Completed 转成官方 true；Formal 分母读取
原始 admission verdict，不因普通系统失败移除。严格配对公平性由现有独立 validator 判断，
监督器始终不宣称 strict fairness。

`status.json` / `SUMMARY.json` 为派生状态，`jobs/<id>/receipt.json` 保存认领证据，
`stdout.log` / `stderr.log` 流式脱敏，`result.json` 保存结果及日志关闭状态。
文件写入使用原子替换和 fsync；日志失败单独归因 harness，并暂停后续派发。

B1 launcher 可配置 `ROBOGUIDE_B1_CONTROLLER_GRPC_PORT`、`ROBOGUIDE_B1_CONTROLLER_PORT`、
`ROBOGUIDE_B1_ARTIFACT_PORT`、`ROBOGUIDE_B1_HABITAT_PORT`、`ROBOGUIDE_B1_HABITAT_PORT_B`、
`ROBOGUIDE_B1_MISSION_PORT`。六个端口必须合法且不同；由同一配置渲染 Node/Mission 配置、
采集 URL 和归档 URL。它不改变 capability、资源、调度或任务目标。

`ROBOGUIDE_B1_MISSION_OBSERVATION_BUDGET_SECONDS` 单独配置 Mission 执行阶段的 Runner
观察等待，默认 1800 秒，允许 1..86400 整数。它与单次 Provider 超时、MI 观察预算及
外层 arm/pair wall budget 分开；增加观察等待不增加模型尝试、技能预算或 simulator steps。
