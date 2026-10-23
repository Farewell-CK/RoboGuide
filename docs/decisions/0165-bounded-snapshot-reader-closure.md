# ADR-0165：Snapshot 原 reader 的有界后台关闭

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0160](0160-original-content-producer-journal-and-interruption-reconciliation.md)、[ADR-0163](0163-bounded-replica-transport-admission.md)

## 决策

实际单 async worker、原 CAS/Reader/SQLite 与独立 Python TCP client 反例中，Snapshot
Drop 同步写 reader close receipt。原 SQL 连接持有 exclusive lock 时，Drop 阻塞约一秒，
独立客户端超时。失败仍正确保留原 Unknown/pin，但网络 worker 不应承担这段 SQL。

仅对实际 Tokio 上下文中的 Snapshot handle 释放，将其唯一原 reader scope 移入同一
Content root 的后台关闭队列。普通同步 scope 与无 Tokio 的同步调用保留原同步行为。
队列持有原 store/SQL writer 租约及完整 immutable ReaderIntent，不复制或重开 scope。
一个 root 同时只调度一个实际 blocking drain task；原 reader rows 上限 4096 同时限定
待关闭原 readers 总数。空 scope 不排队；重复原 ID 仍不能产生新 reader。

只有 worker 实际发布原 close receipt 才释放对应原 pin；排队、Drop 返回、等待取消、
队列排空或超时都不是 closure proof。receipt 仍为 retention-only、零 payload bytes，
`complete=false`；实际原进程/kernel、原 issue/duration/request digest 不变。关闭可以
晚于原 read deadline，但绝不续读预算。数据库失败、调度失败、worker panic 或关闭丢失
保留原 Unknown/责任，不在 Drop 同步重试，也不构造另一 operation 补成功。

实际 worker/queued jobs 保留原租约，取消等待不结束其作用域。drain guard 仅负责本代
队列的内存生命周期，旧 guard 不取消新 worker；丢失 jobs 只保留 durable Unknown。
Tokio 正常 shutdown 等待实际 blocking tasks；进程 kill/中断仍由原 process-absence
合同处理，不能伪造原 managed close receipt。

只读 cleanup observation 与有界 async wait 仅表示本进程队列状态/实际返回错误，不授予
内容、GC、quorum、Node 或执行权限。调用者需独立核对原 SQL reader/receipt/pin。
现有测试与工具在实际要求释放时等待原 worker，再检查其原行，不假设 Drop 返回即完成。
不改变 SQL/Root/Node/wrapper schema，也不启用生产 online content profile。

## 验收

保留旧真实 TCP/SQL contention 反例，验证新单 worker 网络仍响应；原 receipt 实际完成
后才退休 pin。覆盖持锁/SQL 失败、原 deadline、多个独立 readers、队列原上限、取消/
shutdown 和 actual Root3 Snapshot/Archive/cold process gates。测试零设备/模型/模拟器，
不把软件 reader 关闭、进程退出或短窗口 TCP 延迟作为设备停止、物理连续性或 SLO。
同一 voters 内容 ACK、旧 child/升级、路由与完整 N1–N7 继续必需。
