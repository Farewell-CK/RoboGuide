# Controller 写入禁用与原权威恢复验收

日期：2026-10-20。实施基线 `main@5762150282257a210a3b0ad953532f7972a39e6a`。
对应 [ADR-0114](../../decisions/0114-write-disabled-controller-recovery.md)、
[封闭合同](../../../contracts/recovery/authority-v0.1/README.md)和
[恢复指南](../controller-write-recovery-v0.1.md)。

## 实际调用链与结果

独立 PKI/HTTPS → 实际 Controller → 两正式注册的软件 Node → 独立 SQL 故障注入 →
write-disabled/原提交查询与 Node unavailable → 原全局主体/process/generation/checkpoint/
journal revision 恢复 → 同一 Controller 原任务 → 真实文件/CAS/两个独立 verifier →
Memory 原 revoke/回收与重启对账。

保存与 Commit 两次故障都保留原进程、第一原因/代际、完整 committed SQL/源时间和
所有权。原两 Node 的实际日志记录 application unavailable；没有把消息接收算作 ACK。
上传先真实写入一个 byte，禁用后下一 byte 在原 writer 下拒绝并撤除 staging；私有 CAS
读取因缺少可用权威/审计拒绝。readiness 为 503，metrics write-enabled=0，HTTP 查询
继续返回原提交 projection。Inventory 的 query observed_at 可以推进，Node 原 source/
receive/liveness 时间逐项保持不变。

错误 process/generation/checkpoint/revision、非 recovery-admin、故障仍在和同键异参均
拒绝。每个恢复只增加一条零事件 journal probe；原 checkpoint、event tail 保持不变。
旧成功 receipt 能查询，但不能解除下一代故障。恢复后两个原 hash jobs 与独立复算完成，
原 import/receipt/预算保持；完整链路末尾无遗留 owned process。

## 软件边界

库级 7 项针对真实 SQLite 的检查覆盖 full/incremental 存储、实际 save/append/Commit
故障、SQL/cache/零事件 journal drift、open transaction、旧进程/下一代 receipt 和复制
负向端口。生产 HTTP handler 检查 viewer/operator/tenant admin 拒绝及全局原主体成功。
复制不确定性与 catalog fence 必须 startup replay；当前 API 不将本地 probe 冒充远端
接受证明。设备数、模型数、simulator 数均为 0，不得推出设备停止或物理连续性。

## 复现与质量记录

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-authority-recovery-20261020-d --authority-recovery --content-reclamation
```

D 的 `result.json`、`source-identity.json`、两份 `authority-*-recovered.json` 和
`owned-process-exits.json` 保留原命令、binary/config/checker identity、独立 SQL oracle、
实际两 Node unavailable 与整个后续文件链路结果。重现须使用新输出目录。

原 A/B 失败记录保留：A checker 请求了未提供的全局 Mission list；B 将 inventory query
time 误当成 source time，要求整个 JSON 不变。修正查询目标并逐项核对原 Node 时间，
C 与最后源码 D 均通过；没有修改 source receive time 或放宽故障期间 SQL 不变的要求。

D 的实际 Controller SHA-256 为
`3c8d456b52e842905278ad850d28519a63772a59ffb38ccab140ea3a36df1f68`。
其后 `/tmp/rg-authority-cold-recovery-20261020-a/` 对同一原数据完成 6 stores、72 full SQL
tables 与实际 HTTPS CAS 的冷恢复，包含两份原 recovery command；manifest digest 为
`sha256:2cb4528785063a70cb6f1e0e91790120ef6e6d20d713bb8e1c372a8a20e746ce`。
同源码的 `/tmp/rg-authority-replica-20261020-a/` 通过三实际 replica 的 mTLS、少数派拒绝、
Leader death、旧 epoch 路由关闭和多数派丢失/恢复回归。该 HA checker 的本地工作使用
明确的协议 fixture，仅证明复制/隔离兼容；正向业务与内容证据来自上述真实文件链路。

Rust workspace 880 passed、2 原有 ignored、27 suites；其中 State 89 与 Controller 247
项通过。此结果不关闭 F10 完整告警/SLO、写锁内 I/O 收敛或原计划 F4–F9 未交付项。
Rust fmt、workspace/all-target Clippy、private-items Rustdoc 的 warnings-denied 检查通过；
改动 Python 的 Ruff、strict mypy、函数文档与 MkDocs strict 通过。完整 Python 业务代码
未改变，本工作包没有以重跑实验或模型效果替代验收。
