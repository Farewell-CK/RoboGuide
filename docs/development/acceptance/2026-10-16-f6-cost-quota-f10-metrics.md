# F6 成本证据、逻辑 quota 与 F10 Prometheus 入口

状态：软件基础切片通过；真实 GPU/交通/跨主机资源强制、告警平台路由和生产 SLO 达标仍待
部署证据。

Scheduler 新增 `SchedulingCostEvidence`：每条记录绑定 Node、来源、RoboGuide receive time、
不可续期 TTL、queue delay、travel distance、traffic delay、load 和可选 GPU memory。边界、
新鲜度、重复 Node 和异常数值在进入 `SchedulingSnapshot` 时拒绝；确定性整数 score 只影响
候选探索顺序，保留完整 evidence 到 `TaskSchedulingDecision`，不改变 CandidateSet eligibility、
Control Commit、资源所有权或 Local How。没有 evidence 时继续稳定 NodeId 顺序，未知不会被当成
零成本。

Control 另有持久 `QuotaLedger`，明确区分 tenant/category logical claim 与 physical reservation。
它支持有界 limit、幂等 claim、超额拒绝、显式 release、remaining/used 查询和 checkpoint restore
校验；没有配置的 quota 不会隐式变成无限物理资源。当前 default Core 路径仍只使用独占资源
reservation，未把可拆分 quota 冒充成设备或 OS 强制能力。

运维面保留兼容的 JSON metrics，同时新增无高基数身份的 Prometheus 文本入口：

- Mission Service：`GET /metrics/prometheus`
- Artifact/Memory HTTP：`GET /metrics/prometheus`
- Controller：既有 `GET /metrics`

这些入口只输出 Mission 生命周期/idempotency、Artifact catalog/upload、Controller
Mission/Node/Unknown/recovery/event-log high-water 等聚合值，不输出 tenant、request、artifact
或设备身份。

## 验证

- `cargo test --locked -p control`：155 passed，2 ignored；包括 cost evidence 新鲜度/排序与
  quota 幂等、超额和 release。
- `cargo test --locked -p integration-server`：220 passed；Artifact Response 新增 text 分支和
  全部既有安全/内存/恢复回归通过。
- Mission 相关文件 Ruff、strict mypy 通过；完整 Python 检查在后续总验收重跑。

## 未关闭边界

cost evidence 不预测或证明物理执行时间；quota ledger 不替代 Control reservation，也不提供
OS/GPU/设备强制。告警阈值、采集器、路由、跨主机网络和真实负载曲线仍需要部署演练。运行中
物理抢占、完整时空资源图和 arbitrary divisible resource binding 仍未开放。
