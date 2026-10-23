# ADR-0168：副本内容流的原接收责任

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0167](0167-consensus-bound-content-member-identities.md)

## 决策

完整内容数据面使用显式 content SQL v4/layout v0.4。原 root identity、原 local copy、
reader、producer/legacy journal 保持；新 stream journal 和完整 mandatory head 独立
记录接收责任。旧 open/create/升级保留原义，只有显式新 create/open/upgrade 可获得流
能力；旧二进制在零操作时也拒绝新格式。升级本身不产生内容可用或接收完成事实。

每次接收冻结完整 ContentQuorumTarget、确切 qualified input、原 source member/实际
TLS leaf/实际软件 store 或原 owned Controller instance/Artifact writer、原 receiver
root、实际进程/kernel 和不可续预算。target 的 Root map 必须与确切 committed
membership 一致；实际 transport 在原 writer 下校验权限，不能由可序列化 `ok`、PID
或 DTO 自证。后续每个有界 I/O 都重新检查原权限和预算。

接收意图与独立 receive pin 在同一 FULL SQL 事务，先于 staging 创建与载荷读取。
接收方只在实际持久意图后发送 Ready；发送方先等 Ready，再打开源载荷。Ready 不表示
有完整 bytes、quorum、Memory use、Task 或执行权限。内容使用独立有界数据流，不进入
State、Raft application/Snapshot metadata 或 Node Protocol。

单个实际 worker/stream 独占其原 staging FD、原 root/SQL leases 与原预算。块上限
64 KiB；运行及排队容量、TLS/帧/响应 I/O 也须有界。完整 size/hash、文件 fsync、确切
qualified namespace 的原子 publication 与目录 fsync 先于实际 SQL receipt。已有同
qualified bytes 也须独立验证，不能以其它 owner 的相同 hash 替代。

同 ID 的重复请求仅返回原 observation，不创建新 writer、不重复载荷或续预算。中断
保留原 Unknown、部分文件和 pin；实际 worker scope close、原 process absence、文件
publication 与原 within-budget receipt 是不同事实。取消等待不结束仍在运行的实际
worker。缺 mandatory journal/head/原来源或预算时拒绝，不默认空/Completed。

新显式 repair 可有独立 ID/预算并保留原 Unknown；不重复 Task、Execute、provider
调用或物理动作。实际源 read/receiver scope 与 log/view/Snapshot 保留各自责任；
release/GC 不从断连、队列排空、原 deadline 或一个 caller flag 推导。

## 接线与验收

先接实际 backend 的唯一接收 worker、完整 SQL/FD/预算及独立对账，再接真实 mTLS
duplex stream、原 child 的已发布/Prepared source 读能力与 Root7/full-content profile。
只有完整原 recovery projection、source origin、同一 current/joint voters 的实际
metadata+bytes ACK 和 readiness/原 child fence 接线完成，才允许应用 Commit/dispatch。
普通 ReplicaConfig 旧 profile 不获取新能力；实施中不提前宣告生产 v0.4 或 N1/N3 完成。

实际文件/SQL/owned process/TLS gates 覆盖原意图后崩溃、分块/hash/size/SQL 错误、回执
丢失、取消、期限、old writer/格式、source/target owner 与 membership/权限变化、原
Unknown 只读 retry。实际软件检查保持零设备、模型和模拟器；后续文件任务用真实
software Nodes 和独立原目标 verifier，不制造硬件 inventory 或物理连续性事实。
