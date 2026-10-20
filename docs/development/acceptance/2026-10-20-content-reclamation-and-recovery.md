# 原 provider 回收、Active Map 权威与静止内容恢复验收

日期：2026-10-20。实施基线 `main@008aa8a203c8790d5e92fe9cd50e2d00c30743cb`。
对应 [ADR-0112](../../decisions/0112-original-provider-content-reclamation.md)、
[ADR-0113](../../decisions/0113-quiescent-recovery-bundles-and-node-writer-lease.md)。

本工作包的软件进程验收通过。它关闭原 provider 布尔 GC 越权、Active Map 输入/交接竞态、
Node journal 多 writer 与静止 SQL/内容恢复的对应检查；完整 F4/F5 仍有全局引用图、在线
数据面、证书轮换和跨域恢复要求，不能据此声明 SoftwareComplete。

## 实际调用链

正式 Controller mTLS 与两软件 Node → 原 Prepare/import → SQL 故障后的原 provider 查询 →
真实文件计算 → 两个独立复算 verifier → 原 revoke receipt → 本地 tombstone → unlink/fsync →
实际最终 SQL 故障 → backend 重启 → 观察原状态 → 显式原命令对账。

Active Map 只允许原 lease/preparation 中的完整 typed map 与 Node/Group/attempt。整个实际
文件读取和安装共用原 store ordering，并在最后效果处重查许可。交接冻结原 target 和当前
source head；不同 target、已释放 source 与空 stop set 均拒绝。旧八个布尔 `true` 不删除文件。

静止恢复 CLI 获取 Controller、CAS、两 Node、两 backend 的实际进程 writer lease，使用
SQLite backup API 保留 WAL 和完整 72 张表，复制并校验实际内容。恢复到新私有根后，
独立逐表 oracle 比较全部 rows/schema；实际旧 Node 拒绝 v13，新 Node 的真实存储 lease
阻止另一次 backup。随后启动实际 Controller，以 mTLS 查询 Mission/attempt/Catalog，
并从恢复 CAS 独立读取/hash 实际 bytes。重新打开原 backend 仍保留 tombstone，Imported
历史不变，当前 availability=false。备份和恢复的同 command 重试不重复复制或覆盖。

Node journal v13 在任何 SQLite/recovery 前取得 canonical sibling 和 database inode 两份
lease，覆盖软/硬链接别名；最后应用 owner 显式 unlock。迁移保留所有原预算和历史。
Node Contract v0.12/config v0.13/catalog v0.15、Controller wrapper v41 不变。

## 复现与证据

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-content-reclamation-20261020-h --content-reclamation
uv run python tools/quality/check_spatial_node_processes.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --profile /tmp/rg-spatial-use-20261015-b/profile.json \
  --source-root /tmp/roboguide-spatial-provider-inputs-20261015/extract/DemoICPPointClouds \
  --output /tmp/rg-map-identity-20261020-d --mtls --memory-consumer
uv run python tools/quality/check_recovery_bundle_processes.py \
  --from /tmp/rg-content-reclamation-20261020-h --output /tmp/rg-recovery-20261020-h \
  --old-node /tmp/rg-pre-v13-node-20261020
```

目录是该次实际运行已存在的证据位置；重新运行使用新输出目录，不覆盖原记录。

| 门禁 | 实际结果 |
| --- | --- |
| 文件链路 | 2 software Nodes、2 real CPU jobs、2 independent verifiers、1 original import |
| 原内容回收 | 1 tombstone、1 actual unlink、1 final SQL failure、1 original explicit reconciliation |
| Memory 重启 | 2 Node restart、3 backend restart，原 receiver budget 不变 |
| Spatial | 1 native CPU worker、2 software Nodes、2 independent verifier processes、完整原 map/current-use/active-map/query/restart |
| 恢复 | 6 declared stores、14 files、72 full SQL tables，2 Completed attempts 与原 deadline 保留 |
| 恢复 CAS | mTLS 实际读取及独立 SHA-256 与原 manifest 一致 |
| 旧 writer | 实际 v12 Node 拒绝 v13；完整 logical SQL oracle 不变 |
| 活跃 writer | 实际新 Node 持有 lease 时，backup 在任何 partial 创建前拒绝 |
| 证据等级 | devices=0、models=0、simulators=0；无设备停止、物理身份或连续性结论 |

代码、binary、原配置与 checker identity 分别在每个 `source-identity.json`/`result.json` 中
保留。实际归档旧 Node SHA-256 为
`8106c920723ba936e34bd3cd8b3b44833a73382e34a5fc0e7506e1850fa96fa9`；当前 Node 为
`b414b3cabab99982938449e5ca170debb32906d950f60b15ffa765c6865f9147`。
完整原失败保留于 `/tmp/rg-recovery-20261020-a/`：第一次检查缺少原证书，未到 storage
门禁；后续 H 使用原证书，明确验证实际版本拒绝，没有把配置失败记为门禁通过。
恢复 manifest digest 为
`sha256:07f80a50048b1158ddbfd9829b5cf16ad7acf42e99b0073a4992fe84acffc997`。

地图 C 的原失败保留于 `/tmp/rg-map-identity-20261020-c/`：原 revoke ACK 未全部到达时，
Controller 正确返回 `applied=false`，旧 checker 却要求即时应用。D 改为只读观察同一原
evidence，完整核对 payload、原接纳时间和期限未变后，记录后续真实 `applied=true`；不
重交 verifier，不延长期限，不把最初 pending response 改写成成功。

## 质量检查与剩余边界

Rust workspace 当前 872 passed、2 原有 ignored；Node Service 133 passed。
本地 provider 47 项和恢复工具 15 项覆盖正常、拒绝、并发 owner、真实 WAL、损坏、实际
SQL 回滚及最终 receipt 不确定性。独立工作树环境的完整 Python 检查为 2603 passed、
1 skipped；随后新增的 revoke-before-first-grant 回归包含在上述 47 项 provider 检查中。
Clippy workspace/all-targets、Rustdoc/private-items 均以 warnings denied 通过；Ruff、
严格 mypy、函数文档和 MkDocs strict 通过。原共享环境曾错误加载另一工作树的 Mission
v0.2，导致 7 项失败，记录保留；隔离 Python 环境后完整检查通过，未改动另一工作树。

完整 F4/F5 尚须 Controller/global CAS 的 tombstone/reference 协调、在线 replica/content
恢复、archive 完整 replay/compaction 合同以及跨主机部署验证。恢复工具只证明所声明的
stores，不自动证明整个部署没有遗漏；原绝对 authority 路径不被改写。F6–F10 按原完整
整改计划继续实施，任何这些未关闭软件项都阻止 SoftwareComplete。
