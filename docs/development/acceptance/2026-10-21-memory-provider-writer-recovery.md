# Memory provider writer 与完整恢复验收

状态：当前参考实现的软件门禁通过；全局 F4、在线 F5 和其余非发布整改继续。
源基线 `d964bc8`，权威变更见 [ADR-0127](../../decisions/0127-memory-provider-complete-writer-leases.md)。
未运行实验、外部模型、模拟器或设备，未注册虚构的设备能力。

## 修复与版本

实际旧 writer 反例保留于 `/tmp/rg-provider-writer-audit-20261021-a/RESULT.json`：
硬链接别名能再次打开相同 instance；主 Store 关闭后，use 连接存活而根 writer 已释放。
现在四 SQL inode、根锁和 use 锁由全部实际 connection 共同持有，最后关闭才释放。
启动以 SQLite 自身的 journal 排空拒绝仍存活的 legacy 连接；v3 marker 先提交到原主文件，
旧 v2 writer 随后拒绝。原 instance、表、预算与 Unknown 历史保持。

全量 Python 2780 项通过，使用实际 Habitat Python 3.9 兼容门禁，零 skip；严格 mypy
274 个主检查文件和 17 个 Memory 源码/测试文件、Ruff/format、函数文档和 MkDocs strict
通过。实际 recovery v0.4 profile 另经闭合 JSON Schema 验证；Rust 未改动，fmt 检查通过。

显式 recovery v0.4 取得相同实际 writer、验证原 marker，并要求完整四 SQL 与内容。
恢复不启动服务，空 lease 文件不授予执行或物理停止权。Node journal v13、共享 Controller
wrapper v45/inner v23 和原 Node Protocol 合同不变。

## 实际进程结果

| 原结果 | SHA-256 | 验证内容 |
| --- | --- | --- |
| `/tmp/rg-provider-writer-processes-20261021-c/RESULT.json` | `432638bbf3a6afb95d5bc548ada7250b12e36e6fffd3fb076d6c4e87e3f46a13` | 旧实际连接、硬链接、主关闭后的各子连接、旧 writer/工具拒绝；4 SQL/19 tables/实际内容和原 UUID 恢复 |
| `/tmp/rg-provider-lease-scoped-memory-20261021-b/result.json` | `296f83d01f4bf38c15d700f037baf898dae00c7f20045b2d88f45fae309677f9` | 实际双软件 Node，原 PID wait/撤权、child、两个新 Task 独立 import/use、两次独立复算和原目标证据 |
| `/tmp/rg-provider-lease-memory-complete-20261021-c/result.json` | `da350f20e4d71679baf8a474e7b5fdf26fc1e2fee47e9fd72a29728d119c1244` | 单网络 worker、两次实际 SQL 故障/恢复、2 CPU jobs、tombstone/unlink 对账、原预算重启、实际 2 firing/2 resolved |
| `/tmp/rg-provider-lease-complete-recovery-20261021-a/result.json` | `68712179d7f4e28c783628862595c234a0934a526acca0a854f37fd556149dd9` | 6 components、11 SQL、78 tables；逐表 schema/rows 与原 CAS 相同，旧 Node/Controller 和活动 writer 拒绝，真实服务恢复后读取原 catalog/内容/tombstone |

所有 owned 进程已回收；源码、工具、配置与实际 binary digest 保存在各自 identity 文件。
Controller `87e51b689be71f5cd7f70caa78179f8ddf9930fc57801dddf647999f25c7abe0`，
Node `6128db7f0f2519bbeed14fcc69491cb13b7b3c037679bfe170a1b10d5ce2d73d`。
归档旧源码实际由 `d964bc8` 读取，未通过改写 SQLite 伪造旧实现。

首轮组合失败保留在 `rg-provider-lease-memory-complete-20261021-a`：工具使用 SQLite
事务 context 后未关闭原连接，新排空检查正确拒绝重启。现工具用 `closing` 真正关闭它。
首轮 writer checker 的 identity 变量被 actor receipt 覆盖，仅在最终自检失败，证据保留；
修正工具后重跑，未放松产品 writer。中间结果不替代上表的最终固定源码结果。

## 复现与边界

```sh
.venv/bin/python tools/quality/check_provider_writer_processes.py \
  --output /tmp/unique-writer-check --old-provider-source /absolute/archived-provider-v2 \
  --old-recovery-root /absolute/archived-recovery-v03
.venv/bin/python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/unique-complete-memory --process-workers --async-workers 1 \
  --authority-recovery --content-reclamation --monitoring-tools /absolute/verified-monitoring-tools
.venv/bin/python tools/quality/check_recovery_bundle_processes.py \
  --from /tmp/unique-complete-memory --output /tmp/unique-complete-recovery \
  --old-node /absolute/archived-pre-v13-node --old-controller /absolute/archived-pre-v45-controller
```

writer 专项创建零 Node；两条 Memory 工作流使用两台实际软件 Node 进程，设备数仍为零。
scoped 修订仅 source Node 执行，不把 consumer 记成执行者。完整恢复包含两个空 active-map
库和 source 的未启用 use 库，明确保存“不具备该权威”的原状态；专项使用实际 map ledger，
但两者都不证明地图被机器人使用。Imported 历史在 tombstone 后仍不能证明当前可用。
短告警门禁不证明长期 SLO，存储冷恢复不证明在线复制、全局引用或物理执行连续性。
