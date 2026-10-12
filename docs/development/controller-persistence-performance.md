# Controller 事务测量与后续设计

O6 的测量入口是 `tools/quality/measure_controller_pipeline.py`。它启动隔离的真实
Controller 与 `real-node-smoke --simulate-execute`，固定两会话、八轮合成 compute 任务，
不启动 E6、MI、模型、Habitat 或设备。每次用新目录、端口和数据库，保留二进制 SHA、
源码 SHA、输入模板、实际命令/计划/事件/checkpoint、日志和失败分母。

## 已观察结果

2026-10-12，本地 debug 构建，源码 `38e68b9`，Controller 生产路径为 `88b7f9d`。
三次 diagnostics on/off 重复，交替顺序，每轮 16 个 Mission：**96/96 完成**，无隔离。
raw evidence 保存在审计目录 `o6-pipeline-rerun-2026-10-12/`，真实数据不纳入 Git。

| 阶段 | Execution 样本数 | 平均耗时约 ms | 观察含义 |
| --- | --- | --- | --- |
| clone | 144 | 0.73 | 非本剖面的主要成本 |
| reduce | 144 | 1.55 | 包含原 lifecycle 和 Mission 派生推进 |
| serialize | 144 | 27.45 | 全量 JSON checkpoint 成本 |
| SQLite save | 144 | 0.39 | 尚未包含 durable Commit |
| SQLite commit | 144 | 44.63 | 既有 WAL/FULL durable 路径 |
| application queue | 144 | 70.85 | 等待前一事务；不能归因为模型或网络 |
| receive-to-ACK | 144 | 153.45 | 包含等待、gate、事务与完成通知 |

on/off 工作负载中位数约 10.42 / 10.11 秒，轮间范围有重叠，不能证明稳定的诊断开销比例。
两者尾部等待不同，尾部从工作负载耗时中排除。诊断的 global records 无丢失/IO error；
精确 session/stage 维度达到有界上限，有省略计数，不能声称 per-session 样本完整。
百分位是 log2 bucket 上界；queue depth 是窗口最后一次观察，不是峰值。

第一轮原 smoke 客户端错误拒绝相同 Execute 重投，只有 13/96 完成；原目录
`o6-pipeline-2026-10-12/` 完整保留，排除出性能比较。修补后仍拒绝任何变更的
command/session/payload，不改变服务端重投、receipt 或接受规则。

这些结果只定位该本地 debug 剖面的成本；不证明规模上限、release 性能、历史 pilot
根因或硬件行为。未测量 batch 候选，不声明其收益或语义等价，**当前不启用批处理**。

## 批处理的实施门禁

先保留既有单事实事务。候选只能非等待地获取已到达的跨 session 消息，按消息数、
wire bytes 和 elapsed budget 有界；registration、session 替换与 route-loss 单独 barrier。
不能锁内等待凑包，不能合并内容后仅保留 sequence，更不能改 receive time。

每个输入拒绝需要独立 memory/event savepoint；与 Mission 派生 savepoint 的嵌套关系
必须重新设计。一个输入拒绝不能污染另一个有效输入。共享持久化/ownership failure
保持整批 fail closed。timeout/closed completion 不授权处理已超时的迟到命令。

必须比较同一固定事实轨迹的事件、ownership、expiry、current attempt、outbox 与 ACK
边界，并在 reduce/save/commit/swap/completion 注入失败；只有最终 durable Commit 和
live swap 成功才完成该批每个 acceptance，发送仍逐条读取 fresh Clock。再按相同输入
比较 1/N，需稳定减少 durable 总成本且不扩大 tail latency，才决定启用。

## 增量持久化的独立设计

若后续测量继续显示全量 serialize 主导，可研究版本化 checkpoint deltas 加周期性
全量快照。delta 至少携带前一 checkpoint identity、覆盖 event sequence、schema 与
内容 digest；基线缺失、sequence gap、版本不匹配或损坏必须拒绝恢复。

State 原始 received_at、Control reservation/registry watermark、Runtime command intent/
current attempt/fences、Mission hold generation 仍由各自 authority 编码。不得从日志
诊断文本或 Allocation projection 重建所有权；Memory payload 不进入此数据面。

Compaction 只在新全量快照 durable 且可校验恢复后回收旧 deltas。checkpoint write 和
事件接受必须原子，恢复保持 Unknown/no-blind-replay。先 ADR、兼容迁移和 crash/restore
等价性证据，再考虑实现；本包没有引入增量格式或降低 SQLite durability。

## 复现

```bash
cargo build -p integration-server -p real-node-smoke --locked
uv run python tools/quality/measure_controller_pipeline.py \
  --output /tmp/roboguide-pipeline-new --repeats 3 --waves 8
```

输出目录必须不存在。该入口仅终止自己创建的进程。逐个检查 attempted、smoke returncode、
durable completed、诊断损失和来源身份；有失败保留原记录并返回非零，不转换成成功。
