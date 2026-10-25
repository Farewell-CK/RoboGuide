# RoboGuide Production Candidate SLO 与故障演练 v0.1

状态：软件验收与运行处置基线已冻结（2026-10-26）。下表是默认目标，不是实测达标声明。
部署以闭合 observability/deployment profile 固定实际 endpoint、故障域、阈值和接收端；
实际跨主机、30 天窗口和人员确认分别记录证据，不从单机软件门禁推断。

本地 SQL 故障已有[写入禁用与恢复指南](controller-write-recovery-v0.1.md)，实际软件门禁
覆盖保存/Commit 两窗口、最后提交查询、Node unavailable、原探针和上传 chunk fence。
这不证明下表恢复时限或人工响应 SLA 已达标；完整采集/告警部署仍须实际演练。

[原生监控 profile](observability-v0.1.md)和[实际通知验收](acceptance/2026-10-20-observability.md)
已证明受限 mTLS scrape、真实规则评估、Alertmanager 至独立 owned loopback receiver 的
firing/resolved。该短窗口仅报告实际投递，尚不证明 30 天 SLO、跨主机或值守人工确认。

## 指标、分母与默认目标

availability 分母为声明窗口内所有计划探测，缺失、超时、权限或合同错误都不能计入成功。
readiness 单独统计，HTTP health 200 不算任务满足或可执行许可。延迟统计保留实际成功/
尝试分母、失败和丢样；没有样本时为 unavailable，不能填零。30 天目标为 99.9%，对应
43 分 12 秒错误预算；短软件验收只报告该次实际样本。探测默认每 15 秒、评估 15 秒、
pending 60 秒，profile 可显式声明更严格的演练参数。p99 默认目标为 2 秒，backlog 32；
实际部署必须保留采用值，不允许在失败后无记录调整阈值。

| 信号 | 指标来源 | 候选阈值 | 告警动作 |
| --- | --- | --- | --- |
| Mission API availability | Mission `/readyz`、`/metrics` | 30 天 99.9% | 停止远程 mutation，保留本地审计查询 |
| Controller readiness | Controller `/readyz`、event high-water | 5 分钟内恢复 | 进入 quorum/epoch fence，禁止新物理命令 |
| Controller write-disabled | `/metrics` 的 `roboguide_authority_write_enabled`、`disabled_generation` | 1 分钟内告警、5 分钟内确认原故障 | 保留原目录和模式证据，核对 restart_required；只使用原探针或受控重放 |
| Artifact readiness | Artifact `/readyz`、CAS/catalog counters | 5 分钟内恢复 | 禁止新的 publish/import，保留 replay evidence |
| Node route freshness | Controller node/session metrics | 不超过 lease policy | 对绑定 Task 进入 reconciliation/Unknown |
| Runtime Unknown | Controller metrics/recovery view | 0 个未解释增长 | 禁止 release、repeat、handoff |
| Memory import unknown | Node journal/Memory receipt | 15 分钟内有处置 | 同 digest 重试或人工升级，不能换新 import identity |
| Journal/archive integrity | startup/readiness checks | 0 个校验错误 | fail closed，保留原目录和审计 |
| 各入口容量 | actual connections active/limit/rejected、scrape availability | 连续 60 秒满载告警；五分钟新增 rejection 为 warning | 先核对实际 worker/SQL/连接退出，保留原命令并只读对账；调整部署限额不续业务预算 |

固定标签和丢样计数遵循[观测合同](observability-v0.1.md)；当前 `/metrics` 是聚合观察，
不是授权入口。[实际入口验收](acceptance/2026-10-26-listener-capacity.md)保留短窗口原生
firing/resolved、独立响应与实际进程退出；长期达标仍需完整实际观测窗口。

## 故障演练矩阵

| 演练 | 注入 | 必须观察 | 通过条件 |
| --- | --- | --- | --- |
| Mission restart | 杀进程并重启 | idempotency replay、audit continuity | 不重复创建 Mission，不丢 committed request |
| Controller restart | 关闭 composition root | checkpoint/journal/reconciliation 顺序 | 未完成 physical attempt 不被自动重放 |
| SQL save/Commit failure | 实际数据库的独立失败窗口 | first failure、原 SQL/State 时间、Node unavailable、上传 chunk fence | 查询保留、无未提交 ACK/派生命令，原探针 Commit 后恢复；原 receipt 不解除下一代故障 |
| Route loss | 断开 Node gRPC | route/session fence、Runtime Unknown | 不释放 ownership，不接受旧 session fact |
| Leader loss | 停止当前 replica | epoch/quorum、child supervisor | 旧 epoch 不发命令；新 leader 先物化完整视图 |
| Quorum loss | 隔离两成员 | readiness、command delivery fence | no new mutation/physical command |
| Certificate failure | 过期/错配 cert | mTLS negative path | listener 或 replica peer fail closed |
| Journal corruption | 篡改 SQLite/sidecar | startup validation | 进入 degraded/blocked，不删除证据 |
| Archive replay | 恢复 checkpoint + tail | sequence/schema/digest | projection 可重建，旧 schema 被拒绝 |
| 在线 Archive / Snapshot / GC | 原 floor 推进、离线成员、purge、当前内容损坏 | 原 package/Catalog/引用、joint quorum 与 actual bytes | 缺失拒绝；原 Unknown/预算保持；实际 Snapshot 安装及完整恢复后字节一致 |
| Listener exhaustion | 原实际 TCP/TLS/Unix 连接占满、慢 header/response | 实际 excess close、独立入口、原 native firing/resolved | TLS/线程前拒绝；实际 handler 退出后归还；已提交事务不被 socket 关闭回滚 |
| 跨域准备或回复丢失 | 实际 Prepare/Commit 断连、原 owner 退出 | 原持久意图、nonce/budget、各域 ownership/global goal | 不换 ID，不重复副作用；全体 Commit 前无执行；原停止与独立目标分别成立 |
| Memory import interruption | 在 persisted/staged/imported 窗口断电 | import journal/receipt | 同一 digest 可重试；不产生第二 identity |
| Physical Unknown | 丢失 stop/safe-state evidence | recovery view/ownership | 不 release、不 repeat、不 takeover，进入人工升级 |

## 演练记录要求

每次演练保留部署 profile digest、代码 SHA、配置 digest、注入时间、观测时间、事件 high-water、
journal/archive digest、结果、`TEST GAP` 和 `NOT PROVEN`。合成进程测试不能替代物理安全证据；实验和 E6
不作为本整改计划的验收材料。

## 运行处置与升级顺序

由部署声明值守主体和确认时限，默认目标为一分钟投递、五分钟确认；软件只可证明实际
接收端投递，不能代替人工签收。incident 持有者先保存实际代码/配置、原目录、first failure、
最后提交 checkpoint/journal、当前 epoch/quorum 与 alert 时间线。处理流程：

1. 停止新 admission，保留原查询入口；依据真实 write gate 判断只读还是可受控恢复。
2. 对原 command/import/transaction 查询，区分未接纳、已持久意图、Unknown 与独立 completion。
3. 数据库故障按原 generation 恢复探针；复制不确定性按受控 replay，禁止手工清锁或换目录。
4. 缺内容按 exact owner/input/引用修复；Archive package 是私有控制元数据，不经普通 Artifact 发布。
5. 升级按原 all-member support、原 child wait/predecessor/activation 顺序；不安全回退拒绝。
6. 仅在原 stop/revoke、Control/当前 State 和独立目标门禁满足后恢复，保留 resolved 与实际时间。

参考[写入恢复](controller-write-recovery-v0.1.md)、[入口容量](listener-capacity-v0.1.md)、
[代次升级](application-generation-upgrade-v0.1.md)、[在线归档](coordinated-content-archive-v0.1.md)
及[跨域事务](federation-coordination-v0.1.md)。达到恢复目标必须由原故障开始至真实恢复
完成计算；数据库备份成功或一个 health 200 都不能结束该计时。
