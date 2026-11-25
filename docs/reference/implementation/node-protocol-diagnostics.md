# Node Protocol 配置与本地只读诊断

ADR-0075 为现有 Node Protocol v0.4 的 startup timing 和 observation 提供统一入口；
不改变 `Registered`/`Ack` 的 application authority + durable checkpoint 含义。

| 环境变量 | 默认 | 行为 |
| --- | --- | --- |
| `ROBOGUIDE_NODE_HEARTBEAT_INTERVAL_MS` | 5000 | Welcome 中的 heartbeat 周期 |
| `ROBOGUIDE_NODE_LEASE_DURATION_MS` | 15000 | transport fencing 与 Control 注册/续约共用的 lease |
| `ROBOGUIDE_NODE_APPLICATION_ACCEPTANCE_TIMEOUT_MS` | 30000 | 等待 application completion 的上限 |
| `ROBOGUIDE_DIAGNOSTICS_PATH` | 不设置 | 启用 deployment-owned 本地 JSONL 文件；空值也关闭 |

duration 只接受 unsigned integer，必须为正数且可表示；lease 至少覆盖两个 heartbeat
周期。配置一次解析冻结，错误或非 UTF-8 duration 拒绝启动。health freshness 默认仍为
5 秒；延长 heartbeat/lease 不延长 health、State 或 peer TTL。acceptance timeout 不表示
物理停止，也不自动重新提交动作。

```bash
ROBOGUIDE_DIAGNOSTICS_PATH=/absolute/run-local/controller-diagnostics.jsonl \
cargo run -p integration-server -- 127.0.0.1:50051
```

诊断由专用 I/O thread 每 5 秒输出一份 `roboguide.local-diagnostics/v0.1` 汇总；buffer
最多 1024 条 record，额外 session/kind/stage 维度最多 64 个。只保留当前和 `.1` 两个
8 MiB 文件。标识超过 256 bytes 时省略 attribution，保留 aggregate；不会修改原事实。
sink 缓冲满、路径写入失败、关闭或 session attribution 截止只丢失诊断。

两级队列统计 depth、encoded bytes 和 oldest age；每队列最多保留 1024 个 enqueue 时间。
超过跟踪上限时 oldest age 为 null，直到遗漏 entry 全部离开。drop/cancel/channel close
通过 RAII ticket 清理，诊断不删除事实、不改变队列容量或顺序。

汇总按固定 kind/stage 分组，提供样本数、sum/min/max、固定 log2 histogram 和
`p50_upper`/`p95_upper`/`p99_upper`。这些是 bucket 上界，不是精确 percentile。取全局成本
时只消费 `identity: null` 的 rows；session rows 是同一样本的额外维度，不能重复相加。
同时报告 dropped records、I/O error 和 omitted identity 计数。

已观察 receiver/session 与 application queue、write gate/Controller lock、clone、reducer、
checkpoint serialization/bytes、SQLite save/commit、completion/ACK、timer lag、outbox。
HTTP 请求另有整体耗时、clone、checkpoint 和 SQLite 观测；锁争用导致不可取得只读 sink
handle 时可遗漏 request 诊断，不改变 HTTP authority。字段不含 payload、凭据或模型输入。

transport heartbeat age、Control lease remaining 与 health age 独立记录。transport 的
session snapshot 是只读诊断，不授权 Control 续约或物理恢复。原始 ingress receive metadata
与 application 使用同一 process Clock，但此切片仍只观测；O4 单独决定 authority 时间。

验证包括 custom Welcome/route、new/renew/restore 与独立 health freshness，暂停时钟的
completion timeout，以及真实 loopback 的 receive metadata/ACK barrier。正常、disabled、
full-buffer 和失败文件 sink 的 timer 事件及 checkpoint byte-for-byte 相同。
不启动或修改 E6，不把 loopback 结果当成正式扩展性或设备实验。
