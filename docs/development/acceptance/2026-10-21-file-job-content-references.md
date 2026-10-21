# 本地 FileJobs CAS 执行引用验收

本地 FileJobs admission 将 exact imported CAS selector 写入 provider v3
`content_references`，owner 绑定 execution digest，generation 绑定原执行。Tombstone 只按目标 CAS
digest 检查执行引用；同 provider 上无关 CAS 不因别的任务占用而阻塞。

- 普通线程任务在终态结果提交、输入文件关闭后才释放 execution projection。
- 进程任务须等原 PID 被实际 wait、exit receipt 提交后释放。revoke/cancel 到达时，原子引用仍
  挡住 tombstone，直到子进程退出。
- 重启恢复根据原 invocation 重建 `RUNNING/UNKNOWN` 输入引用；重复命令返回原 Unknown，不重新
  执行。进程 exit 缺失时引用保留；原 wait proof 已保存时允许清理 stale projection。
- external export 路径不属于 provider CAS，不会登记为这里的 execution 引用；并不由该
  ContentReclamation 路由删除。
- Public `/v1/content/references` 拒绝保留的 `file-job:` owner，不能伪造或提前释放 job 引用。

对应回归位于 `integrations/memory-provider/tests/test_use_leases.py`、
`test_jobs.py` 与 `test_process_jobs.py`。覆盖真实文件、SQLite、线程与原 POSIX 子进程，未创建设备、
Node、模型或模拟器。该本地写入链不闭合跨主机 Replica、Archive、Controller Catalog、全局
引用对账或物理安全；F4/F5 继续开放。SQLite trigger 注入覆盖 ref/job admission 失败的清理和
原 process-exit receipt 失败时保留输入 GC fence。全量 Python 回归、Memory provider 专项、严格
mypy/Ruff/function-doc、真实 provider restart gate 与 MkDocs strict 均通过。实际进程 gate 证据
位于 `/tmp/rg-memory-execution-references-20261021-a`，其 Node/device/model/simulator 计数均为 0。
参见 [ADR-0144](../../decisions/0144-file-job-content-references.md)。
