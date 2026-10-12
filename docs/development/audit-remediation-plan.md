# 审计优化实施记录

本记录跟踪 2026-10-12 经复核的 O1–O8 优化方案，历史只读方案和 Claude 原报告保留原样。
实施起点为 `dev@7eab30d51bd60f0d3c06e8397b788211ea8a8e9f`。

| 工作包 | 状态 | 验收证据 / 下一步 |
| --- | --- | --- |
| O1 恢复定位硬化 | 已实现 | `7eab30d`；相关包回归通过。历史重复 Role 根因仍为 NOT PROVEN |
| O2 所有权模型 | 已实现并验证 | Control 137 个常规测试；256 条固定轨迹及 10,000 条扩展轨迹通过，支持 case 缩减、保存与回放 |
| O3 配置与只读指标 | 已实现并验证 | ADR-0075；统一 timing、自定义 new/renew/restore/Welcome、两级 queue 及 default-off JSONL，观测失败不改变 durable trace |
| O4 时间与 Control 活性 | 已实现并验证 | ADR-0076；Control 验证连续 exact-session evidence，独立双时间 ingestion、失效/restart fence；workspace checks 通过 |
| O5 Mission 故障隔离 | 待设计 / 实现 | 先 ADR；局部失败回滚，持久隔离，显式重验恢复 |
| O6 事务性能 | 条件性 | 先固定合成轨迹测量；瓶颈和收益不足时记录不实施结论 |
| O7 部署边界 | 待实施 | 在既有模块内拆分中性消费者与具体部署解析 |
| O8 发布技术准备 | 待实施 | 扫描、示例、ADR 标识、边界披露；不选择 LICENSE 或发布 |

E6 尚未开始并持续暂缓。本任务只使用确定性离线测试及独立临时数据库、端口的本地合成
gRPC 测量，不修改 E6 harness、配置、产物，也不启动模型、Habitat 或真实设备。
无关未跟踪内容保留。

每包独立提交并验证远端 SHA。验收区分实现、确定性检查、合成进程测量和真实物理证据；
后两者不能互相替代。性能结论记录源 SHA、冻结输入、重复次数、样本数、失败项和原始
阶段耗时，不能从旧 pilot 归因当前版本。

## 实施基线质量门

上一切片遗漏 workspace Clippy；基线检查发现 `NodeLeasePolicy` 两个私有字段缺少文档。
补齐字段注释后 `cargo clippy --workspace --all-targets --all-features --locked -- -D warnings`
通过。O2 workspace tests 与 Rustdoc 也通过，不以相关包测试代替。

## O2 交付

加入反例后先观察失败，再补齐 recovery release 前置校验、legacy Role 唯一性及 pending
recovery 的 exact-unbound/previous-owner restore 校验。合法路径的事件次序及 checkpoint
shape 不变。此证据不证明 Claude 对历史 pilot 的根因归因。

相关 Control Clippy 和 tests、workspace tests、Rustdoc、formatting、diff 检查通过。扩展
检查 10,000 条轨迹耗时约 58 秒；只表示本地确定性模型覆盖，不是性能或硬件实验。

## O3 交付

新增 `NodeProtocolPolicy`、original ingress metadata 和传输中立诊断端口；部署 root 读取
startup 配置、共享 process Clock，并注入默认关闭的本地 JSONL sink。指标有界聚合、
try-send、独立 I/O thread 和两份文件轮转。格式、workspace Clippy、workspace tests、
Rustdoc 与 diff 检查通过。custom loopback 协议、acceptance timeout、health freshness、
checkpoint restore 及 disabled/full/failed sink 的一致轨迹测试通过。

## O4 交付

reader 连续摘要经传输中立端口进入 Control；timer/consumer 在原 durable candidate
事务中接受 liveness-only evidence，不更新 health、不提前 Ack raw heartbeat。记录初始
session anchor、sequence watermark 与 fenced expiry，restore 同 live lease 清空。
registration lease 使用当前 decision time；其 Offline 状态及后续 State/health/peer/progress
保留实际 receive time。新事件 codec v13 可读取 v2–v12；旧 checkpoint 仍兼容。

连续/真实静默/错误身份/顺序/重放/expiry/route-loss/restart、延迟 health 与 custom
loopback metadata 检查通过；workspace Clippy、tests、Rustdoc 与 formatting 通过。
此实现与合成证据仍不能证明历史 pilot 故障的因果或设备物理停止。
