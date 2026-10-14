# RoboGuide Production Candidate SLO 与故障演练 v0.1

状态：验收草案。指标端点已经提供聚合观察；阈值、告警路由和跨主机演练需要部署环境冻结后执行。

## 指标和 SLO

| 信号 | 指标来源 | 候选阈值 | 告警动作 |
| --- | --- | --- | --- |
| Mission API availability | Mission `/readyz`、`/metrics` | 30 天 99.9% | 停止远程 mutation，保留本地审计查询 |
| Controller readiness | Controller `/readyz`、event high-water | 5 分钟内恢复 | 进入 quorum/epoch fence，禁止新物理命令 |
| Artifact readiness | Artifact `/readyz`、CAS/catalog counters | 5 分钟内恢复 | 禁止新的 publish/import，保留 replay evidence |
| Node route freshness | Controller node/session metrics | 不超过 lease policy | 对绑定 Task 进入 reconciliation/Unknown |
| Runtime Unknown | Controller metrics/recovery view | 0 个未解释增长 | 禁止 release、repeat、handoff |
| Memory import unknown | Node journal/Memory receipt | 15 分钟内有处置 | 同 digest 重试或人工升级，不能换新 import identity |
| Journal/archive integrity | startup/readiness checks | 0 个校验错误 | fail closed，保留原目录和审计 |

具体 percentile、窗口和采集标签必须与部署 profile 一起冻结；当前 `/metrics` 是聚合观察，不是授权入口。

## 故障演练矩阵

| 演练 | 注入 | 必须观察 | 通过条件 |
| --- | --- | --- | --- |
| Mission restart | 杀进程并重启 | idempotency replay、audit continuity | 不重复创建 Mission，不丢 committed request |
| Controller restart | 关闭 composition root | checkpoint/journal/reconciliation 顺序 | 未完成 physical attempt 不被自动重放 |
| Route loss | 断开 Node gRPC | route/session fence、Runtime Unknown | 不释放 ownership，不接受旧 session fact |
| Leader loss | 停止当前 replica | epoch/quorum、child supervisor | 旧 epoch 不发命令；新 leader 先物化完整视图 |
| Quorum loss | 隔离两成员 | readiness、command delivery fence | no new mutation/physical command |
| Certificate failure | 过期/错配 cert | mTLS negative path | listener 或 replica peer fail closed |
| Journal corruption | 篡改 SQLite/sidecar | startup validation | 进入 degraded/blocked，不删除证据 |
| Archive replay | 恢复 checkpoint + tail | sequence/schema/digest | projection 可重建，旧 schema 被拒绝 |
| Memory import interruption | 在 persisted/staged/imported 窗口断电 | import journal/receipt | 同一 digest 可重试；不产生第二 identity |
| Physical Unknown | 丢失 stop/safe-state evidence | recovery view/ownership | 不 release、不 repeat、不 takeover，进入人工升级 |

## 演练记录要求

每次演练保留部署 profile digest、代码 SHA、配置 digest、注入时间、观测时间、事件 high-water、
journal/archive digest、结果、`TEST GAP` 和 `NOT PROVEN`。合成进程测试不能替代物理安全证据；实验和 E6
不作为本整改计划的验收材料。
