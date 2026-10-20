# ADR-0124：文件计算的原操作进程与独立退出证据

日期：2026-10-20。状态：Accepted，实施中。

Memory 文件参考 backend 当前用后台线程计算；旧 safe-state 是取消后线程不再活动的观察，
不能作为 ADR-0110 safe-state v0.3 的原 OS process exit。为 scoped Memory 修订与执行协调
增加显式 `--worker-processes` 配置，Local EAIOS 仍拥有计算和本地使用权限，不改变 Core
设备类型、Mission 语义或 Node 选择。未配置时保留旧线程合同。

每个实际 job 先持久化原 invocation、kernel/time namespace/不可续计算预算和 process instance，
再创建一个独立 owned Python 子进程。父进程在 PID 持久化后才允许计算；每次最多 1MiB
读取和每轮计算前均经父进程当前 use-lease/取消/原预算许可。子进程只持有已验证只读输入
描述符及控制管道，不继承数据库/writer lease；父控制管道关闭即停止，不自动重跑。
每次读取许可携带同一 kernel/time namespace 的绝对期限，取原计算/use budget 与 100ms
窗口的最早者。子进程恢复调度后也必须检查期限，不能恢复使用旧排队许可。

只有原 `Popen.poll()/wait()` 的实际 OS 回收结果入库后才成为独立 process-exit 证据。
safe-state v0.3 连接原显式 generation、Group、instance 和实际 terminal outcome；Cancel
接纳、终态行或线程消失都不能替代 wait 结果。自然 Completed/Failed 可报告真实退出而不
捏造 Cancel receipt。启动/SQL/进程中断保留 Unknown，重启不续预算或补造退出证明。

新 file-job storage marker 与只读旧 `jobs` view 拒绝历史二进制 writer；旧结果迁移保留
原始 bytes/预算，缺 OS proof 的历史记录不获得新 authority。旧线程模式不能重开该库。
父进程重启后，缺独立 wait 证明的原 reader 继续阻止内容回收并报告未证实；独立测试
进程的退出观察不能填补生产 journal。观察页在 60KiB 内轮转，完整历史保留在 SQL；
部署需按保留量、观察间隔和 stop deadline 设置预算，不把分页当作硬实时保证。
Node 必须明确使用现有 config v0.13/Contract v0.12 的 generation 与 safety 元数据协商，
旧输入不能隐式取得 v0.3 进程证明。实际文件、OS 子进程、权限撤销、取消、崩溃/重启和
旧 writer 拒绝需验收。软件进程退出永远不证明设备停止或物理交接。
