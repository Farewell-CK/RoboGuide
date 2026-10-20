# 运行期 MI writer 与原调用联合恢复验收

设计：[ADR-0122](../../decisions/0122-runtime-mi-recovery-writer-closure.md)；
[Recovery profile v0.3](../../../contracts/recovery/bundle-v0.3/README.md)。
本项补齐 ADR-0121 新 SQL worker 的实际冻结/恢复边界，不关闭在线 HA、自动发现、scoped
Memory 迁移或其余 F0/F4–F10 必需项。

## 实际核验

以[已完成的 MI 进程验收](2026-10-20-runtime-mission-intelligence.md)原目录 F 为输入，
先校对原 RESULT 与实际 SQL evidence hash。启动实际生产 `RuntimeWorkStore` 持有原
`runtime-mi.sqlite3.worker.lock` 和数据库 inode；独立 CLI 被拒绝。再以硬链接别名和
一个真实存在但无关的 sibling 锁尝试，仍被原 inode lease 拒绝。持锁进程已实际回收。

全部源服务静止后，独立 recovery CLI 在同一 profile 冻结 Controller、Node journal、
Mission Request、runtime MI 和其原 Artifact writer。四个 SQL 合计 46 张表、1,015,808
字节；独立 SQLite oracle 比较每个 schema/table/row，原 nonce、kernel、预算、原 bytes、
uncertain outcome 和独立 observation 逐项相同。源 Artifact 目录为空，本项没有宣称
恢复非空 CAS；实际非空三副本内容恢复另见[原验收](2026-10-20-replica-cold-recovery.md)。

真实旧 recovery 代码副本拒绝新 profile。新目录 restore receipt v0.3 的相同重试只读
核验现有结果。过程中没有启动 Node、Controller、模型或设备服务；唯一新增进程仅持有
实际 MI store writer。数据恢复不等于服务可自动接管，不把原 Unknown 改成成功。

```sh
.venv/bin/python tools/quality/check_runtime_mi_recovery_processes.py \
  --from /tmp/rg-runtime-mi-generation-20261020-f \
  --output /tmp/rg-runtime-mi-complete-recovery-20261020-a \
  --old-recovery-root /tmp/rg-before-runtime-mi-recovery-tool-20261020
```

| 证据 | SHA-256 |
| --- | --- |
| RESULT | `600defca922296a540a6c47f73a02f78f9e9c6a105faf543f7b20b94b1faadbb` |
| Manifest | `03bf1ab1c76ee1c8611fcecec68e4044cb65333bc404ee21eae3e261c525c0f2` |
| 原 MI 进程结果 | `f77d1657aed656c49a4c210ce039840ee21c4094895c2781d7afdb84998dc044` |
| 实际 checker | `7b5eacd52b06f4adabb8f18ebfc22c59d2c68eb53cdf872a4e68361ad21f5aef` |

## 回归与限制

22 个恢复测试通过：真实 SQL/nonce/原回包复用、实际双 writer、未知 marker/旧 profile
拒绝，及 v0.3 保留 v0.2 非空 CAS replica placement/只读重试。激活服务后的 journal mode
可能合法改变 SQL 文件 bytes；此后旧 restore 重试必须拒绝覆盖，不能伪装成未激活副本。

全量 Python pytest 退出 0；255 个源文件的 strict mypy、Ruff/format、函数文档检查、
实际 profile 的 JSON Schema 校验和严格文档站构建通过。本包没有修改 Rust。

当前是声明数据的静止恢复。代码/配置/证书/时间域的主动迁移、在线联合 snapshot/archive/
catalog/CAS 与完整跨域恢复继续执行；软件数据完整不证明物理停止或硬件连续性。
