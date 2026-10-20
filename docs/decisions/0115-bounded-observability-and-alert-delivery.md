# ADR-0115：有界运行指标与实际告警投递

状态：Accepted。日期：2026-10-20。

## 决定

维护 Controller 的 observation-only 指标面：固定 kind/stage 的 duration histogram、
sampling/drop/queue 完整性，以及现有权威只读计数。指标不持久化进 authority，不授予
ACK、资源或设备事实，不参与重试/计划/停止决策。默认开启的聚合器只 try-lock，失败丢
观察并增加 dropped counter；不能阻塞实际应用。原可选 JSONL 观察独立保留。

Ports 暴露 transport-neutral bounded snapshot，Prometheus 字符串只在应用 composition
渲染。bucket 和 labels 固定，不输出 Mission、Node、session、证书、用户指令或文件路径。
queue 来源按原 kind/stage/session 在固定界限内核对；遗漏、缺失 age 或 observer drop
保持明确 unavailable，不把某个 session 的 last depth 当全局 queue。State/Runtime/Control
仍是唯一权威；stop/Unknown/recovery/quota 指标只读现有真实记录。

部署使用显式 profile 声明服务地址、mTLS 文件、固定 service/故障域与采样/评估窗口，
生成正式 Prometheus 和 Alertmanager 配置；不生成 Node、能力、设备或新 authority。
规则包括抓取失败、write-disabled、Unknown、stop/manual、Memory reconciliation、延迟、
queue incomplete/backlog 和 observer loss。不存在信号不算健康；候选 SLO 与已观察窗口分离。

实际 conformance 必须启动独立 Prometheus/Alertmanager，抓取实际服务，注入真实故障并
由本次拥有的私有 loopback webhook 保留 firing/resolved。不得向真实外部收件人发送测试
消息。Promtool/amtool 校验、固定依赖 SHA、采样与消息 identity 一起保存；synthetic rule
tests 只能验证规则边界，不能独立声明采集/告警部署完成。

当前同机软件验证不证明跨主机 SLA、30 天 availability、物理 deadline 或设备停止。
Node 合同和原进程禁用策略不改变。部署监控 endpoint 遵守原证书/tenant 权限，读取
模式不授予 mutation。指标缺失/损坏、metrics worker 拥塞必须可观测并纳入告警。

全局监控使用显式 `metrics-reader` 证书角色，仅允许 Controller 的 GET healthz/readyz/
metrics，不授予 inventory、State、Mission、events 或任何 mutation。全局 viewer 仍拒绝。
Operator authorization v0.2 明确承载该角色；原 v0.1 仅保留原角色兼容，接纳 v0.2 后
禁止更高 revision 的 v0.1 回退。
共享 wrapper v42 fences 旧 Controller/supervisor；v41 standalone 可在原启动事务迁移，
但旧 wrapper 中藏有新角色的记录拒绝。Node Contract/config/catalog 与 journal v13 不变。
复制 profile 升级仍须原 accepted-state/epoch 合同，不能只改 profile digest 原地启动。

Mission 的 `/metrics/prometheus` 原来遗漏 route role，现与 JSON metrics 共用专用全局
metrics-reader 门禁，拒绝未注册/撤销证书及 tenant viewer。Mission 授权 v0.2 显式承载该
角色，保持 v0.1 原角色兼容，v0.2 持久 profile 不可降级到 v0.1，即使提高 revision。
新角色不能读取 Request 或执行创建/审批/取消；原 HTTP audit、稳定主体和幂等语义不变。
