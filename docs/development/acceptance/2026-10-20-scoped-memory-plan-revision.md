# 原属组 Memory 计划修订验收

[ADR-0126](../../decisions/0126-scoped-memory-for-runtime-plan-revision.md)接通原先拒绝的
generic Group-scoped Memory 修订。实际 administrator 在候选输出/claim/Review 前冻结
原属组、完整 child、membership、策略和原预算；审核后的计划直接引用该 child。
完整原停止/撤权和真实 CAS 核验后，在原事务发布 child 并由 Control 创建新组。
新 Task 各自准备、导入、探测和取得当前使用许可；原数据 owner/provenance 保留。

本工作以 `main@3af9853808a53bc1c1ae2359878f1046d7cd36df` 为基线，当前共享 wrapper
v45；Bridge v23 / event codec v28 / Node journal v13 / Contract v0.12 / config v0.13 /
catalog v0.15 保持原义。完整原 v44 数据和 MI nonce/预算可迁移，旧 writer 拒绝 v45。
补齐 v43/v44 的 journal-head 识别，旧 replica v1 codec 不能丢弃其零事件 command 权威。

## 实际进程证据

Controller binary SHA-256：
`87e51b689be71f5cd7f70caa78179f8ddf9930fc57801dddf647999f25c7abe0`；
Node：`6128db7f0f2519bbeed14fcc69491cb13b7b3c037679bfe170a1b10d5ce2d73d`。
每个检查独立生成实际证书、注册两个软件 Node；执行来自原 Node 的真实文件计算，另一个
Node 保持无作业。没有 PhysicalEntity/device 注册、模型或模拟器。

| 实际结果目录（均含 `result.json`） | 核验 | 结果 SHA-256 |
| --- | --- | --- |
| `/tmp/rg-scoped-memory-complete-accepted-20261020-a` | 原 PID/独立 OS wait、原 revoke、child CAS、新 Group、两次独立复算/Task verifier/原目标、旧 writer 拒绝、完整重启 | `65adbcd4db6ddfed134533ec4fe6d8149a0e63192c87fa0437e94132837077aa` |
| `/tmp/rg-scoped-memory-sql-accepted-20261020-a` | child publication SQL trigger 回滚；原版本/资源/事件未变，实际数据库没有新计算；显式 writer 恢复后完整完成 | `79ac75f00aaaedbcafbaf58a0f8337814d648ab09b90d7e6f3d6b7dd9bd2e1d5` |
| `/tmp/rg-scoped-memory-restart-accepted-20261020-a` | 原停止确认后、publication 前重启；原许可/预算/停止证明逐字段一致，继续正式切换及两次验证 | `ac6a19915653655ca4e58a79ed9dbfc3e3a4a0ff03f93ac4a9720d8602fa340f` |
| `/tmp/rg-scoped-memory-policy-accepted-20261020-a` | 原策略变更/child 撤销拒绝切换；原所有权保留，实际两端 jobs 表新增数为 0 | `72b0abf3d2b1d2483c01a9858c2c5e7724bb047b322382ae292461a57fca8153` |
| `/tmp/rg-scoped-memory-expiry-accepted-20261020-a` | 原 candidate/声明预算到期拒绝；不刷新期限，实际两端 jobs 表新增数为 0 | `e54e05c3b98b0bb8b03a685a995e62220ddb09bd64f1190a6a5e96a3badac2ed` |

正向三个配置各有两个实际新计算，其计数独立查询 source/consumer SQLite；两个负向配置
计数均为零。每个结果都保留独立源码/配置摘要和 owned-process 回收记录。新接收方两次
import、preparation、execution 身份均不同；从实际原 invocation 查询后与真正完成的
attempt 集合比对。两个单独 verifier 进程重新读取原文件和 backend 导入文件并复算；
Task 满足后还需独立原 Mission goal producer。提议方不能提交该目标证明。

声明本身不发布 metadata、不产生 domain event，也不创建目标 Group。实际旧二进制
`/tmp/rg-before-revision-memory-controller-20261020`（v44，SHA-256
`69016c73d5888bdac6efff9905d0c4b38f6331c7c401bfa28908778d26c9f895`）拒绝新库，
拒绝后完整原 checkpoint 未变。单测另覆盖零事件声明、原 receipt 重放、篡改 owner/
provenance/scope/visibility、旧 envelope 和伪造旧 wrapper。

## MI 与机器合同组合

同一 Controller/Node binary 的原 MI 完整回归结果为
`/tmp/rg-revision-memory-mi-regression-20261020-a/RESULT.json`，SHA-256
`7f24793e76afa9c40752a22ffe0054a3de780e1cc5b8d667f6c702a88087d72f`。
实际 Request Service、mTLS、自动发现、原 SQL claim/nonce、两次 HTTP provider 协议 fixture、
回包丢失只读对账、旧 SQL copy 拒绝、独立 Review/原目标和四个实际 CPU 子进程通过。
原 v44 admission/accepted_at/receipt 迁移到 v45，零 Node 迁移不生成 goal 权限。
外部模型调用为零，协议 fixture 不作为模型有效性或物理事实。

`check_revision_memory_schemas.py` 用本地封闭 v0.1/v0.2/v0.3 和 MissionPlan v0.9
校验实际 retained commands/candidate，拒绝旧协议增加 Memory 权限、global child、Spatial
alias 和调用者 stop 布尔值。schema 结果不授予实际权限。
Task verifier 取 `/tenant` 的内部归一化 digest；revision/goal 用发布计划 digest，
实际互换拒绝记录也保留。未更改 verifier 原接纳规则。

## 复现与边界

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/new-owned-directory --handoff-case revision --process-workers \
  --old-controller /path/to/actual-v44-controller
uv run python tools/quality/check_revision_memory_schemas.py \
  --from /tmp/new-owned-directory
```

故障配置为 `revision-sql`、`revision-restart`、`revision-policy`、`revision-expiry`。
SQL 配置只暂停 owned publishers 并排空事实，移除 trigger 后实际 generation-bound 恢复；
不可写期间 Artifact 拒绝当前权限读取，恢复后再确认 child 不存在。所有端口和 ID 来自
本次实际软件配置，Core 没有固定虚拟设备或特殊测试设备路径。

早期反例保留在 `/tmp/rg-scoped-memory-revision-20261020-{a,b,c}` 与
`/tmp/rg-scoped-memory-revision-sql-20261020-a`：检查器曾误取 digest；新协议 restore
漏接 envelope、catalog 初始化顺序及 SQL 不可写响应分别修正。各结果记录自己的原代码
身份，不覆盖失败或将缺失证据改成成功。

全量 Python 2,769 passed / 0 skipped，Rust workspace 923 passed / 2 explicit ignored；
Clippy/private Rustdoc 的 `-D warnings`、271 files strict mypy、格式/函数文档、机器合同与
严格文档站检查通过。普通 MI 回归与非空 scoped
Memory 切换分别通过；实际 MI provider 携带非空声明的整链组合仍需独立验收。
typed-map 完整来源、全局引用/GC/恢复、F0/F5–F10 与总体 SoftwareComplete 仍继续。
软件进程退出不证明设备停止，未作硬件、物理连续性、跨主机或长期 SLO 声明。
