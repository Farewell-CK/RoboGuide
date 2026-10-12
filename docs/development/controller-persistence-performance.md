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

这些历史 O6 结果只定位该本地 debug 剖面的成本；不证明规模上限、release 性能、历史 pilot
根因或硬件行为。当时尚未测量 batch；后续 ADR-0080 已实现并验证有界批处理，当前默认
8 inputs / 2ms，单条模式仍可明确选择。后续结果与限制见本文对应实施记录。

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

## ADR-0080 批处理软件交付与测量（2026-10-12）

`application/fact_batch.rs` 已成为 production Node receiver 的事务 driver。默认最多
8 条/单一 2ms 窗口，边界为 32 条/5ms，窗口严格小于 application timeout；单条模式
`ROBOGUIDE_FACT_BATCH_LIMIT=1` 默认不等待。每项完整派生 transition、input savepoint、
嵌套 Mission savepoint 和原 receive time 保留，registration/update/Unavailable 独立提交。
被拒绝的精确 session 在 Commit 后、outbox flush 前被 fence；共享/SQL 错误全批回滚。

6 项真实 gRPC 检查覆盖 1/N checkpoint/event trace 等价、Commit 前无 Ack、checkpoint
失败时无 Execute、owner 拒绝而另一 session 成功、运行期 hold 的 savepoint 嵌套，以及
collector/window/barrier。另有 State 嵌套事件/序号 rollback 检查。它们不执行硬件。

隔离工程 probe 两轮、每轮 4 个双 session wave，分别检查 limit 1 与 8、diagnostics
on/off，第一组 64/64、第二组 64/64 均完成，保留所有失败分母。首次零窗口几乎没有
形成批次，因此没有从该组推定收益。第二组显式 2ms 窗口得到如下全局记录：

| 轮次 / 配置 | 原输入数 / fact batches | serialization 次数 | Commit 次数 | Mission completed |
| --- | --- | --- | --- | --- |
| 1 / limit 1、wait 0 | 56 / 56 | 69 | 68 | 8/8 |
| 1 / limit 8、wait 2ms | 56 / 43 | 56 | 56 | 8/8 |
| 2 / limit 1、wait 0 | 56 / 56 | 69 | 68 | 8/8 |
| 2 / limit 8、wait 2ms | 56 / 40 | 53 | 53 | 8/8 |

serialization/Commit 包含 timer/HTTP，故不等于 fact batch 数；表来自 diagnostics-on
窗口，未 dropped record 或 I/O error，但 session 细分维度达到上限而省略（283–292）。
全局指标仍保留。小样本耗时与 OS 调度/SQLite 波动明显，不能宣称端到端固定提速比例。
两组原始 logs、数据库、binary/diff SHA256、完整分母和窗口位于仓库外
`/home/duchaoke/workspace/roboguide-audit/adr-follow-up-audit-2026-10-12/` 的
`s2-process-probe` 与 `s2-process-probe-window`。测量为合成工程验证，E6 保持暂停。

每个 batch 仍编码完整 authority snapshot。减少全量磁盘写入的增量存储属于后续
ADR-0081；本阶段不会把事件 log 当成已具备 event-complete reducers。

## ADR-0081 增量存储与 full/release 对照（2026-10-12）

增量链已实现，保留完整 authority snapshot；少量改变通过 exact Copy/Literal 表示，
每项 schema/event-sequence/revision/前后摘要绑定，原子 compaction 最多保留 32 deltas。
缓存只在 Commit 发布，input/Mission/outer rollback 同步恢复 pending cache。旧库迁移
使用 storage marker fences downgrade；readonly inspection 返回当前重建结果。

8 项存储检查覆盖 UTF-8/插入与多位置改变、迁移、nested rollback、事件原子性、
compaction SQL failure、40 次同 event tail 的 bounded revisions、十种损坏拒绝恢复。
应用 restore 检查验证 hold、资源和 nonterminal Unknown/no-blind-replay。workspace
653 passed / 2 explicit ignored；Clippy、Rustdoc 和 Python 检查通过。

原始 `s3-process-probe` 首轮完成 32/32，第二轮首个 Controller 达到 10 秒启动门限。
该进程已创建 schema、尚无 checkpoint，未启动 Node；该轮计划 8、实际 0，另有三个
后续 arm 未到达。日志和 `incomplete-run.json` 保留，原因 NOT PROVEN。随后
`s3-process-probe-recheck` 与 `s3-process-probe-cached-digest` 两组各 64/64 完成。
probe 现已明确记录 planned/started/success、startup/inspection failures 与不可用
checkpoint，不因没有启动 Node 而把失败排除；真实非法 startup 配置验证了这个路径。

改进缓存摘要、exact prefix/suffix reuse 后，debug 两轮 diagnostics-on 的编码写入量
分别由 full 1,895,847 / 1,924,102 bytes 降到 incremental 434,087 / 446,101 bytes
（约 77%）。这些是 representation bytes，包含待 Commit 写入尝试，不是磁盘物理写入
或 SQLite/WAL page 数；两组都无 probe failure。增量计算仍增大 Save 的 CPU 成本。

release 另两轮共 64/64 完成，workload elapsed（每 arm 8 个 Mission）如下：

| 轮次 | full / diagnostics off | incremental / diagnostics off | full / on | incremental / on |
| --- | --- | --- | --- | --- |
| 1 | 2.081s | 2.119s | 2.285s | 2.131s |
| 2 | 2.190s | 2.100s | 2.151s | 2.348s |

样本少、差异双向，不能宣称吞吐或延迟固定提高。默认继续 full；部署在重复 snapshot
写入量是瓶颈时可显式选 `ROBOGUIDE_CHECKPOINT_STORAGE=incremental`。batch 仍编码
完整快照，event-complete reducers 与复制仍是独立机制。debug/release 比较严格区分，
所有 binary/diff hashes、完整分母、WAL 身份和 logs 都在前述仓库外目录。
检查无模型、Habitat、硬件或 E6；初始失败没有从后续结论中删除。
