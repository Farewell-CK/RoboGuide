# 原文件进程退出与 Task 接收身份验收

设计：[ADR-0124](../../decisions/0124-original-file-computation-process-exit.md)、
[ADR-0125](../../decisions/0125-task-owned-memory-import-preparation.md)。
本项实现原 Memory/交接的软件路径；设备、模型和模拟器均零。

## 实际修复

Local EAIOS 文件 backend 增加显式进程模式。原 invocation、kernel budget、instance
先入库，PID 持久化后才允许读取；每次读取和每轮均检查当前使用权、取消和最早原期限。
暂停后的子进程拒绝旧排队许可。独立 OS wait 入库后才产生 safe-state v0.3；Cancelled、
Cancel 接纳和线程消失不替代退出证据。父进程死亡后缺少原 wait 的任务保持 Unknown；
独立检查器的观察没有写成生产证明，不确定 reader 继续阻止 GC 和安全确认。

后续 Task 的 preparation 曾按 Node/provider/manifest 复用任意 Completed import。
实际负例 B 的后续 use request 携带前一 Task 的 `execution_id`/`preparation_id`，backend
正确拒绝并保持 hold。当前按原 preparation/selector/provider 固定新 import ID，消费
已有 intent 前比较完整原 request。旧 import/预算/Unknown 保留；backend 的 owner、
scope、当前 use 和 bytes 校验不变。两份实际 imports 与各自 Controller preparation
一一对应，独立文件复算后才满足 Mission。

真实旧 FileJobs 代码被只读 `jobs` view 拒绝；当前线程模式也不能打开新进程库。原线程
结果迁移不产生 PID。40 个真实子进程的观察轮转保持在 60KiB 内，未用合成终态制造证明。
PID SQL 提交失败、实际 SQL 等待耗尽预算、暂停后的旧许可也有检查。

## 实际进程记录

独立 parent-death 检查运行三个实际子进程：自然完成、父进程被 SIGKILL 后独立回收原
子进程，以及新父进程的实际取消。重启时旧子进程仍被 SIGSTOP 暂停，新 backend 取得
原 writer lease，证明该子进程没有持有 writer 锁。旧 PID/预算/instance 不变，仍为
Unknown；独立回收结果没有成为生产 OS wait。新进程实际取消退出码 -15，自然完成 0。
本检查没有创建 Node 或设备。

双 Node 正式配置使用实际 mTLS/CAS、原 import/probe/use、Node generation 和 safe-state
v0.3，完成 source 原子进程停止/revoke、immutable child、目标两次计算和独立 verifier。
后续 Task 的 import/preparation/attempt 各自独立；控制面重启不重复计算或续预算。
expiry、policy-change、controller-restart 分别保持预期 manual/ownership fence，目标计算零。

两个 SQL rollback 窗口测量独立 SQL 故障：先 SIGSTOP 两个实际 Node 发布进程并确认
事实队列排空，移除 trigger 后通过实际 generation-bound recovery API 恢复，再继续
协议。暂停计入原期限，没有注入事实或重写 session。组合断连负例 A/B/C 保留：A 暴露
旧 checker 未用显式 write recovery；B 暴露 owner 复用；C 在重连后按原 session 拒绝
stop receipt 并保持 manual。独立 SQL 通过不代表跨 session 停止恢复已完成。

```sh
.venv/bin/python tools/quality/check_file_process_workers.py \
  --output /tmp/rg-file-process-crash-20261020-d \
  --old-file-jobs /tmp/rg-before-file-process-jobs-20261020.py
.venv/bin/python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-file-process-handoff-20261020-f \
  --handoff-case complete --process-workers
```

| 记录 | SHA-256 |
| --- | --- |
| parent-death RESULT D | `b151ffc9eae6d6957c6d79424ff0a0f7767526ae8bde4af6b3858f97edaa0a8c` |
| 完整交接 RESULT F | `7ddfa057279be9d554a9ca21a364af75d325339cb9f1a77a792c6112428d0dbc` |
| 单 worker 内容/恢复/告警 RESULT B | `e187bd39e908d8ad52a68c5a4390abf008c5d5086047ce38f73c2c40e92dabd2` |
| expiry RESULT | `2a4fdda074fdd1edcc49c2cc88a7abbdde66308658bdfadadacf79a42ae44d46` |
| policy-change RESULT | `5de0663c4b11b95a1ceaeadb66e0c1bbdd8c90785d871f95c6fce5451766969d` |
| controller-restart RESULT | `9d1b98e05671a6277b5d070ddd81fd32960e7501522afc55e86a199504777762` |
| 实际 Controller | `69016c73d5888bdac6efff9905d0c4b38f6331c7c401bfa28908778d26c9f895` |
| 真实旧 FileJobs 源码 | `88f23551ce5a5f160f48883c29039972981b937000135d8a13096486b203ce7b` |

单 worker 的正式文件链路同时通过原 import SQL 中断对账、真实内容回收、两次 authority
SQL 故障/显式恢复、两次实际 Prometheus/Alertmanager firing/resolved 和 Node/backend
重启。目录 `/tmp/rg-file-process-memory-complete-20261020-b` 保留完整配置与原 receipts；
该短窗口不宣称长期 SLO 达标。不同记录各自冻结的 checker identity 以原 RESULT 为准，
产品 Controller/Node/backend 的实际 identity 可独立核对。

## 验证与边界

919 项 Rust 通过、2 项保留 ignored，Clippy/private Rustdoc/fmt 通过。全量 Python
2717 项通过、1 项保留 skipped；Memory 的 55 项检查通过。主组 265 文件和独立 Memory
16 文件 strict mypy 通过，Ruff/format/函数文档通过。

默认线程 profile 保持旧语义；v0.3 进程证明需要显式协商。观察延迟与保留量和 poll
interval 有关，不建立硬实时或设备停止保证。完整跨主机/在线恢复、全局引用/GC、scoped
Memory 计划修订和 F5–F10 按总计划继续执行；本项不关闭总体 SoftwareComplete。
