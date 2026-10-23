# ADR-0172：Runnable 原 Controller 内容 Profile

- 状态：Accepted，本包已实现并验收，完整内容接管继续实施
- 日期：2026-10-23
- 前置：[ADR-0171](0171-owned-controller-content-ingress.md)

## 部署与生命周期

新增显式 process profile v0.2，只允许 network v0.4、实际已创建的 Root4、parent8、
真实 mTLS、private Unix endpoint 和显式 child 操作预算。原 process v0.1 保持旧含义，
不能隐藏新 content 配置或隐式打开 parent8。每成员实际 Root 必须匹配 self/profile/
committed owner，bootstrap owners 不覆盖已提交 member data。新合同和 child budget
进入同构 application policy digest；每成员 path/route 不改变同构语义。

原 supervisor 在新的 child 前继续执行原 durable spawn/PID/boot/start-tick 和真实
wait；原未 reap/spawning 历史继续阻止替换。只在实际当前完整内容 quorum 与完整原
恢复投影核对后 materialize 原 inactive Controller SQL，原 epoch 再复核才 spawn。
子进程只接收闭合内容配置，不同时继承 legacy application fence。实际 source SDK
writer 和新 client 必须是同一个 Store/lease，Artifact HTTP Prepared guard 经共享
registry 进入原 State 的完整 recovery/content commit，不开第二个 source writer。

新 child 在原预算内可进行有界的只读 registration 等待，等待不产生写入、读取 payload
或执行许可；原实际 PID journal 注册及真实 current fence 成功后才进入应用写路径。
回包丢失只查询原命令和完整原候选，不重新生成 copy/Commit。

## 检查职责

显式 `ProcessFence` 仅核对原实际 PID/TLS、epoch/transfer fence 和当前 infrastructure
quorum，供原进程 watchdog 与事务前检查使用，不声称内容 ready。完整 `Fence` 保留
原 current metadata+完整 bytes quorum，供显式内容就绪查询使用。提交仍必须沿原
complete candidate/bytes/metadata/full quorum 的 ACK，不能由 ProcessFence 替代。
实际使用和下载还须原 ACL/scope/use 与实际 bytes 检查；这些权限不能由进程存活推断。
显式 network parent 的 continuity/transfer 查询沿原 inode 下的已提交 WAL snapshot，
不等待 application mutex；原 mutation writer 仍做实际提交复核。Supervisor 状态
读取与文件发布由单一串行 owned blocking scope 执行，取消等待不宣告实际工作已结束。
Raft 的原日志读取、vote/committed 指针、truncate/purge 和 applied-state SQL 也须
移出网络 executor。显式 producer profile 使用独立最多 16 个实际 running/queued
consensus I/O scopes；原 heavy producer 的八槽保持。每个实际 scope 持有原 Node/
SQL/Root leases 与 queue 前时钟，取消 async 等待不能提前释放它们或补造完成。
新 profile 显式声明 quorum observation window（100 ms–30 秒，不能超过 child 预算），
并进入 application policy digest。在同一原窗口内可重复实际只读 quorum query，始终
检查原 epoch，成功必须来自新的 actual linearizable proof；无 quorum 到期拒绝。
这不重发应用命令、不延长原效果预算，也不改旧 profile 的单次两秒查询或 Raft 的
原 heartbeat/选举参数。实际固定版本 Raft 在刷盘期间可能无法及时回复一次短探针，
单次失败不能在新 profile 中偷偷替代部署声明的完整观察窗口。

新 source/接管后的公开内容路由、原 Prepared 上传的恢复及完整 readiness 属于明确
后续接线。在这些合同完成前，profile v0.2 不能宣告完整 HA 或 SoftwareComplete，
也不能将 receiving Root 的相同 hash 变成新的 source owner/公开 use grant。

## 验收

用真实 `controller-replica` 和 `integration-server`、真实 TLS/SQL/CAS、零 embodied
Node 完成原启动、HTTP 上传、独立 SQLite/字节核对及原 source process wait。核对
原预算、原候选、原 Unknown 和完整 State 投影；旧 profile 启动/格式拒绝独立回归。
本包实际原生结果及完整质量检查见
[验收记录](../development/acceptance/2026-10-23-runnable-controller-content.md)。
后续继续完整 routing/Prepared takeover/readiness、joint/catchup/Snapshot/升级/
回退/recovery/GC/业务目标矩阵。硬件、物理停止与跨主机连续性仍要求实际证据。
