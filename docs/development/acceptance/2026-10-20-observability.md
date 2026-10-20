# 原生指标、最小监控权限与实际通知验收

日期：2026-10-20。实施基线 `main@b4fb791`。
对应 [ADR-0115](../../decisions/0115-bounded-observability-and-alert-delivery.md)、
[运维指南](../observability-v0.1.md)和[原生配置工具](../../../tools/observability/README.md)。

## 已验证的软件调用链

实际封闭部署 profile/TLS 文件 → 原生 Promtool/amtool 校验 → 独立 Prometheus/Alertmanager →
Controller 专用 metrics-reader mTLS scrape → 实际 SQL save/Commit 故障 → 实际规则
firing → 独立 owned loopback receiver → 原权威显式恢复 → 原生 resolved。随后原两软件
Node 完成真实文件/CAS/独立复算、原 Memory revoke/reclamation 和重启对账。

两次故障各保存一次 firing 与 resolved，共 4 次实际投递。监控凭证无业务读取或 mutation
权限；不是复用 recovery-admin。最初 A 发现旧授权拒绝全局 viewer，保留失败记录；
正式增加 metrics-reader 和兼容门禁后 C 通过，未放宽全局 viewer 权限。B 保留先前角色
接线的证据；C 使用最终显式 Operator authorization v0.2。

Controller 的有界聚合器统计固定 kind/stage duration/scalar 与实际 queue；try-lock 丢样、
缺少 identity/age 和容量越界均显式 unavailable。application queue 已是跨 session 总计，
不能对每条来源 snapshot 再求和；ingress 按实际来源聚合。labels 不包含任意业务 identity。
stop pending/manual/overdue、原 Execute intent 和 Memory/Unknown 都读取原权威，不推动状态。

Mission `/metrics/prometheus` 原遗漏角色检查，现与 JSON metrics 共用认证、撤销和全局
metrics-only 门禁。真实 TLS/SQL 测试覆盖 global metrics/admin、tenant viewer、unmapped、
撤销、Request 读取/创建拒绝；没有 interpreter/model/Controller 调用。Mission authorization
v0.2 明确新角色，v0.1 原 grants 可升级，持久 v0.2 拒绝更高 revision 的 v0.1 回退。

共享 wrapper v42 与旧 Controller writer 拒绝保护当前授权；v41 原 process-exit、Memory、
deadline 与 verifier source fence 保留。补齐现代旧 wrapper 的 terminal verifier config
restore 检查，拒绝无原 provenance 的替换。Node Contract/config/catalog 与 journal v13 不变。

## 复现与原证据

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-monitoring-recovery-20261020-c --authority-recovery --content-reclamation \
  --monitoring-tools /tmp/rg-monitoring-tools-20261020
uv run python tools/quality/check_controller_replication_processes.py \
  --output /tmp/rg-observability-replica-20261020-b --skip-build --tls
uv run python tools/quality/check_recovery_bundle_processes.py \
  --from /tmp/rg-monitoring-recovery-20261020-c \
  --output /tmp/rg-observability-cold-recovery-20261020-b \
  --old-node /tmp/rg-pre-v13-node-20261020 \
  --old-controller /tmp/rg-pre-v42-controller-20261020
```

重现使用新输出目录，监控依赖以[原 archive digest](../../../tools/observability/README.md)
核验。C 的 `monitoring/` 保存 native configs、配置检查、Prometheus queries、原生
firing/resolved messages 与四个实际工具 binary digest；`result.json` 和 cleanup 证明
后续文件工作通过且全部 owned processes 已回收。没有向人员或外部应用发送消息。

| 证据 | 实际结果 |
| --- | --- |
| Controller binary | `sha256:da29e6a2db89a7871361fed3d3da848f977d91f21fc7075ffdc1d5f32798a20f` |
| 真实监控 | Prometheus 3.13.4、Alertmanager 0.34.1；2 firing、2 resolved |
| 监控权限 | actual metrics-reader；业务 read/mutation 拒绝 |
| Replica | 3 实际 mTLS replica、minority/leader loss/quorum loss/restore、真实 status metrics |
| 冷恢复 | 6 stores/72 full SQL tables、2 Completed attempts、原 deadline/新角色/回执保留 |
| 旧 Controller | 实际 v41 拒绝 v42，完整 SQL oracle 不变 |
| 冷恢复 manifest | `sha256:a076786fa78e12312eb91daf964f012f78f68a284f3ec44e810723ed3ecfc93e` |
| 设备证据 | devices=0、models=0、simulators=0；不建立物理停止或连续性 |

Replica checker 的 local work 使用显式协议 fixture，仅作为复制/隔离回归；正向业务由
真实文件/独立复算链证明。原下载曾中断导致 archive hash 失败，未执行不完整 binary；
完整重下与官网/官方 release metadata digest 一致后才解压运行。

## 质量和剩余边界

原生配置、规则与 Alertmanager check 通过；有界聚合器、真实 private HTTP stale/future
status、权限/codec/迁移和 profile input 有单独回归。完整 Rust workspace 889 passed、
2 原有 ignored，27 suites；完整 Python 2623 passed、1 原有 skipped。Ruff/format、
strict mypy（主集合 232 files）、函数文档、Rust private-items Rustdoc/warnings denied 与
MkDocs strict 和 workspace/all-target Clippy/warnings denied 均通过。

`/tmp/rg-v41-migration-20261020-a/` 另对实际 v41 数据执行新 Controller 恢复：原完整
SQL/CAS、两 Completed attempts、原预算/回执保持，随后在 startup transaction 升为 v42；
旧进程退出证据的 v41 接纳和更早 fork 拒绝由专项检查保留。

此验收不把配置中未声明的服务算作已覆盖，也不证明跨主机网络、30 天 availability、
人工值守响应 SLA、外部收件端或完整写锁 I/O 优化。F0/F4–F9 及完整 F10 均按原计划继续；
任何这些软件剩余项都阻止 SoftwareComplete。
