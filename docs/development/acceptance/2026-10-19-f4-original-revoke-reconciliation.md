# F4 原 Memory 撤销的部分失败对账验收

状态：本工作包的软件实现与下述验收通过；完整 F4、跨 Group handoff、F5–F10 与整体
SoftwareComplete 仍未关闭。硬件证据独立保留。本轮设备、模型、模拟器均为零，未运行实验。

## 已交付行为

[ADR-0107](../../decisions/0107-original-revoke-observation-and-provider-progress.md) 增加原
provider 的独立撤销进度、最多四个全局 I/O worker、原十秒预算内的三次持久退避 GET，
以及操作员明确提交的独立只读会话。一个 provider 不可用不跳过其余 provider；已确认的
原证明可以离线复用。POST 意图提交后至多执行一次；歧义不会换 ID、换 backend 或重放。

独立会话每条原 revoke 最多一次、最长五分钟/十六代；每代每 provider 一次 GET，单次
最长三十秒。原 initial Unknown、use/import/preparation/执行预算和原始 receive time 不变。
后续结果单独追加，逐 provider 归约；仅当原 provider/context 和独立 stop gate 全部成立
才允许相应作用域的释放。迟到或 changed-owner 证据只进入历史。

Operator HTTP 入口为 `POST /v1/memory/use-queries` 和分页 `GET /v1/memory/use-queries`。
mutation 通过真实 transport witness、writer gate 和稳定 principal/tenant 检查，在后台
SQL worker 提交后再投递。use-leases v0.2 给出原回执与独立 provider_progress；Prometheus
增加未确认 provider、pending query 和过期观察会话聚合计数，不添加高基数身份标签。

catalog v0.14/config v0.12/Node Contract v0.11 显式协商 Query/QueryReceipt；原 use v0.1
不扩写。Controller/supervisor wrapper v39、Node journal v11、事件 codec v26 拒绝旧 writer。
旧 v38/v10 的保守读取不推定缺失证据，不删除原命令、预算或历史。

## 实际进程与独立证据

三种故障配置各使用两个真实软件 Node、三个实际接收 provider，以及额外独立的源端
provider 进程。Node 通过真实证书/API 登记；三个不同的仓库源文件实际经 CAS 导入，
独立子进程重算 SHA-256 并检查原 backend SQLite。透明 Unix 代理只丢弃实际回复，不
生成 provider 回执；所有进程由各次检查创建并回收，cleanup 记录均为 true。

| 配置 | 实际结果 | 原预算/效果 | 独立停止门禁 |
| --- | --- | --- | --- |
| automatic | 丢失原 revoke 回复后，在原预算内 GET 找回，原聚合为 Revoked | 每 provider 一次 POST；没有独立管理会话 | 保留 |
| manual | 原查询预算耗尽并保存 Unknown，Controller/Node 各重启一次，再通过显式 GET 会话确认全部 provider | 原 Unknown 与预算完全不变；每 provider 一次 POST | 保留 |
| unresolved | 原回复已丢失，独立查询持续不可用；观察会话到期后下一代请求拒绝 | 原 Unknown 保留，不能重开会话、续期或强制释放 | 保留 |

这里取消发生在消费者 Execute 前；源 Task 有实际 CPU 哈希及独立 verifier，消费者三份
导入各自验证，但没有启动消费者计算。不将这些检查当成多 provider 计算、设备停止或
Mission 成功。独立停止证明缺失，因此三个取消场景最终均保持 Cancelling/原 ownership；
manual/automatic 的内容确认不能绕过该门禁。unresolved 另外保留内容未确认阻塞。

标准并行生命周期检查另行证明正常完成：一个 Task 已完成、兄弟 Task 仍在实际 CPU
计算且许可未撤销；两个独立 verifier、全部 context/provider ACK 后 Mission Completed。
它使用既有 Node Contract v0.10，核对本次协议扩展没有破坏原正式路径。

复现命令：

```bash
cargo build --locked -p integration-server -p roboguide-node -p controller-replica
uv run python tools/quality/check_memory_preparation_processes.py \
  --use-query-case automatic --output /tmp/rg-use-query-auto-final
uv run python tools/quality/check_memory_preparation_processes.py \
  --use-query-case manual --output /tmp/rg-use-query-manual-final
uv run python tools/quality/check_memory_preparation_processes.py \
  --use-query-case unresolved --output /tmp/rg-use-query-unresolved-final
uv run python tools/quality/check_memory_preparation_processes.py \
  --group-lifecycle --output /tmp/rg-use-query-group-final
uv run python tools/quality/check_memory_preparation_schemas.py \
  --directory /tmp/rg-use-query-manual-final
uv run python tools/quality/check_memory_release_upgrade.py \
  --directory /tmp/rg-use-query-manual-final \
  --old-controller /tmp/roboguide-before-use-query-20261019-integration-server \
  --old-node /tmp/roboguide-before-use-query-20261019-roboguide-node
```

输出目录必须是新建私有目录；上面的路径是本轮证据原件，不可覆盖重跑。每份 result
保留实际二进制、解释器、检查器、后端源码及配置摘要，进程清单在 owned-process-exits.json。
Controller SHA-256：`fc73f32df2ccabdef4eb7000ed5ff746cb616ed58fcf4f59b51bb4f4cd44c86a`；
Node SHA-256：`52246727884211fd2d6c44b7672f5c46707a0bf9a5f10cf1001786d09370be91`。

本地 jsonschema 4.26.0 验证实际配置、MissionPlan、preparation/use/query 与回执；manual
配置覆盖 21 类结构拒绝。取消前未形成聚合 Granted，schema 结果显式记录
positive_grant_available=false；既有并行 profile 另保留实际 Granted 与正常完成证据。

实际旧 Controller v38 对非空日志先被 codec v26 拒绝，对零 Node/零事件数据库被 wrapper
v39 拒绝；实际旧 Node journal v10 binary 被 v11 marker 拒绝。各数据库全部 authority
table 逻辑摘要前后相同；不是构造旧标签或放宽 schema。记录见
`/tmp/rg-use-query-upgrade-final.log` 与 manual 目录 release-upgrade-*/result.json。

## 回归、失败原件与边界

完整 Rust workspace：845 passed、2 项既有 ignored；Integration Server 229、Node Service
129。新增用例覆盖独立观察期限、重启不续期、一次 GET、原 Unknown 不覆盖、当前 Task
释放、活跃 peer 保留、tenant/principal/代数冲突、替换 backend、迟到与旧 wrapper 拒绝。
workspace Clippy、private Rustdoc、fmt、Python Ruff/strict mypy/函数文档检查通过。

主要日志：`/tmp/rg-use-query-workspace-final.log`、`/tmp/rg-use-query-clippy-final.log`、
`/tmp/rg-use-query-rustdoc-final.log`、`/tmp/rg-use-query-schemas-final.log`。

早期 `/tmp/rg-use-query-a` 至 `-f` 均保留为未通过：包括检查配置修改不可变 policy identity、
source 路径缺失、相同 CAS bytes 的别名权限拒绝、查询字段错误，以及对账完成后错误地
期待缺少 stop proof 的 Mission 直接 Cancelled。最终配置使用不同实际源 bytes，按真实
API 字段观察，并明确验证独立 stop hold 保留；未修改 Artifact ACL 或 physical stop gate。
这些失败不计入通过结果，不能作为同条件性能比较。

跨独立主机故障、全成员变更/跨 Group handoff、HA 内容恢复及其余 F4–F10 继续实施。
独立观察会话耗尽后保留人工阻塞；本轮没有增加强制确认或绕过原证据的恢复接口。
