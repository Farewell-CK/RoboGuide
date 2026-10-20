# 已授权运行期 MI 自动发现验收

设计：[ADR-0123](../../decisions/0123-runtime-mi-authorized-work-discovery.md)；
[只读合同](../../../contracts/mission/runtime-plan-work-v0.1/README.md)。

实际 worker v0.2 配置没有任何静态 Request ID。真实故障、原停止/撤销完成后，从实际 mTLS
Controller 的 Generating 队列发现 Mission；Mission HTTP/SQLite 用原唯一索引与 tenant
定位真实 Request。其他稳定主体队列为空，未委托 tenant 返回 403，伪造 principal query
返回 400。实际 TLS Mission 回归核对本 tenant 成功、其他 tenant/不存在均 404，伪造 tenant
返回 403，读取不触发原模型/提交。分页只提供线索，后续权限变化仍经原命令门禁拒绝。

实际计算链使用原 CPU/file 进程、Node、Controller 和独立文件复算。四个原 worker 退出码
`[-15, 7, 0, -15]`；四个 MI worker 退出码 `[-15, 1, 1, 0]`。原 provider SQL 回包提交后
终止；恢复实际较早 SQL 副本的另一 nonce 被拒绝且没有新 HTTP 调用；恢复完整原副本后
候选实际接纳、回包丢失，下一进程通过独立只读 observation 继续 Review。原 uncertain
outcome、预算及 claim 保留，最终新 Group 文件计算和独立原目标 gate 通过。

provider 明确为本地 Responses 协议 fixture，共两次实际 HTTP，外部模型/设备/模拟器均零。
软件计算结果不证明模型有效性、设备停止或物理连续性。原 v43 → v44 迁移、旧 writer
拒绝、普通 resume fence、取消/重启检查复用完整原验收路径并通过。

```sh
.venv/bin/python tools/quality/check_plan_revision_processes.py \
  --output /tmp/rg-runtime-mi-discovery-20261020-a --skip-build \
  --old-controller /tmp/rg-before-automatic-generation-controller-20261020 \
  --automatic-generation
```

| 原证据 | SHA-256 |
| --- | --- |
| RESULT | `b49f23d3d80e6df88cbcd4bc24cc582d6d888169f15595389e1681c9dbbb3dcf` |
| 实际 Controller | `d6690adf9d4fc73ee8c5e8b6438c4878a60ffaaf44c25fd6b3fe60a531e401ba` |
| 实际 MI code | `6b9e4b67e82a29c121e8f82ceb4ab0d5d59f6caaa3f0d00a5d2b9c828b82154e` |
| 原 SQL/observation 证据 | `7898d09a80b5c1a6e374b87bdcaa2b00b9de9e7e8d46b7d01bd0851bce698870` |
| 实际发现页 | `5ad91b26943590cea604c9a19c400911b785fa34f6044c40e1bd896006a0d3c9` |

同一批原数据也通过独立 recovery CLI 的实际 worker/inode writer 拒绝和完整恢复：
四个 SQL、46 张表的 schema/rows 全部一致；原不确定结果和独立 observation 保留。
`/tmp/rg-runtime-mi-discovery-recovery-20261020-a/RESULT.json` 的 SHA-256 为
`f681ce94886ea5b06477318cf86085596ec8c9d12c1ad3c898c1fb5a40f7aea3`。
本目录 Artifact 为空，不替代非空 CAS 或在线恢复验收。

919 项 Rust 通过、2 项保留 ignored；Clippy、private Rustdoc、fmt、255 文件 strict mypy、
Ruff/format、函数文档、实际发现页 JSON Schema 与严格文档站构建通过。
全量 Python 2709 项通过、1 项保留 skipped。

完整 F8 的 scoped Memory 修订仍继续实施；完整非发布整改和软件总验收尚未关闭。
