# 2026-10-23 原进程检查器 SQL 生命周期

分支 `codex/nonrelease-remediation-closure`，前置 `5eb651e`。这是实际检查工具的连接
关闭修复；不修改 Controller SQL 原 writer/排空门禁、格式、权限或 Node 合同。

[7c3219a 原 Rust job annotation](https://github.com/Farewell-CK/RoboGuide/actions/runs/39882422452/job/120182672131)
指出 `Verify original child lineage through stopped Group handoff` 失败。本地相同命令
在 `/tmp/rg-p7-handoff-20261023` 重现：真实 CPU 工作、独立 verifier 和交接已经完成，
Controller 重启报 `Controller original SQLite connections are not quiescent: database is locked`。
GitHub API 仍有 TLS timeout，未取回远端步骤内部原日志；本地完整失败已保留。

原 helper 使用 `with sqlite3.connect(...)`，该上下文结束负责提交/回滚，不关闭连接。
实际 Connection/statement cache 的循环回收不能作为源 SQL 已静止的凭据。修复维护的
23 个 quality modules、71 处拥有连接的作用域：`closing(connection)` 管理实际关闭，
同时保留原 Connection 的事务上下文，正常提交、异常回滚在关闭前完成。未改故障 SQL、
停机等待、原 operation identity、预算、权限或任何断言要求。

证据根目录 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`：

| 证据 | 实际结果 |
| --- | --- |
| `checker-sql-lifetimes-native/result.json` | 两个真实 Python child 均 wait 0；禁用循环 GC，旧 checkpoint helper 读 12 次残留 25 个 SQL FDs，新 helper 读 12 次残留 0；原 checkpoint digest 相同 |
| `checker-lifetimes-handoff-failure/` | 原实际失败完整保留，未改写为成功 |
| `checker-lifetimes-handoff-archive/result.json` | 同一 CI 命令重跑通过；2 个实际软件 Nodes、3 次独立 hash 工作、2 个实际 SQL rollback 窗口、原 revoke/stop/交接/新 import、独立目标满足与原 Controller 重启 |
| `checker-lifetimes-revision-archive/result.json` | 实际 `revision-mi` 原 Request/provider SQL/独立 Review、CPU 进程失败与等待、Memory/import/use、原目标验证和重启通过；Responses endpoint 为显式协议 fixture，外部模型为 0 |

原 `/tmp` 运行目录继续保留，归档带 regular-file 摘要清单和未复制的 socket 清单。
归档仅保留证据，原绝对路径不因复制而变成新的运行权威。所有软件门禁均为设备/模型/
模拟器 0；两软件 Node 的配置、文件与真实进程不能升级为硬件连续性或停止事实。

全部修改的 quality modules Ruff/format、函数文档及原组合严格 mypy 355 files 通过。
Rust 产品代码未改变，沿用前置全库 1028 通过/2 忽略、Clippy 与私有 Rustdoc；没有用
重新跑无关算法测试替代实际失败步骤。原端口/SQL/幂等/预算和完整 P1–P7 要求继续保留，
下一项继续 supervisor 原 SQL inode / 完整投影接管与在线内容、多数 ACK/路由。
