# ADR-0217: Explicit Federation request publication budget

- Status: Accepted
- Date: 2026-10-26

## Context

实际独立三副本域在正常 CPU/目标、领导者切换和在线 Archive 后，后续请求会在 v0.1
最高 10 秒内耗尽其 SQL publication 与 peer I/O 预算。原失败必须保留，不能通过清库、
刷新 kernel clock 或把 Unknown 改为成功来继续。既有 profile 范围也不能静默放大。

## Decision

新增显式 peer profile v0.2，要求闭合字段
`budget_scope=intent-publication-and-peer-io`，原 request 范围 100–30,000 ms。
v0.1 保持 100–10,000 ms。两者使用同一个 SQL-admitted 原 kernel budget，
publication、TLS 与实际 I/O 共同消耗；发送前重算剩余量，到期不打开连接。

新配置通过连续 revision 接受，保留所有旧 profile、原 intent、Unknown、nonce、
事务/cleanup 时钟和决定。v0.2 不得降级到 v0.1。每个实际新 intent 绑定其 profile
revision，旧 intent 不借用新预算。全局时钟区间和预算仍由原 manifest 单独限制。

闭合旧 application decoder 拒绝新必需字段；旧 supervisor 拒绝新 schema。
恢复工具检查精确嵌套代次、范围、scope 和不可降级历史。无需新增 wrapper/Node 格式。

## Validation and limits

范围、缺失/错误 scope、旧范围不变、连续升级和降级无 mutation 由合同测试验证。
真实 HA gate 在实际原 Root 上运行 genuine 前一 supervisor，保留实际 exit/stdout/stderr
和原 SQL/CAS 摘要；拒绝必须先于任何 Node 或当前 supervisor 启动。
完整双域分区、证书、换主、Archive 和冷恢复仍需实际通过，不能从形状测试推断。

30 秒是显式软件部署预算，不是已达成的 SLO、hard realtime、设备停止或跨主机证明。
此前 v0.1 的实际超时和其余已成功阶段作为原证据保留。
