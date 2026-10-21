# 恢复包活动内容引用门禁

恢复工具在复制并独立校验 Memory SQLite 与 CAS 后，继续核对活动
`content_references`、Active Map `refs`、active `heads` 和 pending handoff 的 source/target。
每一条活动引用都必须在同一恢复包中找到精确 `stores/<provider>/content/<sha256>` 字节；活动
引用指向 durable tombstone、缺少 CAS 或 v3 引用表丢失时，backup/verify 都拒绝。已释放的历史
引用不要求旧字节，因为它们不再提供当前保留或使用权。扫描、查询和 JSON 读取保留原操作预算。
不可变 verify 同时复核原 profile 的 Memory v3 writer marker，降低 SQL version 或删除 marker
后重新计算所有摘要也不能获得恢复权威。

恢复专项覆盖有效引用的完整备份/恢复、保留 released 历史、原 CAS 丢失、活动引用与 tombstone
冲突、Active Map/head/pending-handoff 和重新固定 manifest digest 后仍缺少引用内容的反例。
这些是明确标记的 SQL/文件损坏测试，不是地图操作或设备证据。

真实进程门禁沿用 `tools/quality/check_memory_reference_processes.py`，通过实际私有 Unix
provider 导入与登记引用，完全退出原进程后调用独立 recovery CLI，恢复并启动原 provider
查询同一引用；原 CAS hash 独立读取。缺少 CAS 的恢复副本 backup 拒绝；独立损坏包即使重新固定
digest，verify/restore 仍拒绝且不发布目标。有效原包及其输入清单保持可检查。

本次实现提交为 `eab4fb485c05a45ce7ba63a6a556199389208227`；已接入当时最新 main 的
`cb48cbc` 并通过[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39624012158)。
本地全量 Python 为 3002 passed、1 skipped（本机缺少 Python 3.9；CI 配置并运行该解释器），
474 个 Python 文件格式/Ruff、304 个源文件严格 mypy、函数文档和 MkDocs strict 通过。
最终实际进程证据位于 `/tmp/rg-ref-recovery-20261021-b/result.json`，`registered_nodes`、
`device_facts`、`models`、`simulators` 均为 0；有效 backup/verify/restore 退出码为 0，
三个损坏副本拒绝命令退出码为 1，恢复后的实际 provider 查询保留相同原引用身份。

该门禁只证明单个声明 provider 的已存活动引用与本地恢复内容一致，不证明全部引用生产者、
跨主机 Replica、Archive、Controller Catalog、全局 GC、Node、设备或物理连续性。对应 ADR：
[ADR-0145](../../decisions/0145-recovery-active-content-reference-binding.md)。
